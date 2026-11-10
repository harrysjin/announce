"""Private learning management. Review authority never comes from a client field."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import learning_db as db
from .learning_auth import POLICY_VERSION, admin, document_group, ensure_identity, require_identity, same_origin
from .learning_events import encode
from .runtime import SlidingWindowLimiter

router = APIRouter(prefix="/api/learning", tags=["learning"])
LIMIT = SlidingWindowLimiter(120, 60)
ReviewStatus = Literal["pending", "approved", "rejected", "needs_review"]
CaseStatus = Literal["selected", "pending_review", "approved", "rejected", "needs_review"]


def check_rate(request: Request):
    decision = LIMIT.check(request.client.host if request.client else "unknown")
    if not decision.allowed:
        raise HTTPException(429, "학습 관리 요청이 많습니다. 잠시 후 다시 시도해 주세요.", headers={"Retry-After": str(decision.retry_after)})


@router.get("/status")
def status(request: Request, response: Response):
    subject = ensure_identity(request, response)
    if not db.configured():
        return {"available": False, "consent": False, "policy_version": POLICY_VERSION,
                "recommendations_enabled": False, "auto_confirmation_enabled": False}
    with db.connection() as conn:
        row = conn.execute("SELECT enabled,policy_version,updated_at FROM learning_consents WHERE subject_id=%s", (subject,)).fetchone()
    return {"available": True, "consent": bool(row and row["enabled"] and row["policy_version"] == POLICY_VERSION),
            "policy_version": POLICY_VERSION, "consent_record": row,
            "recommendations_enabled": False, "auto_confirmation_enabled": False}


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    policy_version: str


@router.put("/consent")
def consent(request: Request, body: Consent):
    same_origin(request)
    subject = require_identity(request)
    if body.policy_version != POLICY_VERSION:
        raise HTTPException(409, "변경된 동의 안내를 확인해 주세요.")
    if body.enabled:
        document_group("configuration-check")
    with db.connection() as conn:
        conn.execute("""INSERT INTO learning_consents(subject_id,enabled,policy_version,granted_at,withdrawn_at)
                      VALUES (%s,%s,%s,CASE WHEN %s THEN now() END,CASE WHEN NOT %s THEN now() END)
                      ON CONFLICT(subject_id) DO UPDATE SET enabled=excluded.enabled, policy_version=excluded.policy_version,
                      granted_at=CASE WHEN excluded.enabled THEN now() ELSE learning_consents.granted_at END,
                      withdrawn_at=CASE WHEN NOT excluded.enabled THEN now() ELSE NULL END,updated_at=now()""",
                     (subject, body.enabled, POLICY_VERSION, body.enabled, body.enabled))
    return {"consent": body.enabled, "policy_version": POLICY_VERSION}


@router.delete("/records")
def delete_records(request: Request):
    same_origin(request)
    subject = require_identity(request)
    with db.connection() as conn:
        conn.execute("SELECT subject_id FROM learning_consents WHERE subject_id=%s FOR UPDATE", (subject,))
        conn.execute("DELETE FROM learning_consents WHERE subject_id=%s", (subject,))
    return {"deleted": True, "consent": False}


@router.get("/admin")
def admin_status(request: Request):
    check_rate(request)
    return {"reviewer_id": admin(request)}


def owner_lock(conn, case_id: UUID):
    row = conn.execute("SELECT subject_id FROM learning_cases WHERE case_id=%s", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "학습 사례를 찾을 수 없습니다.")
    consent = conn.execute("SELECT * FROM learning_consents WHERE subject_id=%s FOR UPDATE", (row["subject_id"],)).fetchone()
    if not consent or not consent["enabled"]:
        raise HTTPException(409, "기록 제공 동의가 철회된 사례입니다.")
    case = conn.execute("SELECT * FROM learning_cases WHERE case_id=%s FOR UPDATE", (case_id,)).fetchone()
    if not case:
        raise HTTPException(404, "학습 사례를 찾을 수 없습니다.")
    return case


@router.get("/cases")
def cases(request: Request, scope: Literal["mine", "review"] = "mine", q: str = Query(default="", max_length=120),
          state: CaseStatus | None = None, limit: int = Query(default=40, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
    check_rate(request)
    subject = require_identity(request) if scope == "mine" else None
    if scope == "review":
        admin(request)
    clauses, params = [], []
    if subject:
        clauses.append("c.subject_id=%s")
        params.append(subject)
    else:
        clauses.extend(["cons.enabled", "cons.policy_version=%s", "c.active"])
        params.append(POLICY_VERSION)
    if state:
        clauses.append("c.status=%s")
        params.append(state)
    if q:
        clauses.append("(strpos(c.surface,%s)>0 OR strpos(c.lemma,%s)>0 OR strpos(c.context,%s)>0)")
        params.extend([q] * 3)
    where = " AND ".join(clauses) or "true"
    with db.connection() as conn:
        rows = conn.execute(f"SELECT c.* FROM learning_cases c JOIN learning_consents cons USING(subject_id) WHERE {where} ORDER BY c.created_at DESC,c.case_id LIMIT %s OFFSET %s",
                            (*params, limit + 1, offset)).fetchall()
        shown = rows[:limit]
        reviews = conn.execute("SELECT * FROM learning_reviews WHERE case_id=ANY(%s::uuid[]) ORDER BY reviewed_at,review_id", ([r["case_id"] for r in shown],)).fetchall() if shown else []
    grouped = defaultdict(list)
    for review in reviews:
        grouped[review["case_id"]].append(review)
    return {"items": [{**r, "reviews": grouped[r["case_id"]]} for r in shown], "has_more": len(rows) > limit, "next_offset": offset + len(shown)}


@router.get("/events")
def events(request: Request, limit: int = Query(default=40, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
    subject = require_identity(request)
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM learning_events WHERE subject_id=%s ORDER BY recorded_at DESC,event_id LIMIT %s OFFSET %s", (subject, limit + 1, offset)).fetchall()
    return {"items": rows[:limit], "has_more": len(rows) > limit, "next_offset": offset + min(limit, len(rows))}


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


@router.post("/cases/{case_id}/submit")
def submit(case_id: UUID, request: Request, body: Submission):
    same_origin(request)
    subject = require_identity(request)
    from psycopg.types.json import Jsonb
    with db.connection() as conn:
        # Reject other owners before any status disclosure.
        own = conn.execute("SELECT 1 FROM learning_cases WHERE case_id=%s AND subject_id=%s", (case_id, subject)).fetchone()
        if not own:
            raise HTTPException(404, "본인의 학습 사례를 찾을 수 없습니다.")
        case = owner_lock(conn, case_id)
        if not case["active"] or case["status"] not in ("selected", "needs_review") or case["review_version"] != body.expected_version:
            raise HTTPException(409, "현재 상태에서는 검수를 요청할 수 없습니다. 목록을 새로 확인해 주세요.")
        conn.execute("UPDATE learning_cases SET status='pending_review',review_version=review_version+1 WHERE case_id=%s", (case_id,))
        conn.execute("INSERT INTO learning_reviews(review_id,case_id,reviewer_id,reason,before_status,after_status) VALUES (%s,%s,%s,%s,%s,%s)",
                     (uuid4(), case_id, "owner-submission", "본인 검수 요청", Jsonb({"status": case["status"]}), Jsonb({"status": "pending_review"})))
    return {"status": "pending_review"}


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meaning_status: ReviewStatus
    length_status: ReviewStatus
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)


@router.post("/cases/{case_id}/review")
def review(case_id: UUID, request: Request, body: Review):
    check_rate(request)
    reviewer = admin(request)
    from psycopg.types.json import Jsonb
    if not body.reason.strip():
        raise HTTPException(422, "검수 근거를 작성해 주세요.")
    with db.connection() as conn:
        case = owner_lock(conn, case_id)
        if not case["active"] or case["review_version"] != body.expected_version:
            raise HTTPException(409, "사례가 수정되었거나 이미 검수되었습니다. 목록을 새로 확인해 주세요.")
        if body.meaning_status == "approved" and not case["meaning_supported"]:
            raise HTTPException(422, "확인 가능한 사전 의미 또는 숫자 읽기 선택 근거가 없습니다.")
        if body.length_status == "approved" and not case["length_supported"]:
            raise HTTPException(422, "공식 사전 발음·적용 규칙과 원문 표시 위치가 확인되지 않아 장음을 승인할 수 없습니다.")
        new_status = ("rejected" if "rejected" in (body.meaning_status, body.length_status)
                      else "approved" if body.meaning_status == body.length_status == "approved"
                      else "needs_review")
        before = {k: case[k] for k in ("status", "meaning_status", "length_status")}
        after = {"status": new_status, "meaning_status": body.meaning_status, "length_status": body.length_status}
        conn.execute("UPDATE learning_cases SET status=%s,meaning_status=%s,length_status=%s,review_version=review_version+1 WHERE case_id=%s",
                     (new_status, body.meaning_status, body.length_status, case_id))
        conn.execute("INSERT INTO learning_reviews(review_id,case_id,reviewer_id,reason,before_status,after_status) VALUES (%s,%s,%s,%s,%s,%s)",
                     (uuid4(), case_id, reviewer, body.reason.strip(), Jsonb(before), Jsonb(after)))
    return after


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 10
    return "train" if bucket < 8 else "validation" if bucket == 8 else "test"


@router.get("/export.jsonl")
def export_jsonl(request: Request, scope: Literal["mine", "approved"] = "mine",
                 after: UUID | None = None, limit: int = Query(default=200, ge=1, le=500)):
    check_rate(request)
    subject = require_identity(request) if scope == "mine" else None
    if scope == "approved":
        admin(request)
    with db.connection() as conn:
        # One bounded consistent snapshot per page. Clients must follow X-Next-Cursor.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if scope == "mine":
            rows = conn.execute("SELECT * FROM learning_events WHERE subject_id=%s AND (%s::uuid IS NULL OR event_id>%s::uuid) ORDER BY event_id LIMIT %s",
                                (subject, after, after, limit + 1)).fetchall()
            shown = rows[:limit]
            case_rows = conn.execute("SELECT * FROM learning_cases WHERE event_id=ANY(%s::uuid[])", ([r["event_id"] for r in shown],)).fetchall() if shown else []
            review_rows = conn.execute("SELECT * FROM learning_reviews WHERE case_id=ANY(%s::uuid[])", ([r["case_id"] for r in case_rows],)).fetchall() if case_rows else []
            reviews_by_case, cases_by_event = defaultdict(list), defaultdict(list)
            for row in review_rows:
                reviews_by_case[row["case_id"]].append(row)
            for row in case_rows:
                cases_by_event[row["event_id"]].append({**row, "reviews": reviews_by_case[row["case_id"]]})
            records = [{"schema_version": 1, "record_type": "event", **r, "cases": cases_by_event[r["event_id"]],
                        "dataset_split": split_for(r["document_group"])} for r in shown]
            cursor_field = "event_id"
        else:
            rows = conn.execute("""SELECT c.* FROM learning_cases c JOIN learning_consents cons USING(subject_id)
                                  WHERE c.active AND c.status='approved' AND cons.enabled AND cons.policy_version=%s
                                  AND (%s::uuid IS NULL OR c.case_id>%s::uuid) ORDER BY c.case_id LIMIT %s""",
                                (POLICY_VERSION, after, after, limit + 1)).fetchall()
            shown = rows[:limit]
            records = [{"schema_version": 1, "record_type": "approved_case", **{k: v for k, v in r.items() if k not in ("subject_id", "job_id")},
                        "dataset_split": split_for(r["document_group"]), "label_source": "human_review_with_dictionary_evidence"} for r in shown]
            cursor_field = "case_id"
    next_cursor = str(shown[-1][cursor_field]) if len(rows) > limit else ""
    return Response("".join(encode(r) + "\n" for r in records), media_type="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="sori-learning.jsonl"', "X-Next-Cursor": next_cursor,
                             "X-Export-Complete": str(not next_cursor).lower()})
