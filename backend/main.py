"""Ephemeral PDF workspace API. Run: python -m uvicorn backend.main:app."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from urllib.parse import quote

import fitz
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .dictionary import DictionaryClient, DictionaryError
from .pdf import extract, counts, marked_document, output_pdf, csv_export, verification, sample_pdf, circle_for
from .runtime import ACTIVE_STATES, AnalysisGate, SlidingWindowLimiter, progress_snapshot
from .release import VERSION, release_identity

MAX_BYTES = 20 * 1024 * 1024
TTL_SECONDS = 2 * 60 * 60
SESSIONS: dict[str, dict] = {}
TASKS: dict[str, asyncio.Task] = {}
GATE = AnalysisGate(concurrency=max(1, min(2, int(os.environ.get("ANALYSIS_CONCURRENCY", "2")))))
UPLOAD_LIMIT = SlidingWindowLimiter(30, 60)
ANALYZE_LIMIT = SlidingWindowLimiter(10, 60)
ROOT = Path(tempfile.mkdtemp(prefix="jang-eum-"))


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def cleanup() -> None:
    now = time.time()
    for job_id, session in list(SESSIONS.items()):
        if now - session["_touched"] > TTL_SECONDS:
            task = TASKS.pop(job_id, None)
            if task:
                task.cancel()
            shutil.rmtree(ROOT / job_id, ignore_errors=True)
            SESSIONS.pop(job_id, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def sweep():
        while True:
            await asyncio.sleep(60)
            cleanup()
    sweeper = asyncio.create_task(sweep())
    yield
    sweeper.cancel()
    for task in TASKS.values():
        task.cancel()
    shutil.rmtree(ROOT, ignore_errors=True)


app = FastAPI(title="장음 · PDF 낭독 작업 공간", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def limit_request(request: Request, limiter: SlidingWindowLimiter):
    client = request.client.host if request.client else "unknown"
    decision = limiter.check(client)
    if not decision.allowed:
        raise HTTPException(429, "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
                            headers={"Retry-After": str(decision.retry_after)})


def session_for(job_id: str) -> dict:
    cleanup()
    job = SESSIONS.get(job_id)
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다. 삭제되었거나 보관 시간이 지났습니다.")
    job["_touched"] = time.time()
    return job


def public(job: dict) -> dict:
    result = {key: value for key, value in job.items() if not key.startswith("_")}
    result["counts"] = counts(job)
    result["dictionary_configured"] = bool(os.environ.get("STDICT_API_KEY"))
    return deepcopy(result)


def idle_required(job: dict) -> None:
    if job["analysis"]["state"] in ACTIVE_STATES:
        raise HTTPException(409, "분석을 중단한 뒤 수정해 주세요.")


def history(job: dict, action: str):
    job["history"].append({"at": timestamp(), "action": action})
    job["history"] = job["history"][-500:]
    job["revision"] += 1


@app.get("/api/health")
def health():
    return {"ok": True, "dictionary_configured": bool(os.environ.get("STDICT_API_KEY")),
            "max_upload_bytes": MAX_BYTES, "max_pages": 100, "retention_seconds": TTL_SECONDS,
            "ocr_supported": False, **release_identity()}


@app.get("/api/sample")
def sample():
    return Response(sample_pdf(), media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=reading-sample.pdf"})


@app.post("/api/jobs", status_code=201)
async def upload(request: Request, file: UploadFile = File(...)):
    limit_request(request, UPLOAD_LIMIT)
    cleanup()
    if len(SESSIONS) >= 30:
        raise HTTPException(429, "작업 공간이 가득 찼습니다. 이전 작업을 삭제한 뒤 다시 시도해 주세요.")
    data = await file.read(MAX_BYTES + 1)
    await file.close()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "20MB 이하의 PDF를 선택해 주세요.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "PDF 형식의 파일을 선택해 주세요.")
    try:
        pages, occurrences = extract(data)
    except ValueError as error:
        messages = {"encrypted": "암호로 보호된 PDF입니다. 암호를 해제한 파일을 올려 주세요.",
                    "too_many_pages": "100쪽 이하의 PDF를 선택해 주세요.", "empty": "페이지가 없는 PDF입니다."}
        raise HTTPException(422, messages.get(str(error), "PDF를 읽을 수 없습니다. 파일이 손상되었는지 확인해 주세요.")) from None
    except (fitz.FileDataError, RuntimeError):
        raise HTTPException(422, "손상되었거나 지원하지 않는 PDF입니다.") from None
    job_id = uuid.uuid4().hex
    folder = ROOT / job_id
    folder.mkdir(mode=0o700)
    path = folder / "original.pdf"
    path.write_bytes(data)
    path.chmod(0o600)
    filename = Path((file.filename or "문서.pdf").replace("\\", "/")).name
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:180] or "문서.pdf"
    warnings = []
    for page in pages:
        if page["quality"] == "scan":
            warnings.append(f"{page['number']}쪽에 추출 가능한 글자가 없습니다. 스캔·빈 페이지는 자동 분석하지 않습니다. OCR은 아직 지원하지 않습니다.")
        elif page["quality"] == "poor":
            warnings.append(f"{page['number']}쪽의 문자·좌표 품질이 낮아 자동 표시에서 제외합니다.")
    job = {"id": job_id, "filename": filename, "sha256": hashlib.sha256(data).hexdigest(),
           "pages": pages, "occurrences": occurrences, "marks": [],
           "settings": {"color": "#CEE4F5", "opacity": 0.6, "size": 1.0},
           "analysis": {"state": "idle", "done": 0, "total": len({o['text'] for o in occurrences}), "message": "PDF를 읽었습니다. 사전 분석을 시작하거나 직접 표시해 보세요."},
           "warnings": warnings, "history": [], "revision": 0, "created_at": timestamp(),
           "_path": path, "_touched": time.time(), "_dictionary": DictionaryClient()}
    SESSIONS[job_id] = job
    return public(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return public(session_for(job_id))


@app.get("/api/jobs/{job_id}/progress")
def get_progress(job_id: str):
    return progress_snapshot(session_for(job_id), gate=GATE)


def apply_length_evidence(job: dict, occurrence: dict, evidence: dict, source: str, reason: str, reason_code: str, mark_source: str | None = None):
    """Map only original syllable IDs; partial evidence is never whole-short."""
    occurrence["long_glyph_ids"] = []
    job["marks"] = [m for m in job["marks"] if m.get("occurrence_id") != occurrence["id"] or m["source"] == "manual"]
    positions = evidence["positions"]
    page = job["pages"][occurrence["page"] - 1]
    glyph_map = {g["id"]: g for g in page["glyphs"]}
    if evidence.get("alignment_kind") == "original_characters":
        ids = occurrence["glyph_ids"]
        if (len(ids) != len(occurrence["text"]) or any(len(glyph_map[g]["text"]) != 1 for g in ids)
                or "".join(glyph_map[g]["text"] for g in ids) != occurrence["text"]
                or any(not glyph_map[ids[i]]["text"].isdigit() for i in positions if 0 <= i < len(ids))):
            occurrence.update(status="unresolved", reason="숫자 읽기와 원문 숫자의 위치가 정확히 대응하지 않습니다.", reason_code="alignment", evidence_type=None)
            return
    else:
        ids = [gid for gid in occurrence["glyph_ids"] if re.fullmatch(r"[가-힣]", glyph_map[gid]["text"])]
    if any(i < 0 or i >= len(ids) for i in positions) or not ids:
        occurrence.update(status="unresolved", reason="발음과 원문 음절 위치를 연결할 수 없습니다.", reason_code="alignment", evidence_type=None)
        return
    if page["quality"] != "text" or any(not glyph_map[g]["valid"] for g in ids):
        occurrence.update(status="unresolved", reason="문자 좌표 품질을 확인해야 합니다.", reason_code="coordinates", evidence_type=None)
        return
    coverage = evidence.get("coverage", {"complete": True, "checked_ranges": [[0, len(ids)]], "unresolved_ranges": []})
    occurrence.update(pronunciations=evidence["pronunciations"], pronunciation_scope=evidence.get("pronunciation_scope", occurrence["text"]),
                      evidence_type=evidence["evidence_type"], length_evidence=evidence.get("evidence", [evidence]),
                      applied_rules=evidence.get("applied_rules", []), coverage=coverage, source=source)
    occurrence.update(status=("long" if positions else "none") if coverage["complete"] else "unresolved",
                      reason=reason, reason_code=reason_code)
    occurrence["long_glyph_ids"] = [ids[i] for i in positions]
    existing = {m["glyph_id"] for m in job["marks"]}
    for glyph_id in occurrence["long_glyph_ids"]:
        if glyph_id not in existing:
            job["marks"].append({"id": uuid.uuid4().hex, "page": occurrence["page"], "glyph_id": glyph_id,
                                 "source": mark_source or source,
                                 "evidence_type": occurrence["evidence_type"],
                                 "occurrence_id": occurrence["id"], "dx": 0, "dy": 0, "scale": 1})
            existing.add(glyph_id)


def evidence_description(evidence: dict, *, consensus: bool, user_selected: bool) -> tuple[str, str]:
    inferred = evidence["evidence_type"].startswith("rule")
    automatic_source = "rule" if inferred else ("dictionary_consensus" if consensus else "dictionary")
    reason = ("모든 허용 후보의 장단이 일치합니다. 문맥의 뜻 선택은 미확정입니다." if consensus else evidence["explanation"])
    if inferred:
        reason += " · 사전 기본형과 원문 형태에 대한 규칙 판정 (전체 활용형 직접 발음 아님)"
    if user_selected:
        reason += " · 사용자 의미 선택"
    return automatic_source, reason


def mark_candidate(job: dict, occurrence: dict, candidate: dict | None, user_selected: bool = False, consensus_candidates: list[dict] | None = None):
    from .linguistics import form_evidence, form_consensus
    plan = occurrence.get("morphology", {})
    is_consensus = consensus_candidates is not None
    evidence = form_consensus(consensus_candidates, occurrence["text"], plan) if is_consensus else form_evidence(candidate, occurrence["text"], plan)
    occurrence["selected_candidate_id"] = None if is_consensus else candidate["id"]
    occurrence["lemma"] = plan.get("lemma", occurrence["text"]) if is_consensus else candidate["word"]
    occurrence["meaning_status"] = "unresolved" if is_consensus else ("user_selected" if user_selected else "verified")
    occurrence["evidence_candidates"] = [c["id"] for c in consensus_candidates] if is_consensus else [candidate["id"]]
    if evidence is None:
        occurrence.update(status="unresolved", source="manual" if user_selected else "dictionary",
                          reason="실제 형태의 직접 발음 또는 적용 범위가 확인된 규칙 근거가 없습니다. 기본형 발음을 무조건 복사하지 않습니다.",
                          reason_code="inflection_unverified", evidence_type=None, applied_rules=[],
                          pronunciations=[], length_evidence=[], long_glyph_ids=[], pronunciation_scope=occurrence["text"],
                          coverage={"complete": False, "checked_ranges": [], "unresolved_ranges": [[0, len(occurrence["text"])]]})
        job["marks"] = [m for m in job["marks"] if m.get("occurrence_id") != occurrence["id"] or m["source"] == "manual"]
        if not user_selected:
            occurrence.update(meaning_status="unresolved", selected_candidate_id=None)
        return
    automatic_source, reason = evidence_description(evidence, consensus=is_consensus, user_selected=user_selected)
    source = "manual" if user_selected else automatic_source
    # A user's meaning choice is manual; its generated circles are replaceable
    # rule/dictionary results until the user actually edits their geometry.
    apply_length_evidence(job, occurrence, evidence, source, reason, "length_consensus" if is_consensus else "verified", mark_source=automatic_source)


def apply_number_result(job: dict, occurrence: dict, result: dict, *, user_selected: bool = False):
    """Keep reading choice separate from dictionary and §6 length evidence."""
    numeric = deepcopy(result["numeric"])
    numeric["choice_source"] = "user" if user_selected else "automatic"
    candidates = deepcopy(result.get("candidates", []))
    occurrence.update(numeric=numeric, candidates=candidates, all_candidates=candidates,
                      excluded_candidates=[], selected_candidate_id=None,
                      lemma=result.get("pronunciation_scope") or occurrence["text"],
                      evidence_candidates=result.get("evidence_candidates", []),
                      meaning_status="user_selected" if user_selected else ("verified" if result["status"] != "unresolved" else "unresolved"))
    evidence = {**deepcopy(result), "alignment_kind": "original_characters",
                "evidence": result.get("length_evidence", []),
                "evidence_type": result.get("evidence_type"),
                "pronunciations": result.get("pronunciations", []),
                "coverage": {**result.get("coverage", {"complete": False, "checked_ranges": [], "unresolved_ranges": [[0, len(occurrence["text"])]]}),
                             "coordinate_system": "characters"}}
    automatic_source = "rule" if "rule" in (evidence["evidence_type"] or "") else "dictionary"
    reason = result["reason"] + (" · 사용자 읽기 선택" if user_selected else "")
    apply_length_evidence(job, occurrence, evidence, "manual" if user_selected else automatic_source,
                          reason, result["reason_code"], mark_source=automatic_source)
    if result["status"] == "unresolved":
        occurrence["status"] = "unresolved"


def remember_number_result(job: dict, occurrence: dict, result: dict):
    option = result["numeric"].get("selected_option_id") or ""
    job.setdefault("_numeric_results", {}).setdefault(occurrence["id"], {})[option] = deepcopy(result)


async def analyze_job(job: dict):
    from .linguistics import occurrence_plans, candidates_for_plan, form_consensus, compound_evidence
    client = job["_dictionary"]
    words = list(dict.fromkeys(o["text"] for o in job["occurrences"]))
    failures = 0
    try:
        job["analysis"].update(message="원문 문맥에서 기본형·조사·활용 범위를 분석합니다.")
        plans = await asyncio.to_thread(occurrence_plans, job)
        for index, word in enumerate(words):
            relevant = [o for o in job["occurrences"] if o["text"] == word]
            for occurrence in relevant:
                plan = plans[occurrence["id"]]
                occurrence["morphology"] = plan
                # Manual changes survive retries; dictionary discovery cannot
                # silently turn a user's edit into an automatic judgment.
                if occurrence.get("source") == "manual":
                    continue
                occurrence.update(meaning_status="unresolved", selected_candidate_id=None,
                                  evidence_type=None, evidence_candidates=[], applied_rules=[],
                                  long_glyph_ids=[], length_evidence=[], pronunciations=[],
                                  component_results=[], coverage={"complete": False, "checked_ranges": [],
                                                                 "unresolved_ranges": [[0, len(word)]]})
                job["marks"] = [m for m in job["marks"] if m.get("occurrence_id") != occurrence["id"] or m["source"] == "manual"]
                if plan["kind"] == "number" or occurrence["kind"] == "number":
                    from .numbers import number_plan, evaluate_number
                    try:
                        result = await evaluate_number(number_plan(word, occurrence.get("context", ""), morphology=plan), client)
                        remember_number_result(job, occurrence, result)
                        apply_number_result(job, occurrence, result)
                    except DictionaryError as error:
                        failures += 1
                        occurrence.update(status="unresolved", reason=str(error), reason_code="lookup_failed")
                    continue
                if plan["kind"] == "foreign" or occurrence["kind"] != "word":
                    occurrence.update(status="unresolved", reason="숫자·단위 또는 외래 표기의 실제 읽기를 확인해 주세요.", reason_code="reading_required")
                    continue
                try:
                    whole = await client.lookup(word)
                    occurrence["lemma"] = plan["lemma"]
                    found = list(whole)

                    def keep_candidates(candidates):
                        occurrence["all_candidates"] = list({c["id"]: c for c in found}.values())
                        occurrence["candidates"] = list({c["id"]: c for c in candidates}.values())
                        allowed = {c["id"] for c in occurrence["candidates"]}
                        occurrence["excluded_candidates"] = [c["id"] for c in occurrence["all_candidates"] if c["id"] not in allowed]

                    # A particle interpretation can collide with a different
                    # headword (말의). Preserve that existing abstention policy.
                    if plan["kind"] == "nominal_particle" and plan["lemma"] != word:
                        found.extend(await client.lookup(plan["lemma"]))
                        candidates = candidates_for_plan(found, plan)
                        keep_candidates(candidates)
                        collision = any(c["word"] == word for c in candidates) and any(c["word"] == plan["lemma"] for c in candidates)
                        if collision:
                            occurrence.update(status="unresolved", reason="전체 표제어와 조사 결합 해석이 겹칩니다. 문맥의 뜻과 실제 발음 근거를 확인해 주세요.", reason_code="surface_ambiguous")
                            continue

                    candidates = candidates_for_plan(whole, plan)
                    keep_candidates(candidates)
                    if plan.get("proper_context"):
                        occurrence.update(status="unresolved", reason="인명·고유명사로 분석되었습니다. 비슷한 보통명사의 장음을 옮기지 않습니다.", reason_code="proper_name")
                        continue
                    # Whole-word dictionary evidence wins, including homonym
                    # conflicts: components cannot override an unknown reading.
                    if candidates and form_consensus(candidates, word, {}) is not None:
                        if len(candidates) == 1:
                            mark_candidate(job, occurrence, candidates[0])
                        else:
                            mark_candidate(job, occurrence, None, consensus_candidates=candidates)
                        continue
                    if any(c["word"] == word for c in candidates):
                        occurrence.update(status="unresolved", reason="전체 단어의 후보별 장단이 다르거나 직접 발음 근거가 부족합니다. 구성 요소로 덮어쓰지 않습니다.", reason_code="ambiguous")
                        continue

                    if plan["lemma"] != word and not any(c["word"] == plan["lemma"] for c in found):
                        found.extend(await client.lookup(plan["lemma"]))
                    if (plan["kind"] in ("verb", "derived") and hasattr(client, "lookup_inflections")
                            and form_consensus(candidates_for_plan(found, plan), word, {}) is None):
                        found.extend(await client.lookup_inflections(word))
                    candidates = candidates_for_plan(found, plan)
                    keep_candidates(candidates)
                    if candidates:
                        if len(candidates) == 1:
                            mark_candidate(job, occurrence, candidates[0])
                        elif form_consensus(candidates, word, plan) is not None:
                            mark_candidate(job, occurrence, None, consensus_candidates=candidates)
                        else:
                            occurrence.update(status="unresolved", reason="문맥에 맞는 후보와 실제 형태의 발음 근거를 확인해야 합니다. 후보 간 장단이 다르거나 적용 근거가 없는 후보가 있습니다.", reason_code="ambiguous")
                    elif plan.get("components"):
                        results = []
                        for component in plan["components"]:
                            entries = await client.lookup(component["lemma"])
                            results.append({**component, "candidates": candidates_for_plan(entries, {"pos": component["pos"]})})
                        occurrence["component_results"] = results
                        evidence = compound_evidence(results, word)
                        occurrence["evidence_candidates"] = evidence["evidence_candidates"]
                        apply_length_evidence(job, occurrence, evidence, "rule",
                                              "구성 요소의 사전 근거와 원문 범위를 연결했습니다." + (" 전체 범위의 장단은 미확정입니다." if not evidence["coverage"]["complete"] else " 전체 단어 직접 발음과 구별되는 규칙 판정입니다."),
                                              "compound_verified" if evidence["coverage"]["complete"] else "partial_evidence")
                    else:
                        occurrence.update(status="unresolved", reason="문법·형태에 맞는 공식 표제어를 찾지 못했습니다. 단음으로 판정하지 않습니다.", reason_code="proper_name" if plan["kind"] == "proper" else "not_found")
                except DictionaryError as error:
                    failures += 1
                    occurrence.update(status="unresolved", reason=str(error), reason_code="lookup_failed")
            job["analysis"].update(done=index + 1, message=f"사전 조회 {index+1}/{len(words)} · 원문 형태와 직접 발음을 연결하고 있습니다.")
            await asyncio.sleep(0)
        unresolved = counts(job)["unresolved"]
        meaning_unresolved = sum(o.get("meaning_status") == "unresolved" for o in job["occurrences"])
        manual = counts(job)["manual"]
        consensus_count = sum(o.get("evidence_type") == "candidate_consensus" for o in job["occurrences"])
        message = f"조회 종료 · 장단 미확정 {unresolved}건 · 뜻 선택 미확정 {meaning_unresolved}건"
        if consensus_count:
            message += f" · 후보 장단 일치 {consensus_count}건(문맥 전체 검증 아님)"
        if manual:
            message += f" · 사용자 수정 {manual}건"
        if failures:
            message += f" · 사전 조회 실패 {failures}건, 다시 시도할 수 있습니다."
        job["analysis"].update(state="error" if failures else "complete", message=message,
                               meaning_unresolved=meaning_unresolved, consensus=consensus_count)
        history(job, f"사전 조회 완료 · 실패 {failures}건 · 장단 미확정 {unresolved}건 · 뜻 선택 미확정 {meaning_unresolved}건")
    except asyncio.CancelledError:
        job["analysis"].update(state="cancelled", message="분석을 중단했습니다. 확인한 결과는 유지됩니다.")
        raise
    except Exception:
        # Do not let unexpected morphology/response failures strand a running
        # task, and never emit exception URLs or uploaded text into logs.
        job["analysis"].update(state="error", message="형태·발음 근거 처리 중 오류가 발생했습니다. 확인한 결과를 유지했습니다. 다시 시도할 수 있습니다.")


@app.post("/api/jobs/{job_id}/analyze")
async def analyze(job_id: str, request: Request):
    job = session_for(job_id)
    if job["analysis"]["state"] in ACTIVE_STATES:
        return public(job)
    limit_request(request, ANALYZE_LIMIT)
    if not os.environ.get("STDICT_API_KEY"):
        job["analysis"].update(state="waiting_key", message="표준국어대사전 연결 대기 중입니다. 서버에 STDICT_API_KEY를 설정하면 사전 분석을 사용할 수 있습니다. 직접 표시와 다운로드는 가능합니다.")
        return public(job)
    if not job["occurrences"]:
        job["analysis"].update(state="error", message="분석할 텍스트가 없습니다. 스캔 PDF의 OCR은 아직 지원하지 않습니다.")
        return public(job)
    job["analysis"].update(state="queued", done=0, total=len({o['text'] for o in job['occurrences']}), message="분석 작업을 준비합니다.")
    def clear_finished_task():
        if TASKS.get(job_id) is task:
            TASKS.pop(job_id, None)
    task = asyncio.create_task(GATE.run(job, analyze_job, on_done=clear_finished_task))
    TASKS[job_id] = task
    return public(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = session_for(job_id)
    task = TASKS.pop(job_id, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    job["analysis"].update(state="cancelled", message="분석을 중단했습니다. 확인한 결과는 유지됩니다.")
    return public(job)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: str = "#CEE4F5"
    opacity: float = Field(default=0.6, ge=0.1, le=1)
    size: float = Field(default=1, ge=0.5, le=2)

    @field_validator("color")
    @classmethod
    def color_valid(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("올바른 HEX 색상을 입력해 주세요.")
        return value.upper()


class StatePatch(BaseModel):
    settings: Settings | None = None
    marks: list[dict] | None = None
    occurrences: list[dict] | None = None
    restore: bool = False


def restore_length_evidence(job: dict, original: dict, edited: dict) -> None:
    """Recompute history evidence from server-owned candidates and boundaries."""
    if original.get("numeric") is not None:
        selected = edited.get("numeric", {}).get("selected_option_id") or ""
        cached = job.get("_numeric_results", {}).get(original["id"], {}).get(selected)
        if cached is None:
            raise HTTPException(422, "서버에서 검증한 숫자 읽기만 복원할 수 있습니다.")
        apply_number_result(job, original, cached, user_selected=edited.get("numeric", {}).get("choice_source") == "user")
        if edited.get("reason_code") == "manual":
            original.update(source="manual", status=edited["status"], reason="사용자가 표시를 수정했습니다. 사전 자동 검증과 구별됩니다.", reason_code="manual")
        return
    from .linguistics import form_evidence, form_consensus, compound_evidence
    selected = next((c for c in original["candidates"] if c["id"] == edited["selected_candidate_id"]), None)
    plan = original.get("morphology", {})
    evidence = form_evidence(selected, original["text"], plan) if selected else None
    if not selected and edited.get("evidence_type") in ("candidate_consensus", "rule_consensus"):
        if set(edited.get("evidence_candidates", [])) == {c["id"] for c in original["candidates"]}:
            evidence = form_consensus(original["candidates"], original["text"], plan)
    if not selected and edited.get("evidence_type") == "rule_inference" and original.get("component_results"):
        components = deepcopy(original["component_results"])
        evidence = compound_evidence(components, original["text"])
        original["component_results"] = components
    user_selected = bool(selected and (edited.get("meaning_status") == "user_selected" or len(original["candidates"]) != 1))
    original.update(selected_candidate_id=selected["id"] if selected else None,
                    lemma=selected["word"] if selected else plan.get("lemma", original["text"]),
                    meaning_status=("user_selected" if user_selected else "verified") if selected and evidence else "unresolved")
    if evidence is None:
        original.update(evidence_candidates=[], evidence_type=None, pronunciations=[], length_evidence=[],
                        applied_rules=[], pronunciation_scope=original["text"], long_glyph_ids=[],
                        coverage={"complete": False, "checked_ranges": [], "unresolved_ranges": [[0, len(original["text"])]]},
                        source="manual" if edited.get("source") == "manual" else None)
        return
    coverage = evidence.get("coverage", {"complete": True, "checked_ranges": [[0, len(original["text"])]], "unresolved_ranges": []})
    is_component = not selected and evidence["evidence_type"] == "rule_inference"
    if is_component:
        automatic_source = "rule"
        reason = "구성 요소의 사전 근거와 원문 범위를 연결했습니다." + (" 전체 범위의 장단은 미확정입니다." if not coverage["complete"] else " 전체 단어 직접 발음과 구별되는 규칙 판정입니다.")
        reason_code = "compound_verified" if coverage["complete"] else "partial_evidence"
    else:
        automatic_source, reason = evidence_description(evidence, consensus=not selected, user_selected=user_selected)
        reason_code = "verified" if selected else "length_consensus"
    original.update(evidence_candidates=[selected["id"]] if selected else evidence["evidence_candidates"],
                    evidence_type=evidence["evidence_type"], pronunciations=evidence["pronunciations"],
                    length_evidence=evidence.get("evidence", [evidence]), applied_rules=evidence.get("applied_rules", []),
                    pronunciation_scope=evidence.get("pronunciation_scope", original["text"]), coverage=coverage,
                    source="manual" if edited.get("source") == "manual" or user_selected else automatic_source,
                    reason=reason, reason_code=reason_code)
    if edited.get("source") == "manual" and edited.get("reason_code") == "manual":
        original.update(reason="사용자가 표시를 수정했습니다. 사전 자동 검증과 구별됩니다.", reason_code="manual")
    # Glyph IDs and geometry were never taken from the snapshot's linguistic
    # metadata. The immutable server extraction provides the only alignment.
    original["long_glyph_ids"] = [original["glyph_ids"][i] for i in evidence["positions"]]


@app.patch("/api/jobs/{job_id}/state")
def patch_state(job_id: str, patch: StatePatch):
    job = session_for(job_id)
    idle_required(job)
    glyphs = {g["id"]: (p["number"], g) for p in job["pages"] for g in p["glyphs"]}
    validated_marks = None
    if patch.marks is not None:
        if len(patch.marks) > 30000:
            raise HTTPException(422, "표시 개수 한도를 초과했습니다.")
        validated_marks = []
        seen = set()
        existing = {m["id"]: m for m in job["marks"]}
        for mark in patch.marks:
            gid = mark.get("glyph_id")
            if gid not in glyphs or gid in seen or not glyphs[gid][1]["valid"]:
                raise HTTPException(422, "중복되거나 유효하지 않은 글자 위치입니다.")
            seen.add(gid)
            original = existing.get(mark.get("id"))
            unchanged = original and all(mark.get(k, default) == original.get(k, default) for k, default in (("glyph_id", None), ("dx", 0), ("dy", 0), ("scale", 1)))
            try:
                dx, dy, scale = float(mark.get("dx", 0)), float(mark.get("dy", 0)), float(mark.get("scale", 1))
                if not (-50 <= dx <= 50 and -50 <= dy <= 50 and 0.3 <= scale <= 3):
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(422, "위치·크기 보정 범위를 확인해 주세요.") from None
            normalized = {"id": str(mark.get("id") or uuid.uuid4().hex)[:100], "page": glyphs[gid][0],
                                    "glyph_id": gid, "source": original["source"] if unchanged else "manual",
                                    "occurrence_id": original.get("occurrence_id") if unchanged else next((o["id"] for o in job["occurrences"] if gid in o["glyph_ids"]), None),
                                    "dx": dx, "dy": dy, "scale": scale}
            if unchanged and original.get("evidence_type"):
                normalized["evidence_type"] = original["evidence_type"]
            validated_marks.append(normalized)
    if patch.occurrences is not None:
        originals = {o["id"]: o for o in job["occurrences"]}
        for edited in patch.occurrences:
            if edited.get("id") not in originals:
                raise HTTPException(422, "원문에 없는 항목입니다.")
            if edited.get("status", "unresolved") not in ("long", "none", "unresolved"):
                raise HTTPException(422, "알 수 없는 판정 상태입니다.")
            note = edited.get("note", "")
            if not isinstance(note, str) or len(note) > 2000:
                raise HTTPException(422, "메모는 2,000자 이하로 입력해 주세요.")
            selected = edited.get("selected_candidate_id")
            if selected is not None and selected not in {c["id"] for c in originals[edited["id"]]["candidates"]}:
                raise HTTPException(422, "서버에서 조회된 사전 후보만 복원할 수 있습니다.")
        for edited in patch.occurrences:
            original = originals[edited["id"]]
            # A review memo does not change pronunciation evidence or its source.
            if "note" in edited:
                original["note"] = edited["note"]
            if "status" in edited and edited["status"] != original.get("status"):
                original["status"] = edited["status"]
                original["source"] = "manual"
                original["reason"] = "사용자가 검토 결과를 수정했습니다."
                original["reason_code"] = "manual"
            if patch.restore and "selected_candidate_id" in edited:
                restore_length_evidence(job, original, edited)
    if patch.settings is not None:
        job["settings"] = patch.settings.model_dump()
    if validated_marks is not None:
        if patch.restore:
            requested = {str(m.get("id")): m for m in patch.marks}
            occurrences = {o["id"]: o for o in job["occurrences"]}
            for mark in validated_marks:
                raw = requested.get(mark["id"], {})
                occurrence = occurrences.get(mark.get("occurrence_id"))
                # Restore automatic provenance only after the server has
                # rebuilt its evidence and verified the exact original glyph
                # and unedited geometry. Moved/added manual circles stay manual.
                if (occurrence and raw.get("source") in ("dictionary", "dictionary_consensus", "rule")
                        and mark["glyph_id"] in occurrence.get("long_glyph_ids", [])
                        and mark["dx"] == mark["dy"] == 0 and mark["scale"] == 1
                        and occurrence.get("evidence_type")):
                    kind = occurrence["evidence_type"]
                    mark.update(source="rule" if "rule" in kind else ("dictionary_consensus" if kind == "candidate_consensus" else "dictionary"),
                                evidence_type=kind)
        old = {m["glyph_id"]: m for m in job["marks"]}
        new = {m["glyph_id"]: m for m in validated_marks}
        semantic_fields = ("id", "glyph_id", "page", "source", "occurrence_id", "dx", "dy", "scale")
        changed = {gid for gid in old.keys() | new.keys()
                   if gid not in old or gid not in new or
                   any(old[gid].get(key) != new[gid].get(key) for key in semantic_fields)}
        for occurrence in job["occurrences"]:
            if any(g in changed for g in occurrence["glyph_ids"]):
                if not patch.restore:
                    occurrence["source"] = "manual"
                    occurrence["status"] = "long" if any(g in new for g in occurrence["glyph_ids"]) else "none"
                    occurrence["reason"] = "사용자가 표시를 수정했습니다. 사전 자동 검증과 구별됩니다."
                    occurrence["reason_code"] = "manual"
        job["marks"] = validated_marks
    history(job, "표시 설정·검토 상태 수정")
    job["warnings"] = [w for w in job["warnings"] if not w.startswith("표시 ")]
    with fitz.open(job["_path"]) as document:
        circles_by_page: dict[int, list[tuple[str, fitz.Rect]]] = {}
        for mark in job["marks"]:
            pno, glyph = glyphs[mark["glyph_id"]]
            page = document[pno - 1]
            rect = circle_for(glyph, mark, job["settings"], page)
            circles_by_page.setdefault(pno, []).append((mark["id"], rect))
            if not (page.rect * page.derotation_matrix).contains(rect):
                job["warnings"].append(f"표시 {mark['id']}의 원형이 페이지 경계에 닿습니다. 위치·크기를 확인해 주세요.")
        # Spatial buckets keep overlap review linear for ordinary script pages.
        for pno, circles in circles_by_page.items():
            buckets: dict[tuple[int, int], list[tuple[str, fitz.Rect]]] = {}
            overlaps = set()
            for mark_id, rect in circles:
                cells = [(x, y) for x in range(int(rect.x0 // 64), int(rect.x1 // 64) + 1)
                         for y in range(int(rect.y0 // 64), int(rect.y1 // 64) + 1)]
                for cell in cells:
                    for other_id, other_rect in buckets.get(cell, []):
                        if rect.intersects(other_rect):
                            overlaps.add(tuple(sorted((mark_id, other_id))))
                    buckets.setdefault(cell, []).append((mark_id, rect))
            if overlaps:
                job["warnings"].append(f"표시 {pno}쪽의 원형 {len(overlaps)}쌍이 겹칩니다. 위치·크기를 확인해 주세요.")
    return public(job)


class CandidateChoice(BaseModel):
    candidate_id: str


@app.post("/api/jobs/{job_id}/occurrences/{occurrence_id}/candidate")
def choose_candidate(job_id: str, occurrence_id: str, choice: CandidateChoice):
    job = session_for(job_id)
    idle_required(job)
    occurrence = next((o for o in job["occurrences"] if o["id"] == occurrence_id), None)
    if not occurrence:
        raise HTTPException(404, "원문 항목을 찾을 수 없습니다.")
    if occurrence.get("numeric") is not None:
        raise HTTPException(422, "숫자는 발음 후보의 뜻 대신 숫자 읽기를 선택해 주세요.")
    candidate = next((c for c in occurrence["candidates"] if c["id"] == choice.candidate_id), None)
    if not candidate:
        raise HTTPException(422, "이 항목에서 조회된 사전 후보를 선택해 주세요.")
    mark_candidate(job, occurrence, candidate, user_selected=True)
    history(job, f"{occurrence_id}의 사전 의미 선택")
    return public(job)


class ReadingChoice(BaseModel):
    option_id: str = Field(min_length=1, max_length=100)


@app.post("/api/jobs/{job_id}/occurrences/{occurrence_id}/reading")
async def choose_reading(job_id: str, occurrence_id: str, choice: ReadingChoice):
    from .numbers import evaluate_number
    job = session_for(job_id)
    idle_required(job)
    occurrence = next((o for o in job["occurrences"] if o["id"] == occurrence_id), None)
    if occurrence is None:
        raise HTTPException(404, "원문 항목을 찾을 수 없습니다.")
    plan = occurrence.get("numeric")
    if not plan or choice.option_id not in {option["id"] for option in plan.get("options", [])}:
        raise HTTPException(422, "이 항목에서 제안한 숫자 읽기만 선택할 수 있습니다.")
    revision = job["revision"]
    try:
        result = await evaluate_number(deepcopy(plan), job["_dictionary"], choice.option_id)
    except DictionaryError as error:
        raise HTTPException(502, str(error)) from None
    # A lookup must not overwrite an edit or analysis made while it awaited I/O.
    if session_for(job_id) is not job or job["revision"] != revision:
        raise HTTPException(409, "작업이 변경되었습니다. 최신 상태에서 읽기를 다시 선택해 주세요.")
    idle_required(job)
    remember_number_result(job, occurrence, result)
    apply_number_result(job, occurrence, result, user_selected=True)
    history(job, f"{occurrence_id}의 숫자 읽기 선택")
    return public(job)


@app.get("/api/jobs/{job_id}/pages/{page}/preview.png")
def preview(job_id: str, page: int, marked: bool = True, scale: float = Query(default=1.5, ge=0.2, le=2)):
    job = session_for(job_id)
    if page < 1 or page > len(job["pages"]):
        raise HTTPException(404, "페이지가 없습니다.")
    data = job["_path"].read_bytes()
    document = marked_document(data, job, page_numbers={page}) if marked else fitz.open(stream=data, filetype="pdf")
    pdf_page = document[page - 1]
    scale = min(scale, 2400 / max(pdf_page.rect.width, pdf_page.rect.height))
    png = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
    document.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/export/{kind}")
def export(job_id: str, kind: str):
    job = session_for(job_id)
    idle_required(job)
    base = job["filename"].rsplit(".", 1)[0]
    if kind == "pdf":
        try:
            body = output_pdf(job["_path"].read_bytes(), job)
        except ValueError:
            raise HTTPException(500, "원문 보존 검증에 실패했습니다. PDF를 내보내지 않았습니다.") from None
        content_type, filename = "application/pdf", base + "_장음표시.pdf"
    elif kind == "md":
        body = verification(job).encode("utf-8")
        content_type, filename = "text/markdown; charset=utf-8", base + "_장음검증.md"
    elif kind == "csv":
        body = csv_export(job)
        content_type, filename = "text/csv; charset=utf-8", base + "_장음목록.csv"
    else:
        raise HTTPException(404, "지원하지 않는 다운로드 형식입니다.")
    return Response(body, media_type=content_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


@app.delete("/api/jobs/{job_id}")
async def delete(job_id: str):
    session_for(job_id)
    task = TASKS.pop(job_id, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    shutil.rmtree(ROOT / job_id, ignore_errors=True)
    del SESSIONS[job_id]
    return {"deleted": True}


# Built frontend is optional in development (Vite proxies /api to this server).
DIST = Path(__file__).resolve().parents[1] / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "요청한 API를 찾을 수 없습니다.")
        target = (DIST / path).resolve()
        if path and target.is_relative_to(DIST.resolve()) and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
