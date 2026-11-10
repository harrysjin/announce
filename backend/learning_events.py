"""Append-only learning events and transactional bridges to ephemeral PDF edits."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import threading
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from . import learning_db as db
from .learning_auth import POLICY_VERSION, document_group, require_identity, same_origin
from .release import ENGINE_VERSION, release_identity


class MutationMeta(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    idempotency_key: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    target_event_id: UUID | None = None


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def official(candidate: dict) -> bool:
    try:
        queried = datetime.fromisoformat(candidate.get("queried_at", ""))
        return (urlparse(candidate.get("url", "")).hostname == "stdict.korean.go.kr"
                and str(candidate.get("target_code", "")).isdigit()
                and str(candidate.get("sense_code", ""))
                and candidate.get("id") == f"{candidate['target_code']}:{candidate['sense_code']}"
                and queried.tzinfo is not None)
    except (ValueError, TypeError, KeyError):
        return False


def short_context(job: dict, occurrence: dict) -> str:
    # Center on the actual glyph span, including repeated words on the same line.
    page = job["pages"][occurrence["page"] - 1]
    first = next((g for g in page["glyphs"] if g["id"] == occurrence["glyph_ids"][0]), None)
    line = [g for g in page["glyphs"] if first and g.get("line") == first.get("line")]
    offset = 0
    for glyph in line:
        if glyph["id"] == first["id"]:
            break
        offset += len(glyph["text"])
    text = "".join(g["text"] for g in line) or occurrence.get("context", "")
    if not line:
        offset = max(0, text.find(occurrence["text"]))
    return text[max(0, offset - 40):offset + min(80, len(occurrence["text"])) + 40][:160]


def unresolved_reason(occurrence: dict) -> str:
    code = occurrence.get("reason_code", "not_analyzed")
    if code in ("deadline", "timeout", "lookup_timeout"):
        return "lookup_timeout"
    if code == "lookup_failed":
        return occurrence.get("lookup_failure_kind") or "lookup_failed"
    if code == "not_found":
        return "not_in_dictionary"
    if code in ("ambiguous", "numeric_ambiguous"):
        return "meaning_ambiguous"
    if code in ("inflection_unverified", "partial_evidence", "numeric_unverified"):
        return "pronunciation_evidence_missing"
    return "resolved" if occurrence.get("status") != "unresolved" else code


CANDIDATE_FIELDS = ("id", "target_code", "sense_code", "word", "display_word", "origin", "hanja", "pos", "lookup_provenance",
                    "pronunciations", "pronunciation", "definition", "inflections", "url", "queried_at")
STATE_FIELDS = ("status", "source", "meaning_status", "selected_candidate_id", "numeric", "reason", "reason_code",
                "evidence_type", "evidence_candidates", "pronunciations", "length_evidence", "applied_rules",
                "coverage", "pronunciation_scope", "lookup_failure_kind")


def snapshot(job: dict, occurrence: dict) -> dict:
    candidates = {c["id"]: c for c in occurrence.get("all_candidates", []) + occurrence.get("candidates", [])}
    for component in occurrence.get("component_results", []):
        candidates.update({c["id"]: c for c in component.get("candidates", [])})
    records = [{k: deepcopy(c[k]) for k in CANDIDATE_FIELDS if k in c} for c in candidates.values()]
    glyph_ids = occurrence["glyph_ids"]
    marked = job.get("_learning_mark_index")
    if marked is None:
        marked = {m["glyph_id"]: m for m in job["marks"]}
    expected_glyphs = set(occurrence.get("long_glyph_ids", []))
    actual = [i for i, gid in enumerate(glyph_ids) if gid in marked]
    expected = [i for i, gid in enumerate(glyph_ids) if gid in expected_glyphs]
    selected = next((c for c in records if c["id"] == occurrence.get("selected_candidate_id")), None)
    numeric = occurrence.get("numeric") or {}
    option = next((o for o in numeric.get("options", []) if o["id"] == numeric.get("selected_option_id")), None)
    ids = occurrence.get("evidence_candidates", [])
    support = [c for c in records if c["id"] in ids]
    rules = occurrence.get("applied_rules", [])
    meaning_supported = bool((selected and official(selected)) or option)
    length_supported = bool(occurrence.get("coverage", {}).get("complete")
                            and occurrence.get("evidence_type") and occurrence.get("length_evidence")
                            and occurrence.get("pronunciations") and occurrence.get("status") in ("long", "none")
                            and support and all(official(c) for c in support)
                            and actual == expected
                            and ("rule" not in occurrence["evidence_type"] or rules)
                            and all(urlparse(r.get("url", "")).hostname == "korean.go.kr"
                                    or (urlparse(r.get("url", "")).hostname or "").endswith(".korean.go.kr") for r in rules))
    return {"surface": occurrence["text"], "lemma": occurrence.get("lemma", occurrence["text"]),
            "pos": (selected or {}).get("pos") or occurrence.get("morphology", {}).get("pos", ""),
            "context": short_context(job, occurrence), "state": {**{k: deepcopy(occurrence[k]) for k in STATE_FIELDS if k in occurrence},
                      "morphology": {k: deepcopy(v) for k, v in occurrence.get("morphology", {}).items()
                                     if k not in ("trailing_context", "following_tokens")}},
            "candidates": records, "marked_character_indices": actual, "engine_character_indices": expected,
            "eligible_candidate_ids": [c["id"] for c in occurrence.get("candidates", [])],
            "excluded_candidate_ids": occurrence.get("excluded_candidates", []),
            "component_evidence": [{k: deepcopy(v) for k, v in component.items() if k != "candidates"}
                                   for component in occurrence.get("component_results", [])],
            "analysis_error_code": job.get("analysis", {}).get("error_code"),
            "meaning_supported": meaning_supported, "length_supported": length_supported,
            "unresolved_reason": unresolved_reason(occurrence),
            "dictionary_provenance": {"provider": "국립국어원 표준국어대사전", "api": "https://stdict.korean.go.kr/api/view.do",
                                      "edition": None, "edition_note": "API 응답에 판본 번호 없음; 후보 조회 시각과 내용 해시로 스냅샷 식별",
                                      "adapter_version": ENGINE_VERSION,
                                      "snapshot_sha256": hashlib.sha256(encode(records).encode()).hexdigest()}}


def semantic_signature(occurrence: dict, marks: list[dict], include_status: bool = True) -> str:
    # Geometry, color and free-form notes cannot become pronunciation labels.
    return encode({"candidate": occurrence.get("selected_candidate_id"),
                   "reading": (occurrence.get("numeric") or {}).get("selected_option_id"),
                   "status": occurrence.get("status") if include_status else None,
                   "marks": sorted(m["glyph_id"] for m in marks if m["glyph_id"] in occurrence["glyph_ids"])})


def persist(conn, subject: str, job: dict, before: dict, meta: MutationMeta, request_hash: str,
            event_type: str, target: str | None, payload: dict, compensation: dict | None = None) -> dict:
    from psycopg.types.json import Jsonb
    old_marks = {m["glyph_id"]: m for m in before["marks"]}
    new_marks = {m["glyph_id"]: m for m in job["marks"]}
    before_job = {**job, **before, "_learning_mark_index": old_marks}
    after_job = {**job, "_learning_mark_index": new_marks}
    old_occurrences = {o["id"]: o for o in before["occurrences"]}
    requested = {o.get("id"): o for o in payload.get("occurrences") or []}
    compensation_targets = {c["occurrence_id"] for c in compensation["changes"]} if compensation is not None else None
    changes = []
    for occurrence in job["occurrences"]:
        if compensation_targets is not None and occurrence["id"] not in compensation_targets:
            continue
        old = old_occurrences[occurrence["id"]]
        explicit_status = requested.get(occurrence["id"], {}).get("status", old.get("status")) != old.get("status")
        include_status = event_type != "manual_edit" or explicit_status
        old_local = [old_marks[gid] for gid in old["glyph_ids"] if gid in old_marks]
        new_local = [new_marks[gid] for gid in occurrence["glyph_ids"] if gid in new_marks]
        changed = semantic_signature(old, old_local, include_status) != semantic_signature(occurrence, new_local, include_status)
        if changed or (target == occurrence["id"] and event_type in ("meaning_selected", "reading_selected")):
            changes.append({"occurrence_id": occurrence["id"], "before": snapshot(before_job, old), "after": snapshot(after_job, occurrence)})
    if event_type == "manual_edit" and not changes:
        event_type = "visual_edit" if before["settings"] != job["settings"] or before["marks"] != job["marks"] else "note_edit"
    group = document_group(job["sha256"])
    # Geometry is logged separately by character index; never store the page or full extraction.
    visual = {"settings_before": before["settings"], "settings_after": job["settings"], "marks": []}
    for gid in old_marks.keys() | new_marks.keys():
        a, b = old_marks.get(gid), new_marks.get(gid)
        if a != b:
            visual["marks"].append({"glyph_id": gid, "before": {k: a.get(k) for k in ("dx", "dy", "scale")} if a else None,
                                     "after": {k: b.get(k) for k in ("dx", "dy", "scale")} if b else None})
    conn.execute("""INSERT INTO learning_events(event_id,subject_id,idempotency_key,request_hash,job_id,document_group,event_type,
                  compensates_event_id,engine_version,build_commit,before_revision,after_revision,changes,visual_changes,consent_version)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (meta.event_id, subject, meta.idempotency_key, request_hash, job["id"], group, event_type,
                  meta.target_event_id, ENGINE_VERSION, release_identity()["build_commit"], before["revision"], job["revision"],
                  Jsonb(changes), Jsonb(visual), POLICY_VERSION))
    if changes:
        conn.execute("UPDATE learning_cases SET active=false WHERE subject_id=%s AND job_id=%s AND occurrence_id=ANY(%s) AND active",
                     (subject, job["id"], [c["occurrence_id"] for c in changes]))
    rows = []
    for change in changes:
        after = change["after"]
        rows.append((uuid4(), meta.event_id, subject, job["id"], change["occurrence_id"], group, after["surface"], after["lemma"],
                     after["pos"], after["context"], Jsonb(after), after["unresolved_reason"], after["meaning_supported"], after["length_supported"]))
    if rows:
        with conn.cursor() as cursor:
            cursor.executemany("""INSERT INTO learning_cases(case_id,event_id,subject_id,job_id,occurrence_id,document_group,surface,lemma,pos,context,
                               snapshot,unresolved_reason,meaning_supported,length_supported) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    return {"state": "saved", "event_id": str(meta.event_id), "replayed": False}


def validate_compensation(job: dict, before: dict, original: dict) -> None:
    def linguistic(record):
        state = record["state"]
        return (state.get("selected_candidate_id"), (state.get("numeric") or {}).get("selected_option_id"),
                state.get("status"), record["marked_character_indices"])
    old_job = {**job, **before}
    old = {o["id"]: o for o in before["occurrences"]}
    new = {o["id"]: o for o in job["occurrences"]}
    targets = {c["occurrence_id"] for c in original["changes"]}
    for change in original["changes"]:
        oid = change["occurrence_id"]
        if (linguistic(snapshot(old_job, old[oid])) != linguistic(change["after"])
                or linguistic(snapshot(job, new[oid])) != linguistic(change["before"])):
            raise HTTPException(422, "취소 대상 사건의 실제 변경 전후 상태와 일치하지 않습니다.")
    for oid in old.keys() - targets:
        # Visual-only edits may change the old engine's manual status, but not marks or reading choices.
        if semantic_signature(old[oid], before["marks"], False) != semantic_signature(new[oid], job["marks"], False):
            raise HTTPException(422, "취소 대상에 없는 언어적 수정을 함께 복원할 수 없습니다.")
    visual = original["visual_changes"]
    if before["settings"] != visual["settings_after"] or job["settings"] != visual["settings_before"]:
        raise HTTPException(422, "취소 대상의 표시 설정과 일치하지 않습니다.")
    def geometry(marks):
        return {m["glyph_id"]: {k: m.get(k, 1 if k == "scale" else 0) for k in ("dx", "dy", "scale")} for m in marks}
    old_marks, new_marks = geometry(before["marks"]), geometry(job["marks"])
    for change in visual["marks"]:
        gid = change["glyph_id"]
        if old_marks.get(gid) != change["after"] or new_marks.get(gid) != change["before"]:
            raise HTTPException(422, "취소 대상의 표시 위치 변경과 일치하지 않습니다.")
    allowed = {c["glyph_id"] for c in visual["marks"]}
    if any(old_marks.get(gid) != new_marks.get(gid) for gid in (old_marks.keys() | new_marks.keys()) - allowed):
        raise HTTPException(422, "취소 대상에 없는 표시 수정을 함께 복원할 수 없습니다.")


@contextmanager
def mutation(job: dict, request: Request | None, meta: MutationMeta, payload: dict,
             event_type: str, target: str | None = None):
    """Serialize edits, commit DB first, roll back every in-memory field on failure."""
    lock = job.setdefault("_edit_lock", threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "다른 수정이 진행 중입니다. 다시 시도해 주세요.")
    saved = None
    try:
        record = bool(request and request.headers.get("x-learning-record") == "true")
        if request:
            same_origin(request)
        if meta.target_event_id is not None and event_type not in ("undo", "redo"):
            raise HTTPException(422, "상쇄 대상 사건은 취소·다시 실행에만 지정할 수 있습니다.")
        if event_type in ("undo", "redo") and record and meta.target_event_id is None:
            raise HTTPException(422, "취소·다시 실행의 대상 사건 ID가 필요합니다.")
        if not record:
            if meta.expected_revision is not None and job["revision"] != meta.expected_revision:
                raise HTTPException(409, "작업이 변경되었습니다. 최신 상태를 불러와 주세요.")
            saved = {k: deepcopy(job[k]) for k in ("settings", "marks", "occurrences", "history", "revision", "warnings")}
            job["_uncommitted_snapshot"] = saved
            yield True
            job["_learning_receipt"] = {"state": "not_requested", "event_id": None}
            return
        subject = require_identity(request)
        if subject != job.get("_learning_owner"):
            raise HTTPException(403, "본인이 올린 문서의 수정만 학습 기록으로 제공할 수 있습니다.")
        if meta.idempotency_key is None or meta.expected_revision is None:
            raise HTTPException(422, "학습 기록에는 중복 방지 키와 수정 버전이 필요합니다.")
        request_hash = hashlib.sha256(encode({"job": job["id"], "type": event_type, "target": target, "payload": payload}).encode()).hexdigest()
        receipt = None
        with db.connection() as conn:
            # Serializes this user's consent withdrawal, deletion, submissions and mutations across processes.
            consent = conn.execute("SELECT * FROM learning_consents WHERE subject_id=%s FOR UPDATE", (subject,)).fetchone()
            if not consent or not consent["enabled"] or consent["policy_version"] != POLICY_VERSION:
                raise HTTPException(409, "학습 기록 제공 동의가 꺼져 있습니다. 동의 상태를 새로 확인해 주세요.")
            existing = conn.execute("SELECT event_id,request_hash FROM learning_events WHERE subject_id=%s AND idempotency_key=%s", (subject, meta.idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(409, "같은 중복 방지 키로 다른 수정을 보낼 수 없습니다.")
                receipt = {"state": "saved", "event_id": str(existing["event_id"]), "replayed": True}
                yield False
            else:
                if job["revision"] != meta.expected_revision:
                    raise HTTPException(409, "작업이 변경되었습니다. 최신 상태를 불러와 주세요.")
                if conn.execute("SELECT 1 FROM learning_events WHERE event_id=%s", (meta.event_id,)).fetchone():
                    raise HTTPException(409, "이미 사용한 사건 ID입니다.")
                original = None
                if meta.target_event_id:
                    original = conn.execute("SELECT event_type,changes,visual_changes FROM learning_events WHERE event_id=%s AND subject_id=%s AND job_id=%s",
                                            (meta.target_event_id, subject, job["id"])).fetchone()
                    if (not original or (event_type == "redo" and original["event_type"] != "undo")
                            or conn.execute("SELECT 1 FROM learning_events WHERE compensates_event_id=%s", (meta.target_event_id,)).fetchone()):
                        raise HTTPException(422, "같은 작업의 유효한 취소 대상을 지정해 주세요.")
                if job["analysis"]["state"] in ("running", "queued"):
                    raise HTTPException(409, "분석을 중단한 뒤 수정해 주세요.")
                saved = {k: deepcopy(job[k]) for k in ("settings", "marks", "occurrences", "history", "revision", "warnings")}
                saved["_numeric_results"] = deepcopy(job.get("_numeric_results", {}))
                job["_uncommitted_snapshot"] = saved
                yield True
                if original is not None:
                    validate_compensation(job, saved, original)
                receipt = persist(conn, subject, job, saved, meta, request_hash, event_type, target, payload, original)
        # Successful receipt becomes visible only after PostgreSQL commit succeeds.
        job["_learning_receipt"] = receipt
    except BaseException:
        if saved is not None:
            job.update(saved)
        raise
    finally:
        job.pop("_uncommitted_snapshot", None)
        lock.release()
