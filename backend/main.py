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

from .dictionary import DictionaryClient, DictionaryError, long_positions, pronunciation_for
from .pdf import extract, counts, marked_document, output_pdf, csv_export, verification, sample_pdf, circle_for
from .runtime import ACTIVE_STATES, AnalysisGate, SlidingWindowLimiter, progress_snapshot

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


app = FastAPI(title="장음 · PDF 낭독 작업 공간", version="0.1.0", lifespan=lifespan)


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
            "ocr_supported": False, "version": "0.1.0"}


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


def mark_candidate(job: dict, occurrence: dict, candidate: dict | None, user_selected: bool = False, consensus_candidates: list[dict] | None = None):
    from .linguistics import candidate_evidence, consensus_evidence
    is_consensus = consensus_candidates is not None
    combined = consensus_evidence(consensus_candidates, occurrence["text"]) if is_consensus else None
    evidence = combined if is_consensus else candidate_evidence(candidate, occurrence["text"])
    occurrence["selected_candidate_id"] = None if is_consensus else candidate["id"]
    occurrence["lemma"] = occurrence.get("morphology", {}).get("lemma", occurrence["text"]) if is_consensus else candidate["word"]
    occurrence["source"] = "manual" if user_selected else ("dictionary_consensus" if is_consensus else "dictionary")
    occurrence["meaning_status"] = "unresolved" if is_consensus else ("user_selected" if user_selected else "verified")
    occurrence["evidence_candidates"] = [c["id"] for c in consensus_candidates] if is_consensus else [candidate["id"]]
    occurrence["applied_rules"] = []
    occurrence["long_glyph_ids"] = []
    job["marks"] = [m for m in job["marks"] if m.get("occurrence_id") != occurrence["id"] or m["source"] == "manual"]
    if evidence is None:
        occurrence.update(status="unresolved", reason="실제 형태의 직접 발음 근거가 없거나, 후보별 장단·음절 연결이 다릅니다. 기본형 발음을 복사하지 않습니다.", reason_code="inflection_unverified", evidence_type=None)
        if not user_selected:
            occurrence.update(meaning_status="unresolved", selected_candidate_id=None)
        return
    positions = evidence["positions"]
    page = job["pages"][occurrence["page"] - 1]
    glyph_map = {g["id"]: g for g in page["glyphs"]}
    ids = [gid for gid in occurrence["glyph_ids"] if re.fullmatch(r"[가-힣]", glyph_map[gid]["text"])]
    if any(i >= len(ids) for i in positions) or not ids:
        occurrence.update(status="unresolved", reason="발음과 원문 음절 위치를 연결할 수 없습니다.", reason_code="alignment", evidence_type=None)
        return
    if page["quality"] != "text" or any(not glyph_map[g]["valid"] for g in ids):
        occurrence.update(status="unresolved", reason="문자 좌표 품질을 확인해야 합니다.", reason_code="coordinates", evidence_type=None)
        return
    occurrence["pronunciations"] = evidence["pronunciations"]
    occurrence["pronunciation_scope"] = occurrence["text"]
    occurrence["evidence_type"] = "candidate_consensus" if is_consensus else evidence["evidence_type"]
    occurrence["length_evidence"] = evidence["evidence"] if is_consensus else [evidence]
    reason = ("모든 조회 후보의 장단이 일치합니다. 문맥의 뜻 선택은 미확정입니다." if is_consensus else
              evidence["explanation"] + (" · 사용자 의미 선택" if user_selected else " · 문법·형태와 단일 의미 직접 일치"))
    occurrence.update(status="long" if positions else "none", reason=reason, reason_code="length_consensus" if is_consensus else "verified")
    occurrence["long_glyph_ids"] = [ids[i] for i in positions]
    existing = {m["glyph_id"] for m in job["marks"]}
    for glyph_id in occurrence["long_glyph_ids"]:
        if glyph_id not in existing:
            job["marks"].append({"id": uuid.uuid4().hex, "page": occurrence["page"], "glyph_id": glyph_id,
                                 "source": "dictionary_consensus" if is_consensus else "dictionary",
                                 "evidence_type": occurrence["evidence_type"],
                                 "occurrence_id": occurrence["id"], "dx": 0, "dy": 0, "scale": 1})
            existing.add(glyph_id)


# These transformations are candidate discovery only. A stripped form never
# inherits the lemma's length; it stays unresolved without an exact inflection.
PARTICLES = ("으로부터", "에게서", "이라고", "에서는", "으로는", "까지는", "부터는", "으로", "에서", "에게", "께서", "하고", "처럼", "보다", "까지", "부터", "이며", "이라", "을", "를", "은", "는", "이", "가", "의", "에", "와", "과", "도", "만")


async def analyze_job(job: dict):
    from .linguistics import occurrence_plans, candidates_for_plan, consensus_evidence
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
                                  long_glyph_ids=[], length_evidence=[])
                job["marks"] = [m for m in job["marks"] if m.get("occurrence_id") != occurrence["id"] or m["source"] == "manual"]
                if plan["kind"] in ("number", "foreign") or occurrence["kind"] != "word":
                    occurrence.update(status="unresolved", reason="숫자·단위 또는 외래 표기의 실제 읽기를 확인해 주세요.", reason_code="reading_required")
                    continue
                try:
                    queries = list(dict.fromkeys([word, plan["lemma"]]))
                    found = []
                    for query in queries:
                        found.extend(await client.lookup(query))
                    all_candidates = list({c["id"]: c for c in found}.values())
                    candidates = candidates_for_plan(all_candidates, plan)
                    occurrence["all_candidates"] = all_candidates
                    occurrence["excluded_candidates"] = [c["id"] for c in all_candidates if c["id"] not in {c["id"] for c in candidates}]
                    occurrence["candidates"] = candidates
                    occurrence["lemma"] = plan["lemma"]
                    if plan["kind"] == "proper":
                        occurrence.update(status="unresolved", reason="인명·고유명사로 분석되었습니다. 비슷한 보통명사의 장음을 옮기지 않습니다.", reason_code="proper_name")
                    elif len(candidates) == 1:
                        mark_candidate(job, occurrence, candidates[0])
                    elif candidates:
                        combined = consensus_evidence(candidates, word)
                        if combined is not None:
                            mark_candidate(job, occurrence, None, consensus_candidates=candidates)
                        else:
                            surface_ambiguous = plan["kind"] == "nominal_particle" and any(c["word"] == word for c in candidates) and any(c["word"] == plan["lemma"] for c in candidates)
                            occurrence.update(status="unresolved", reason="문맥에 맞는 후보와 실제 형태의 발음 근거를 확인해야 합니다. 후보 간 장단이 다르거나 직접 근거가 없는 후보가 있습니다.", reason_code="surface_ambiguous" if surface_ambiguous else "ambiguous")
                    else:
                        occurrence.update(status="unresolved", reason="문법·형태에 맞는 공식 표제어를 찾지 못했습니다. 단음으로 판정하지 않습니다.", reason_code="not_found")
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
                original["selected_candidate_id"] = edited["selected_candidate_id"]
                selected = next((c for c in original["candidates"] if c["id"] == edited["selected_candidate_id"]), None)
                original["lemma"] = selected["word"] if selected else original["text"]
                original["meaning_status"] = "user_selected" if selected else "unresolved"
                # Rebuild evidence from server-owned candidates, never from
                # client pronunciation strings or an arbitrary consensus subset.
                from .linguistics import candidate_evidence, consensus_evidence
                restored = candidate_evidence(selected, original["text"]) if selected else None
                if selected and restored:
                    original.update(evidence_candidates=[selected["id"]],
                                    evidence_type=restored["evidence_type"],
                                    pronunciations=restored["pronunciations"], length_evidence=[restored])
                elif (not selected and edited.get("evidence_type") == "candidate_consensus"
                      and set(edited.get("evidence_candidates", [])) == {c["id"] for c in original["candidates"]}):
                    agreed = consensus_evidence(original["candidates"], original["text"])
                    if agreed:
                        original.update(evidence_candidates=agreed["evidence_candidates"],
                                        evidence_type="candidate_consensus", pronunciations=agreed["pronunciations"],
                                        length_evidence=agreed["evidence"])
                    else:
                        original.update(evidence_candidates=[], evidence_type=None, pronunciations=[], length_evidence=[])
                else:
                    original.update(evidence_candidates=[], evidence_type=None, pronunciations=[], length_evidence=[])
    if patch.settings is not None:
        job["settings"] = patch.settings.model_dump()
    if validated_marks is not None:
        old = {m["glyph_id"]: m for m in job["marks"]}
        new = {m["glyph_id"]: m for m in validated_marks}
        semantic_fields = ("id", "glyph_id", "page", "source", "occurrence_id", "dx", "dy", "scale")
        changed = {gid for gid in old.keys() | new.keys()
                   if gid not in old or gid not in new or
                   any(old[gid].get(key) != new[gid].get(key) for key in semantic_fields)}
        for occurrence in job["occurrences"]:
            if any(g in changed for g in occurrence["glyph_ids"]):
                occurrence["source"] = "manual"
                if not patch.restore:
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
    candidate = next((c for c in occurrence["candidates"] if c["id"] == choice.candidate_id), None)
    if not candidate:
        raise HTTPException(422, "이 항목에서 조회된 사전 후보를 선택해 주세요.")
    mark_candidate(job, occurrence, candidate, user_selected=True)
    history(job, f"{occurrence_id}의 사전 의미 선택")
    return public(job)


@app.get("/api/jobs/{job_id}/pages/{page}/preview.png")
def preview(job_id: str, page: int, marked: bool = True, scale: float = Query(default=1.5, ge=0.2, le=2)):
    job = session_for(job_id)
    if page < 1 or page > len(job["pages"]):
        raise HTTPException(404, "페이지가 없습니다.")
    data = job["_path"].read_bytes()
    document = marked_document(data, job) if marked else fitz.open(stream=data, filetype="pdf")
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
