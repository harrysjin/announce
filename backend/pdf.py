from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from datetime import datetime, timezone

import fitz

TOKEN = re.compile(
    r"(?:[₩$€]?\d{1,2}:\d{2}(?::\d{2})?[가-힣]*"
    r"|[₩$€]?\d{1,3}(?:,\d{3})+(?:\.\d+)?[가-힣]*"
    r"|[₩$€]?[가-힣A-Za-z0-9]+(?:[.·\-/][가-힣A-Za-z0-9]+)*)[%％]?"
)


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
    characters = occurrence.get("numeric") is not None or occurrence.get("coverage", {}).get("coordinate_system") == "characters"
    surface = occurrence["text"][start:end] if characters else "".join(re.findall(r"[가-힣]", occurrence["text"])[start:end])
    extent = str(start + 1) if end == start + 1 else f"{start + 1}–{end}"
    return f"{surface} ({extent}{'문자' if characters else '음절'}, 원문 [{start}:{end}])"


def numeric_record(occurrence: dict) -> str:
    numeric = occurrence.get("numeric")
    if numeric is None:
        return ""
    selected = numeric.get("selected_option_id")
    records = [f"{numeric.get('kind','')} · 읽기 선택 {selected or '미확정'} · {numeric.get('choice_source','automatic')}"]
    for option in numeric.get("options", []):
        records.append(f"{option.get('id','')} · {option.get('system','')} · {option.get('reading','')} · " + ("선택" if selected == option.get("id") else "미선택 후보"))
        for item in option.get("alignment", []):
            start, end = item["source_start"], item["source_end"]
            records.append(f"원문 {occurrence['text'][start:end]} [{start}:{end}] → "
                           f"발음 음절 [{item['reading_start']}:{item['reading_end']}] · exact={str(item.get('exact', False)).lower()}")
    return "\n".join(records)


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
    numbers = [o for o in job["occurrences"] if o.get("numeric") is not None]
    if numbers:
        lines.extend(["", "## 숫자 읽기와 원문 대응", "",
                      "- 읽기 선택과 사전 직접 발음·규칙 판정은 별도로 기록합니다. exact=false인 대응에는 장음 원을 임의로 넣지 않습니다.",
                      "- 원문 범위는 숫자·기호·단위를 포함한 0부터 시작하는 문자 반개방 범위입니다. 발음 범위는 읽기의 음절 범위입니다.", "",
                      "|쪽|원문|숫자 용법·읽기 선택·대응 기록|", "|---|---|---|"])
        for occurrence in numbers:
            lines.append("|" + "|".join(esc(v) for v in (occurrence["page"], occurrence["text"], numeric_record(occurrence))) + "|")
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
    writer.writerow(["쪽", "원문", "표제어", "한자", "발음", "선택한 뜻", "장단 상태", "뜻 상태", "근거 유형", "사유", "출처", "조회 시각", "적용 규칙", "메모", "발음 범위", "검증 범위", "미확정 범위", "구성요소별 근거", "기본형·전체 단어 후보 조회 기록", "숫자 읽기와 원문 대응"])
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
               "\n".join(component_records), "\n".join(candidate_records), numeric_record(o)]
        writer.writerow([("'" + str(v)) if str(v or "").startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


# Highlight paint is page vector content; Circle annotations below only retain
# review metadata. In particular no viewer has to honor an annotation blend mode.
_MASK_SCALE = 4
_MASK_TILE = 64
_MAX_MASK_TILES = 64
_MAX_CLIP_RECTS = 1024
_MAX_PAGE_CLIP_RECTS = 40000
_MAX_MASK_MARKS = 256


def _complement_rectangles(bounds: fitz.Rect, excluded: list[fitz.Rect]) -> list[fitz.Rect]:
    """Nonoverlapping rectangles outside every protected glyph bounding box."""
    if bounds.is_empty or _MAX_CLIP_RECTS <= 0:
        return []
    boxes = [r & bounds for r in excluded if r.intersects(bounds)]
    if not boxes:
        return [bounds]
    if len(boxes) > 2000:
        # Bound work for an unusually large manual circle. Protect the union
        # box rather than risk painting over any original glyph.
        union = fitz.Rect(boxes[0])
        for rect in boxes[1:]:
            union |= rect
        boxes = [union]
    ys = sorted({bounds.y0, bounds.y1, *(v for r in boxes for v in (r.y0, r.y1))})
    result = []
    for top, bottom in zip(ys, ys[1:]):
        intervals = sorted((r.x0, r.x1) for r in boxes if r.y0 < bottom and r.y1 > top)
        left = bounds.x0
        for start, end in intervals:
            if start > left:
                result.append(fitz.Rect(left, top, start, bottom))
            left = max(left, end)
        if left < bounds.x1:
            result.append(fitz.Rect(left, top, bounds.x1, bottom))
    if len(result) > _MAX_CLIP_RECTS:
        union = fitz.Rect(boxes[0])
        for rect in boxes[1:]:
            union |= rect
        # A union leaves at most four safe strips. Do not recurse: a very small
        # budget can still be lower than this conservative complement's size.
        result = [fitz.Rect(bounds.x0, bounds.y0, bounds.x1, union.y0),
                  fitz.Rect(bounds.x0, union.y1, bounds.x1, bounds.y1),
                  fitz.Rect(bounds.x0, union.y0, union.x0, union.y1),
                  fitz.Rect(union.x1, union.y0, bounds.x1, union.y1)]
        result = sorted((rect for rect in result if not rect.is_empty),
                        key=lambda rect: rect.get_area(), reverse=True)[:_MAX_CLIP_RECTS]
    return result


class _InkProtection:
    """Bounded raster analysis produces vector clips, never a page image.

    Small original tiles are reused only within this page. Flat-background tiles
    protect actual ink plus a 1.5-point guard. Uncertain/image/gradient tiles
    protect complete glyph boxes. No rendered pixels are embedded in the PDF.
    """
    def __init__(self, page: fitz.Page, glyphs: list[dict]):
        self.page = page
        self.glyphs = [fitz.Rect(g["pdf_bbox"]) for g in glyphs if g["text"].strip()]
        self.bins = {}
        self.wide = []
        for index, rect in enumerate(self.glyphs):
            xs = range(math.floor(rect.x0 / _MASK_TILE), math.floor(rect.x1 / _MASK_TILE) + 1)
            ys = range(math.floor(rect.y0 / _MASK_TILE), math.floor(rect.y1 / _MASK_TILE) + 1)
            if len(xs) * len(ys) > 1024:
                self.wide.append(index)
                continue
            for x in xs:
                for y in ys:
                    self.bins.setdefault((x, y), []).append(index)
        self.tiles = {}
        self.used = 0
        self.marks = 0

    def _tile(self, x: int, y: int):
        from collections import Counter
        key = (x, y)
        if key in self.tiles:
            return self.tiles[key]
        if len(self.tiles) >= _MAX_MASK_TILES:
            return None  # cache/work bound: use conservative text-box protection
        rect = fitz.Rect(x * _MASK_TILE, y * _MASK_TILE, (x + 1) * _MASK_TILE, (y + 1) * _MASK_TILE) & self.page.rect
        if rect.is_empty:
            return None
        pix = self.page.get_pixmap(matrix=fitz.Matrix(_MASK_SCALE, _MASK_SCALE), clip=rect,
                                   colorspace=fitz.csRGB, alpha=False, annots=True)
        data = pix.samples
        color, count = Counter(data[i:i+3] for i in range(0, len(data), 3)).most_common(1)[0]
        tile = (pix.x, pix.y, pix.width, pix.height, data, color, count >= pix.width * pix.height * .4)
        if len(self.tiles) < _MAX_MASK_TILES:
            self.tiles[key] = tile
        return tile

    def rectangles(self, circle: fitz.Rect) -> list[fitz.Rect]:
        bounds = circle & self.page.rect
        nearby = set(self.wide)
        for x in range(math.floor((bounds.x0 - 1) / _MASK_TILE), math.floor((bounds.x1 + 1) / _MASK_TILE) + 1):
            for y in range(math.floor((bounds.y0 - 1) / _MASK_TILE), math.floor((bounds.y1 + 1) / _MASK_TILE) + 1):
                nearby.update(self.bins.get((x, y), []))
        boxes = [self.glyphs[i] + (-.5, -.5, .5, .5) for i in nearby
                 if (self.glyphs[i] + (-.5, -.5, .5, .5)).intersects(bounds)]
        self.marks += 1
        width = math.ceil(bounds.x1 * _MASK_SCALE) - math.floor(bounds.x0 * _MASK_SCALE)
        height = math.ceil(bounds.y1 * _MASK_SCALE) - math.floor(bounds.y0 * _MASK_SCALE)
        if (bounds.is_empty or width * height > 160000 or self.marks > _MAX_MASK_MARKS
                or self.used >= _MAX_PAGE_CLIP_RECTS):
            return _complement_rectangles(bounds, boxes)
        x0, y0 = math.floor(bounds.x0 * _MASK_SCALE), math.floor(bounds.y0 * _MASK_SCALE)
        protected = bytearray(width * height)
        for box in boxes:
            box &= bounds
            sx, ex = max(x0, math.floor(box.x0 * _MASK_SCALE)), min(x0 + width, math.ceil(box.x1 * _MASK_SCALE))
            sy, ey = max(y0, math.floor(box.y0 * _MASK_SCALE)), min(y0 + height, math.ceil(box.y1 * _MASK_SCALE))
            found_ink = False
            uncertain = False
            for py in range(sy, ey):
                for px in range(sx, ex):
                    tile = self._tile(px // (_MASK_TILE * _MASK_SCALE), py // (_MASK_TILE * _MASK_SCALE))
                    if tile is None or not tile[-1]:
                        uncertain = True
                        break
                    tx, ty, tw, th, samples, background, stable = tile
                    offset = ((py - ty) * tw + px - tx) * 3
                    ink = samples[offset:offset+3] != background
                    if ink:
                        found_ink = True
                        # Guard both antialias edges and the tiny differences
                        # between PDF engines' glyph rasterization.
                        for yy in range(max(y0, py - 6), min(y0 + height, py + 7)):
                            start, stop = max(x0, px - 6), min(x0 + width, px + 7)
                            protected[(yy - y0) * width + start - x0:(yy - y0) * width + stop - x0] = b'\1' * (stop - start)
                if uncertain:
                    break
            if uncertain or not found_ink:
                # Font substitution can differ between engines: extracted text
                # which MuPDF cannot paint may still be visible in a viewer.
                for py in range(sy, ey):
                    protected[(py - y0) * width + sx - x0:(py - y0) * width + ex - x0] = b'\1' * (ex - sx)
        # Merge identical horizontal runs vertically; path complexity is capped.
        active, finished = {}, []
        for y in range(height):
            runs, x = [], 0
            while x < width:
                if protected[y * width + x]:
                    x += 1
                    continue
                start = x
                while x < width and not protected[y * width + x]:
                    x += 1
                runs.append((start, x))
            next_active = {}
            for run in runs:
                next_active[run] = active.pop(run, y)
            finished.extend((start, top, end, y) for (start, end), top in active.items())
            active = next_active
            if len(finished) + len(active) > _MAX_CLIP_RECTS:
                return _complement_rectangles(bounds, boxes)
        finished.extend((start, top, end, height) for (start, end), top in active.items())
        self.used += len(finished)
        return [fitz.Rect((x0 + a) / _MASK_SCALE, (y0 + b) / _MASK_SCALE,
                          (x0 + c) / _MASK_SCALE, (y0 + d) / _MASK_SCALE) for a, b, c, d in finished]


def _paint_circle(page: fitz.Page, circle: fitz.Rect, rgb: tuple, opacity: float,
                  *, overlay: bool, clip_rects: list[fitz.Rect] | None = None):
    # PyMuPDF's Shape matrix on a rotated, cropped page can omit the crop
    # translation. Paint in unrotated coordinates and restore /Rotate without
    # changing the original content, crop box or glyph coordinates.
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        shape = page.new_shape()
        shape.draw_oval(circle)
        shape.finish(color=None, fill=rgb, fill_opacity=opacity)
        if clip_rects is not None:
            transform = ~page.transformation_matrix
            path = []
            for rect in clip_rects:
                pdf_rect = rect * transform
                path.append(f"{pdf_rect.x0:.5f} {pdf_rect.y0:.5f} {pdf_rect.width:.5f} {pdf_rect.height:.5f} re")
            # Empty clip is intentional if the circle is entirely protected.
            # Disjoint positive runs prevent overlaps reopening original ink.
            shape.totalcont = "q\nn\n" + "\n".join(path) + "\nW n\n" + shape.totalcont + "\nQ\n"
        shape.commit(overlay=overlay)
    finally:
        if rotation:
            page.set_rotation(rotation)


def _paint_marks(document: fitz.Document, data: bytes, job: dict, rgb: tuple,
                 page_numbers: set[int] | None = None):
    original = None
    try:
        for page_data in job["pages"]:
            if page_numbers is not None and page_data["number"] not in page_numbers:
                continue
            marks = [m for m in job["marks"] if m["page"] == page_data["number"]]
            if not marks:
                continue
            page = document[page_data["number"] - 1]
            glyphs = {g["id"]: g for g in page_data["glyphs"]}
            # Drawing logs use unrotated page coordinates, like pdf_bbox.
            fills = [fitz.Rect(box) for kind, box in page.get_bboxlog()
                     if kind in ("fill-path", "fill-image", "fill-shade")]
            protector = None
            for mark in marks:
                circle = circle_for(glyphs[mark["glyph_id"]], mark, job["settings"], page)
                blocked = any(box.intersects(circle) for box in fills)
                clip_rects = None
                if blocked:
                    if original is None:
                        original = fitz.open(stream=data, filetype="pdf")
                    if protector is None:
                        source_page = original[page.number]
                        source_page.set_rotation(0)
                        protector = _InkProtection(source_page, page_data["glyphs"])
                    clip_rects = protector.rectangles(circle)
                _paint_circle(page, circle, rgb, job["settings"]["opacity"],
                              overlay=blocked, clip_rects=clip_rects)
    finally:
        if original is not None:
            original.close()


def marked_document(data: bytes, job: dict, attach_log: bool = False,
                    *, page_numbers: set[int] | None = None) -> fitz.Document:
    """Paint selected one-based pages for previews, or every page for export."""
    document = fitz.open(stream=data, filetype="pdf")
    color = job["settings"]["color"].lstrip("#")
    rgb = tuple(int(color[i:i+2], 16) / 255 for i in (0, 2, 4))
    glyphs = {g["id"]: g for p in job["pages"] for g in p["glyphs"]}
    occurrences = {o["id"]: o for o in job["occurrences"]}
    _paint_marks(document, data, job, rgb, page_numbers)
    for mark in job["marks"]:
        if page_numbers is not None and mark["page"] not in page_numbers:
            continue
        page = document[mark["page"] - 1]
        glyph = glyphs[mark["glyph_id"]]
        circle = circle_for(glyph, mark, job["settings"], page)
        # A nonpainting Circle preserves the original review rectangle, color,
        # opacity and popup metadata. All visible paint is common page content.
        annotation = page.add_circle_annot(circle)
        # None means "leave unchanged" in PyMuPDF and would preserve the
        # circle annotation's default red stroke. An empty sequence removes it.
        annotation.set_colors(stroke=[], fill=rgb)
        annotation.set_border(width=0)
        annotation.set_opacity(job["settings"]["opacity"])
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
        ap_type, ap_value = document.xref_get_key(annotation.xref, "AP/N")
        if ap_type != "xref":
            raise ValueError("표시의 출처 주석 모양을 안전하게 만들 수 없습니다.")
        # Keep a valid, explicit empty appearance. Merely clearing /IC or
        # removing /AP can make a viewer regenerate a painted Circle.
        document.update_stream(int(ap_value.split()[0]), b"q\nQ\n")
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
