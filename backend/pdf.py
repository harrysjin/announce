from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from datetime import datetime, timezone

import fitz

TOKEN = re.compile(r"[가-힣A-Za-z0-9]+(?:[.·\-/][가-힣A-Za-z0-9]+)*")


def extract(data: bytes) -> tuple[list[dict], list[dict]]:
    document = fitz.open(stream=data, filetype="pdf")
    if document.needs_pass:
        document.close()
        raise ValueError("encrypted")
    if document.page_count > 100:
        document.close()
        raise ValueError("too_many_pages")
    if document.page_count == 0:
        document.close()
        raise ValueError("empty")
    pages, occurrences = [], []
    for pno, page in enumerate(document):
        glyphs = []
        page_lines = []
        glyph_index = 0
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                line_glyphs = []
                for span in line["spans"]:
                    for char in span["chars"]:
                        rect = fitz.Rect(char["bbox"])
                        display = rect * page.rotation_matrix
                        good = (not rect.is_empty and all(math.isfinite(v) for v in rect)
                                and math.isfinite(span["size"]) and 0 < span["size"] <= 500)
                        glyph = {"id": f"p{pno+1}g{glyph_index}", "text": char["c"],
                                 "bbox": list(display), "pdf_bbox": list(rect),
                                 "size": float(span["size"]), "valid": good,
                                 "line": len(page_lines)}
                        glyph_index += 1
                        glyphs.append(glyph)
                        line_glyphs.append(glyph)
                text = "".join(g["text"] for g in line_glyphs)
                page_lines.append(text)
                # Character spans remain attached to each occurrence, rather than
                # searching for text again during output.
                char_map = [g for g in line_glyphs for _ in g["text"]]
                for match in TOKEN.finditer(text):
                    selected = char_map[match.start():match.end()]
                    surface = match.group()
                    kind = "number" if any(c.isdigit() for c in surface) else ("foreign" if re.search(r"[A-Za-z]", surface) else "word")
                    occurrences.append({
                        "id": f"o{len(occurrences)}", "page": pno + 1,
                        "text": surface, "lemma": surface,
                        "context": text, "glyph_ids": list(dict.fromkeys(g["id"] for g in selected)),
                        "status": "unresolved", "reason": "아직 사전을 조회하지 않았습니다.",
                        "reason_code": "not_analyzed", "kind": kind, "candidates": [],
                        "selected_candidate_id": None, "note": "", "source": None,
                    })
        text = "\n".join(page_lines)
        visible = [g for g in glyphs if g["text"].strip()]
        bad = sum("�" in g["text"] or not g["valid"] for g in visible)
        quality = "scan" if not visible else ("poor" if bad / len(visible) > 0.05 else "text")
        pages.append({"number": pno + 1, "width": page.rect.width, "height": page.rect.height,
                      "rotation": page.rotation, "quality": quality,
                      "text": text, "glyphs": glyphs})
    document.close()
    return pages, occurrences


def circle_for(glyph: dict, mark: dict, settings: dict, page: fitz.Page | None = None) -> fitz.Rect:
    rect = fitz.Rect(glyph["bbox"] if page is not None else glyph["pdf_bbox"])
    diameter = (glyph["size"] + 3) * settings["size"] * mark.get("scale", 1)
    center = (rect.tl + rect.br) / 2
    center.x += mark.get("dx", 0)
    center.y += mark.get("dy", 0)
    result = fitz.Rect(center.x - diameter / 2, center.y - diameter / 2,
                       center.x + diameter / 2, center.y + diameter / 2)
    return result * page.derotation_matrix if page is not None else result


def evidence_for(occurrence: dict) -> list[dict]:
    ids = occurrence.get("evidence_candidates") or [occurrence.get("selected_candidate_id")]
    candidates = [c for c in occurrence.get("candidates", []) if c["id"] in ids]
    for component in occurrence.get("component_results", []):
        component_ids = component.get("evidence_candidates", [])
        candidates.extend(c for c in component.get("candidates", []) if c["id"] in component_ids)
    return list({c["id"]: c for c in candidates}.values())


def syllable_range(occurrence: dict, start: int, end: int) -> str:
    """Describe original half-open syllable offsets without rewriting the PDF."""
    surface = "".join(re.findall(r"[가-힣]", occurrence["text"])[start:end])
    extent = str(start + 1) if end == start + 1 else f"{start + 1}–{end}"
    return f"{surface} ({extent}음절, 원문 [{start}:{end}])"


def coverage_description(occurrence: dict) -> str:
    coverage = occurrence.get("coverage")
    if not coverage:
        return ""
    parts = ["전체 범위 확인" if coverage.get("complete") else "일부 범위만 확인 · 전체 장단 미확정"]
    for key, label in (("checked_ranges", "확인 범위"), ("unresolved_ranges", "미확정 범위")):
        spans = coverage.get(key, [])
        if spans:
            parts.append(label + ": " + "; ".join(syllable_range(occurrence, start, end) for start, end in spans))
    return " · ".join(parts)


def lookup_candidates(occurrence: dict) -> list[dict]:
    """Retain discovery records separately from established length evidence."""
    return list({candidate["id"]: candidate
                 for candidate in occurrence.get("all_candidates", []) + occurrence.get("candidates", [])}.values())


def verification(job: dict) -> str:
    summary = counts(job)
    recorded_at = job.get("history", [])[-1]["at"] if job.get("history") else job.get("created_at", "미기록")
    lines = [f"# {job['filename']} 장음 검증 기록", "",
             f"- 원본 SHA-256: `{job['sha256']}`", f"- 마지막 작업 기록 시각: {recorded_at}",
             f"- 표시: {summary['marks']}개 / 원문 출현: {summary['occurrences']}개 / 장단 미확정: {summary['unresolved']}개",
             f"- 뜻 선택 미확정: {summary['meaning_unresolved']}개 (장단 판정과 별개)",
             f"- 색상: {job['settings']['color']}, 불투명도: {job['settings']['opacity']}, 크기: {job['settings']['size']}",
             "- 후보 간 장단 일치는 특정 뜻을 자동 선택했다는 뜻이 아닙니다. 근거 후보를 모두 기록합니다.",
             "- 실제 발화 형태의 사전 발음과 규칙 적용을 구별합니다. 사용자 수정은 사전 자동 검증으로 간주하지 않습니다.",
             "- 스캔/OCR 및 일반적인 문맥 의미의 완전 자동 판정은 지원하지 않습니다.",
             "", "|쪽|원문|장단 판정|뜻 상태|근거 유형|표제어·한자|발음·범위|뜻·사유|출처·조회 시각|확인 범위|메모|",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    esc = lambda value: str(value or "").replace("|", "\\|").replace("\n", " ")
    for occurrence in job["occurrences"]:
        evidence = evidence_for(occurrence)
        selected = next((c for c in evidence if c["id"] == occurrence.get("selected_candidate_id")), {})
        state = {"long": "장음 위치 확인", "none": "표시 없음", "unresolved": "미확정"}[occurrence["status"]]
        if occurrence.get("source") == "manual":
            state = "사용자 수정 · " + state
        sources = "; ".join(f"{c['id']} {c.get('url','')} {c.get('queried_at','')}" for c in evidence)
        rules = "; ".join(f"{r.get('label','')} {r.get('explanation','')} {r.get('url','')}" for r in occurrence.get("applied_rules", []))
        readings = " / ".join(dict.fromkeys(occurrence.get("pronunciations") or [c.get("pronunciation", "") for c in evidence]))
        scope = occurrence.get("pronunciation_scope")
        if scope and scope != occurrence["text"]:
            readings = "기본형·구성요소 발음: " + readings + f" (범위: {scope})"
        values = [occurrence["page"], occurrence["text"], state, occurrence.get("meaning_status", "미확정"),
                  occurrence.get("evidence_type", ""),
                  "; ".join(dict.fromkeys(f"{c.get('word','')} {c.get('origin','')}" for c in evidence)),
                  readings,
                  f"{selected.get('definition','')} {occurrence['reason']}", sources + (" · " + rules if rules else ""),
                  coverage_description(occurrence), occurrence.get("note", "")]
        lines.append("|" + "|".join(esc(v) for v in values) + "|")
    lookups = [(occurrence, candidate) for occurrence in job["occurrences"]
               for candidate in lookup_candidates(occurrence)]
    if lookups:
        lines.extend(["", "## 기본형·전체 단어 후보 조회 기록", "",
                      "- 아래는 조회된 후보의 기록이며, 특정 뜻을 선택한 결과가 아닙니다. 판정에 사용한 후보는 위 근거 기록과 구별합니다.",
                      "- 표제어 발음은 원문 전체의 직접 발음이나 활용형·복합어로의 자동 복사를 뜻하지 않습니다.", "",
                      "|쪽|원문|후보 ID|표제어·한자|품사|표제어 사전 발음|후보 뜻|조회·판정 상태|출처·조회 시각|",
                      "|---|---|---|---|---|---|---|---|---|"])
        for occurrence, candidate in lookups:
            selected = candidate["id"] == occurrence.get("selected_candidate_id")
            excluded = candidate["id"] in occurrence.get("excluded_candidates", [])
            state = "선택된 후보" if selected else ("형태·품사 조건에서 제외된 조회 후보" if excluded else "미선택 조회 후보")
            values = [occurrence["page"], occurrence["text"], candidate["id"],
                      f"{candidate.get('word','')} {candidate.get('origin','')}", candidate.get("pos", ""),
                      " / ".join(candidate.get("pronunciations", [])), candidate.get("definition", ""), state,
                      f"{candidate.get('url','')} {candidate.get('queried_at','')}"]
            lines.append("|" + "|".join(esc(v) for v in values) + "|")
    components = [(occurrence, component) for occurrence in job["occurrences"]
                  for component in occurrence.get("component_results", [])]
    if components:
        lines.extend(["", "## 구성요소별 조회 근거", "",
                      "- 아래 뜻은 구성요소의 후보 뜻이며, 전체 표현의 뜻을 선택한 결과가 아닙니다.",
                      "- 범위는 원문 첫 음절을 1로 센 값이며, 원문 글자·좌표를 변경하지 않습니다.", "",
                      "|쪽|원문|원문 음절 범위|기본형·품사|구성요소 상태·사유|후보 ID|표제어·한자|사전 발음|후보 뜻|출처·조회 시각|",
                      "|---|---|---|---|---|---|---|---|---|---|"])
        for occurrence, component in components:
            for candidate in component.get("candidates") or [{}]:
                values = [occurrence["page"], occurrence["text"],
                          syllable_range(occurrence, component["start"], component["end"]),
                          f"{component.get('lemma','')} · {candidate.get('pos') or component.get('pos','')}",
                          f"{component.get('status','unresolved')} · {component.get('reason','')}", candidate.get("id", ""),
                          f"{candidate.get('word','')} {candidate.get('origin','')}",
                          " / ".join(candidate.get("pronunciations", [])), candidate.get("definition", ""),
                          f"{candidate.get('url','')} {candidate.get('queried_at','')}"]
                lines.append("|" + "|".join(esc(v) for v in values) + "|")
    lines.extend(["", "## 표시 좌표와 사용자 수정", "", "|표시 ID|쪽|문자 ID|출처|위치 보정(pt)|크기 배율|", "|---|---|---|---|---|---|"])
    for mark in job["marks"]:
        lines.append(f"|{mark['id']}|{mark['page']}|{mark['glyph_id']}|{mark['source']}|{mark.get('dx',0)}, {mark.get('dy',0)}|{mark.get('scale',1)}|")
    if job.get("warnings"):
        lines.extend(["", "## 입력·좌표 경고", ""] + [f"- {w}" for w in job["warnings"]])
    lines.extend(["", "## 수정 이력", ""] + [f"- {h['at']} · {h['action']}" for h in job.get("history", [])])
    return "\n".join(lines) + "\n"


def counts(job: dict) -> dict:
    occurrences = job["occurrences"]
    return {"words": len({o["lemma"] for o in occurrences}), "occurrences": len(occurrences),
            "marks": len(job["marks"]), "unresolved": sum(o["status"] == "unresolved" for o in occurrences),
            "long": sum(o["status"] == "long" for o in occurrences),
            "none": sum(o["status"] == "none" for o in occurrences),
            "meaning_unresolved": sum(o.get("meaning_status") == "unresolved" for o in occurrences),
            "consensus": sum(o.get("evidence_type") in ("candidate_consensus", "rule_consensus") for o in occurrences),
            "manual": sum(o.get("source") == "manual" for o in occurrences)}


def csv_export(job: dict) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["쪽", "원문", "표제어", "한자", "발음", "선택한 뜻", "장단 상태", "뜻 상태", "근거 유형", "사유", "출처", "조회 시각", "적용 규칙", "메모", "발음 범위", "검증 범위", "미확정 범위", "구성요소별 근거", "기본형·전체 단어 후보 조회 기록"])
    for o in job["occurrences"]:
        evidence = evidence_for(o)
        c = next((c for c in evidence if c["id"] == o.get("selected_candidate_id")), {})
        unique = lambda key: " / ".join(dict.fromkeys(str(c.get(key, "")) for c in evidence))
        component_records = []
        for component in o.get("component_results", []):
            scope = syllable_range(o, component["start"], component["end"])
            for candidate in component.get("candidates") or [{}]:
                component_records.append(f"{scope} · {component.get('lemma','')} · {component.get('status','unresolved')} · "
                                         f"{component.get('reason','')} · {candidate.get('id','')} · {candidate.get('pos','')} · "
                                         f"{candidate.get('definition','')} · {candidate.get('pronunciation','')} · "
                                         f"{candidate.get('url','')} · {candidate.get('queried_at','')}")
        candidate_records = []
        for candidate in lookup_candidates(o):
            selected = candidate["id"] == o.get("selected_candidate_id")
            excluded = candidate["id"] in o.get("excluded_candidates", [])
            state = "선택된 후보" if selected else ("형태·품사 조건에서 제외된 조회 후보" if excluded else "미선택 조회 후보")
            candidate_records.append(f"{state} · {candidate['id']} · {candidate.get('word','')} · {candidate.get('origin','')} · "
                                     f"{candidate.get('pos','')} · {candidate.get('definition','')} · "
                                     f"{candidate.get('pronunciation') or ' / '.join(candidate.get('pronunciations', []))} · "
                                     f"{candidate.get('url','')} · {candidate.get('queried_at','')}")
        row = [o["page"], o["text"], unique("word"), unique("origin"),
               " / ".join(o.get("pronunciations") or [unique("pronunciation")]), c.get("definition"),
               o["status"], o.get("meaning_status", "unresolved"), o.get("evidence_type"), o["reason"],
               unique("url"), unique("queried_at"),
               " / ".join(" ".join(filter(None, (r.get("label"), r.get("explanation"), r.get("url")))) for r in o.get("applied_rules", [])), o.get("note"),
               o.get("pronunciation_scope", ""), coverage_description(o),
               "; ".join(syllable_range(o, start, end) for start, end in o.get("coverage", {}).get("unresolved_ranges", [])),
               "\n".join(component_records), "\n".join(candidate_records)]
        writer.writerow([("'" + str(v)) if str(v or "").startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def marked_document(data: bytes, job: dict, attach_log: bool = False) -> fitz.Document:
    document = fitz.open(stream=data, filetype="pdf")
    color = job["settings"]["color"].lstrip("#")
    rgb = tuple(int(color[i:i+2], 16) / 255 for i in (0, 2, 4))
    glyphs = {g["id"]: g for p in job["pages"] for g in p["glyphs"]}
    occurrences = {o["id"]: o for o in job["occurrences"]}
    for mark in job["marks"]:
        page = document[mark["page"] - 1]
        glyph = glyphs[mark["glyph_id"]]
        circle = circle_for(glyph, mark, job["settings"], page)
        # Circle annotations retain searchable original text and existing
        # annotations. Multiply prevents covering the glyphs with pale fill.
        annotation = page.add_circle_annot(circle)
        # None means "leave unchanged" in PyMuPDF and would preserve the
        # circle annotation's default red stroke. An empty sequence removes it.
        annotation.set_colors(stroke=[], fill=rgb)
        annotation.set_border(width=0)
        annotation.set_opacity(job["settings"]["opacity"])
        annotation.set_blendmode("Multiply")
        occurrence = occurrences.get(mark.get("occurrence_id"))
        evidence = evidence_for(occurrence) if occurrence else []
        dictionary_urls = list(dict.fromkeys(c["url"] for c in evidence if c.get("url")))
        rule_urls = [rule["url"] for rule in (occurrence or {}).get("applied_rules", []) if rule.get("url")]
        urls = list(dict.fromkeys(dictionary_urls + rule_urls))
        description = "사용자 수정" if mark["source"] == "manual" else (occurrence or {}).get("reason", "표준국어대사전 발음 근거")
        if occurrence:
            description += "\n근거 유형: " + str(occurrence.get("evidence_type") or "사용자 수정")
            for rule in occurrence.get("applied_rules", []):
                description += "\n" + " · ".join(filter(None, (rule.get("label"), rule.get("explanation"))))
            coverage = coverage_description(occurrence)
            if coverage:
                description += "\n" + coverage
        annotation.set_info(title="장음 표시", content=description + ("\n" + "\n".join(urls) if urls else ""))
        annotation.set_flags(fitz.PDF_ANNOT_IS_PRINT)
        annotation.update()
        # A single URI is safe only when all evidence points to the same entry.
        # Multi-entry consensus retains every URL in the annotation and attached log.
        if len(urls) == 1:
            page.insert_link({"kind": fitz.LINK_URI, "from": circle, "uri": urls[0]})
    if attach_log:
        name = job["filename"].rsplit(".", 1)[0] + "_장음검증.md"
        # Preserve existing file attachments, even when their names collide.
        existing = set(document.embfile_names())
        if name in existing:
            name = f"{job['id'][:8]}_{name}"
        document.embfile_add(name, verification(job).encode("utf-8"), filename=name, ufilename=name)
    return document


def preservation_signature(document: fitz.Document) -> list[tuple]:
    return [(page.get_text(), tuple(page.mediabox), tuple(page.cropbox), page.rotation) for page in document]


def output_pdf(data: bytes, job: dict) -> bytes:
    original = fitz.open(stream=data, filetype="pdf")
    expected = preservation_signature(original)
    original.close()
    result = marked_document(data, job, attach_log=True)
    output = result.tobytes(garbage=3, deflate=True)
    result.close()
    check = fitz.open(stream=output, filetype="pdf")
    if preservation_signature(check) != expected:
        check.close()
        raise ValueError("원문 보존 검증에 실패했습니다.")
    check.close()
    return output


def sample_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    # Built-in CJK font keeps the sample self-contained.
    page.insert_text((64, 80), "낭독 연습", fontname="korea", fontsize=24)
    page.insert_text((64, 112), "예시 문서 · 업로드와 수동 표시를 체험해 보세요.", fontname="korea", fontsize=10, color=(0.4, 0.4, 0.4))
    lines = ["좋은 아침입니다.", "", "오늘은 우리의 언어에 귀를 기울입니다.", "같은 글자도 뜻과 문맥에 따라 다르게 읽힙니다.", "", "눈이 내리는 아침, 눈을 감고 소리를 들어 봅니다.", "말의 길이를 살피면 문장이 조금 더 또렷해집니다.", "", "천천히 읽고, 잠시 쉬고, 다시 이어 갑니다."]
    for i, line in enumerate(lines):
        page.insert_text((64, 178 + i * 32), line, fontname="korea", fontsize=14)
    page.insert_text((64, 760), "사전 조회 전에는 장음이 자동으로 표시되지 않습니다.", fontname="korea", fontsize=10, color=(0.5, 0.5, 0.5))
    data = document.tobytes()
    document.close()
    return data
