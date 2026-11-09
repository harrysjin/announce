"""Natural-sentence Chromium regression using captured official API records.

Run after ``npm run build`` from the repository root::
    python3 tests/site_sentence_browser.py

This starts a loopback fixture server, replaces its dictionary client with the
full CombinedReplay, and stops it on exit. It does not call the deployed site
or the live dictionary. The generated PDF is a regression input, not a general
linguistic benchmark or a macOS Preview / Adobe Reader compatibility result.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY / "tests"))
ENGINE_VERSION = "2026.11.09.2"
LINES = (
    "보도부를 통해 2명을 선발하며 4개를 준비합니다.",
    "2 번 확인하고 2 명을 뽑습니다.",
    "2026년에는 보도부가 활동합니다.",
    "5개도 준비합니다.",
    "사회혁신융합전공 말의 알아 끌어 첫눈 밀물",
    "24 20 2번 10:25 010-2456-7788",
    "4개구리 2명예",
    "2 명예 회장 4 개발 계획 4 개구리",
)


def serve_fixture(port: int):
    import uvicorn
    import backend.main as backend
    from test_site_reaudit import CombinedReplay

    # A non-secret placeholder makes the local UI enable automatic analysis.
    # Every lookup is intercepted by the replay client before uploads begin.
    os.environ["STDICT_API_KEY"] = "local-sentence-fixture-only"
    backend.DictionaryClient = CombinedReplay
    uvicorn.run(backend.app, host="127.0.0.1", port=port, log_level="warning")


def source_pdf():
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_text((42, 60), "문장 속 형태소와 숫자 장음", fontname="korea", fontsize=19)
    for index, line in enumerate(LINES):
        page.insert_text((42, 125 + index * 73), line, fontname="korea", fontsize=15)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(410, 770, 550, 795),
                      "uri": "https://example.invalid/original-document"})
    annotation = page.add_rect_annot(fitz.Rect(430, 40, 550, 65))
    annotation.set_colors(stroke=(.4, .4, .4))
    annotation.set_info(title="원문 주석", content="문장 회귀 검사 원본 주석")
    annotation.update()
    result = document.tobytes()
    document.close()
    return result


def assert_captured_sources(job):
    """Gate candidate provenance against records, never an invented lexicon."""
    from test_site_reaudit import CombinedReplay

    replay = CombinedReplay()
    entries = {**replay.numeric.entries, **replay.morphology.entries}
    records = {}
    for candidates in entries.values():
        for candidate in candidates:
            records.setdefault(candidate["id"], []).append(candidate)
    keys = ("id", "word", "pos", "origin", "pronunciations", "definition", "url", "queried_at")
    count = 0
    for occurrence in job["occurrences"]:
        candidates = list(occurrence.get("candidates", []))
        for component in occurrence.get("component_results", []):
            candidates += component.get("candidates", [])
        for candidate in candidates:
            assert candidate["id"] in records, (occurrence["text"], candidate["id"])
            projection = {key: candidate.get(key) for key in keys}
            assert any(projection == {key: source.get(key) for key in keys}
                       for source in records[candidate["id"]]), projection
            assert candidate["url"].startswith("https://stdict.korean.go.kr/")
            assert candidate["queried_at"]
            count += 1
        assert set(occurrence.get("evidence_candidates", [])) <= {c["id"] for c in candidates}
        for evidence in occurrence.get("length_evidence", []):
            assert evidence["candidate_id"] in records, evidence
            readings = evidence["pronunciations"]
            sources = records[evidence["candidate_id"]]
            assert any(readings == source["pronunciations"] or
                       any(readings == inflection["pronunciations"]
                           for inflection in source.get("inflections", []))
                       for source in sources), evidence
    assert count > 15, "The full captured candidate sets must participate"
    return count


def run_browser(base: str, output: Path):
    from backend.pdf import extract, preservation_signature

    original = source_pdf()
    (output / "original.pdf").write_bytes(original)
    initial_pages, _ = extract(original)
    errors, external_requests = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def local_only(route):
            if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                external_requests.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("http://**/*", local_only)
        page.route("https://**/*", local_only)
        page.goto(base, wait_until="networkidle")
        health = page.request.get(base + "/api/health").json()
        assert health["engine_version"] == ENGINE_VERSION, health
        assert {"particle_boundaries", "numeric_readings", "original_glyph_alignment"} <= set(health["capabilities"])
        with page.expect_response(lambda response: response.url.endswith("/api/jobs") and response.request.method == "POST") as uploaded:
            page.get_by_label("PDF 업로드").set_input_files({
                "name": "문장_형태소_숫자_통합.pdf", "mimeType": "application/pdf", "buffer": original})
        endpoint = base + "/api/jobs/" + uploaded.value.json()["id"]

        def state():
            result = page.request.get(endpoint)
            assert result.status == 200
            return result.json()

        def wait_complete():
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                current = state()
                if current["analysis"]["state"] == "complete":
                    return current
                assert current["analysis"]["state"] != "error", current["analysis"]
                page.wait_for_timeout(100)
            raise AssertionError(current["analysis"])

        def wait_preview():
            revision = state()["revision"]
            page.wait_for_function("""revision => {
              const paper = document.querySelector('.pdf-paper');
              const image = paper?.querySelector('img');
              return paper?.getAttribute('aria-busy') === 'false' && image?.naturalWidth > 0 &&
                new URL(image.src).searchParams.get('revision') === String(revision);
            }""", arg=revision)

        def occurrence(text, *, context=None, index=0, job=None):
            matches = [item for item in (job or state())["occurrences"]
                       if item["text"] == text and (context is None or context in item.get("context", ""))]
            assert len(matches) > index, (text, context, [item["text"] for item in state()["occurrences"]])
            return matches[index]

        def marks_for(item, job=None):
            return [mark for mark in (job or state())["marks"] if mark.get("occurrence_id") == item["id"]]

        def open_occurrence(item):
            heading = page.locator(f'.occurrence-heading[aria-controls="detail-{item["id"]}"]')
            if heading.get_attribute("aria-expanded") != "true":
                heading.click()
            return heading.locator("..")

        def history(button):
            with page.expect_response(lambda response: response.url.endswith("/state") and response.request.method == "PATCH"):
                page.get_by_role("button", name=button, exact=True).click()
            wait_preview()
            return state()

        job = wait_complete()
        wait_preview()
        assert job["pages"] == initial_pages, "Analysis must retain original glyph IDs/text/coordinates"
        expect(page.get_by_label("분석 엔진 버전")).to_contain_text(ENGINE_VERSION)
        (output / "initial-analysis.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
        source_count = assert_captured_sources(job)
        expected = {"보도부를": "long", "2명을": "long", "4개를": "long", "2026년에는": "long",
                    "보도부가": "long", "5개도": "none", "선발하며": "unresolved",
                    "사회혁신융합전공": "unresolved", "말의": "unresolved", "알아": "none",
                    "끌어": "long", "첫눈": "none", "밀물": "none", "24": "unresolved",
                    "20": "unresolved", "2번": "unresolved", "10:25": "unresolved",
                    "010-2456-7788": "unresolved", "4개구리": "unresolved", "2명예": "unresolved"}
        for text, status in expected.items():
            item = occurrence(text, job=job)
            assert item["status"] == status, (text, item)
            positions = [mark["glyph_id"] for mark in marks_for(item, job)]
            assert positions == ([item["glyph_ids"][0]] if status == "long" else []), (text, positions)
        for text in ("보도부를", "보도부가"):
            item = occurrence(text, job=job)
            assert item["morphology"]["lemma"] == "보도부"
            assert "rule" in item["evidence_type"] and item["applied_rules"]
            assert item["pronunciation_scope"] == "보도부"
        for text, system in (("2명을", "native"), ("4개를", "native"), ("5개도", "native"), ("2026년에는", "sino")):
            item = occurrence(text, job=job)
            option = next(option for option in item["numeric"]["options"] if option["id"] == item["numeric"]["selected_option_id"])
            assert option["system"] == system
            assert item["coverage"]["coordinate_system"] == "characters"
            assert item["coverage"]["checked_ranges"] == [[0, len(text)]]
            assert all(0 <= mapping["source_start"] <= mapping["source_end"] <= len(text) for mapping in option["alignment"])
        spaced = [item for item in job["occurrences"] if item["text"] == "2" and "뽑습니다" in item.get("context", "")]
        assert len(spaced) == 2, spaced
        ordinal, counter = spaced
        assert (ordinal["status"], counter["status"]) == ("unresolved", "long")
        assert marks_for(ordinal, job) == []
        assert [mark["glyph_id"] for mark in marks_for(counter, job)] == [counter["glyph_ids"][0]]
        for item, unit in ((ordinal, "번"), (counter, "명")):
            for option in item["numeric"]["options"]:
                assert any(part["text"] == unit for part in option["segments"] if part["role"] == "unit")
        option = next(option for option in counter["numeric"]["options"] if option["id"] == counter["numeric"]["selected_option_id"])
        outside = [mapping for mapping in option["alignment"] if mapping.get("source_location") == "following_context"]
        assert outside and all(mapping["source_start"] == mapping["source_end"] == len(counter["text"]) and not mapping["exact"] for mapping in outside)
        for item in job["occurrences"]:
            if item["text"] in ("2", "4") and "명예" in item.get("context", ""):
                assert item["status"] == "unresolved" and not marks_for(item, job), item
        page.screenshot(path=str(output / "desktop-natural-sentences.png"), full_page=True)
        page.get_by_role("button", name="검토 목록").click()
        counter_row = open_occurrence(counter)
        mapping_details = counter_row.locator(".numeric-option.chosen details")
        mapping_details.locator("summary").click()
        rows = mapping_details.locator("tbody tr").all_inner_texts()
        assert len(rows) == len(option["alignment"])
        assert not any(re.search(r"\((\d+)–(\d+)번째 글자\)", row) and
                       int(re.search(r"\((\d+)–(\d+)번째 글자\)", row)[1]) >
                       int(re.search(r"\((\d+)–(\d+)번째 글자\)", row)[2]) for row in rows), rows
        outside_rows = [rows[index] for index, mapping in enumerate(option["alignment"]) if mapping.get("source_location") == "following_context"]
        assert all("뒤 문맥" in row or "표기 밖" in row or "원문 밖" in row for row in outside_rows), outside_rows
        assert counter_row.locator(".candidate-query-time").count() > 0
        page.screenshot(path=str(output / "desktop-spaced-counter-evidence.png"), full_page=True)

        compound = open_occurrence(occurrence("사회혁신융합전공", job=job))
        expect(compound.get_by_text("일부 범위만 확인 · 전체 장단 미확정", exact=True)).to_be_visible()
        expect(compound.get_by_role("region", name="구성요소별 근거")).to_be_visible()
        components = compound.locator(".component-evidence details")
        assert components.count() >= 4
        components.first.locator("summary").click()
        assert components.first.locator(".component-candidate a").count() > 0

        derived = occurrence("선발하며", job=job)
        assert derived["morphology"]["lemma"] == "선발하다"
        assert {candidate["origin"] for candidate in derived["candidates"]} == {"先發 · 하다", "選拔 · 하다"}
        selected_candidate = next(candidate for candidate in derived["candidates"] if candidate["origin"] == "選拔 · 하다")
        derived_row = open_occurrence(derived)
        card = derived_row.locator(".candidate").filter(has_text=selected_candidate["definition"])
        with page.expect_response(lambda response: response.url.endswith("/candidate") and response.request.method == "POST"):
            card.get_by_role("button", name="이 의미 선택", exact=True).click()
        wait_preview()
        chosen_derived = occurrence("선발하며")
        assert chosen_derived["status"] == "long" and chosen_derived["source"] == "manual"
        assert chosen_derived["meaning_status"] == "user_selected" and chosen_derived["selected_candidate_id"] == selected_candidate["id"]
        assert chosen_derived["evidence_type"] == "rule_inference" and chosen_derived["applied_rules"]
        assert [mark["glyph_id"] for mark in marks_for(chosen_derived)] == [chosen_derived["glyph_ids"][0]]
        expect(derived_row.get_by_text("직접 수정", exact=True)).to_be_visible()
        assert derived_row.locator(".rule-source").count() == len(chosen_derived["applied_rules"])

        number = occurrence("24")
        sino = next(option for option in number["numeric"]["options"] if option["system"] == "sino")
        number_row = open_occurrence(number)
        number_card = number_row.locator(".numeric-option").filter(has=page.get_by_text(sino["reading"], exact=True))
        with page.expect_response(lambda response: response.url.endswith("/reading") and response.request.method == "POST") as selected_response:
            number_card.get_by_role("button", name="이 읽기 선택", exact=True).click()
        assert selected_response.value.status == 200
        wait_preview()
        chosen_number = occurrence("24")
        assert chosen_number["numeric"]["choice_source"] == "user" and chosen_number["status"] == "long"
        assert [mark["glyph_id"] for mark in marks_for(chosen_number)] == [chosen_number["glyph_ids"][0]], "24 marks original 2, never 4"
        expect(number_row.get_by_text("읽기 선택", exact=True)).to_be_visible()
        expect(number_row.get_by_text("규칙 판정", exact=True)).to_be_visible()
        undone = history("실행 취소")
        assert occurrence("24", job=undone)["status"] == "unresolved"
        assert not marks_for(number, undone)
        redone = history("다시 실행")
        for key in ("numeric", "source", "status", "evidence_type", "length_evidence", "applied_rules", "coverage", "reason"):
            assert occurrence("24", job=redone)[key] == chosen_number[key], key
        for key in ("source", "meaning_status", "reason", "reason_code", "evidence_type"):
            assert occurrence("보도부를", job=redone)[key] == occurrence("보도부를", job=job)[key], key
        assert occurrence("선발하며", job=redone) == chosen_derived
        page.screenshot(path=str(output / "desktop-reading-and-meaning-selected.png"), full_page=True)

        def assert_export(label):
            wait_preview()
            before_download = state()
            image_url = page.locator(".pdf-paper img").get_attribute("src")
            query = parse_qs(urlparse(image_url).query)
            assert query["marked"] == ["true"] and query["revision"] == [str(before_download["revision"])]
            png = page.request.get(base + image_url).body()
            (output / f"{label}-preview.png").write_bytes(png)
            page.get_by_role("button", name="다운로드", exact=True).click()
            with page.expect_download() as download:
                page.get_by_role("button", name="장음 표시 PDF").click()
            destination = output / f"{label}.pdf"
            download.value.save_as(destination)
            preview = fitz.Pixmap(png)
            zoom = float(query["scale"][0])
            with fitz.open(stream=original, filetype="pdf") as source, fitz.open(destination) as result:
                assert preservation_signature(source) == preservation_signature(result)
                assert extract(original)[0] == extract(destination.read_bytes())[0]
                assert [font[1:] for font in source[0].get_fonts(full=True)] == [font[1:] for font in result[0].get_fonts(full=True)]
                links = [{key: value for key, value in link.items() if key != "xref"} for link in result[0].get_links()]
                assert all({key: value for key, value in link.items() if key != "xref"} in links for link in source[0].get_links())
                result_page = result[0]
                rendered = result_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                assert (preview.width, preview.height, preview.n, preview.samples) == (rendered.width, rendered.height, rendered.n, rendered.samples), "Web preview and downloaded PDF rendering differ"
                result_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / f"{label}-export.png")
                ink = source[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).samples
                assert len(ink) == len(rendered.samples)
                assert all(ink[index:index + 3] == rendered.samples[index:index + 3]
                           for index in range(0, len(ink), 3) if ink[index:index + 3] != b"\xff\xff\xff"), "Original nonwhite ink changed"
                assert ink != rendered.samples, "Markers must remain visible over opaque white page paint"

                def annotation_signature(annotation):
                    return annotation.type, tuple(annotation.rect), annotation.colors, annotation.opacity, annotation.flags, annotation.info

                assert [annotation_signature(annotation) for annotation in source[0].annots()] == [annotation_signature(annotation) for annotation in result_page.annots() if annotation.info["title"] != "장음 표시"]
                annotations = [annotation for annotation in result_page.annots() if annotation.info["title"] == "장음 표시"]
                assert len(annotations) == len(before_download["marks"])
                glyphs = {glyph["id"]: glyph for pdf_page in before_download["pages"] for glyph in pdf_page["glyphs"]}
                for mark, annotation in zip(before_download["marks"], annotations):
                    rectangle = fitz.Rect(glyphs[mark["glyph_id"]]["pdf_bbox"])
                    target = (rectangle.tl + rectangle.br) / 2 + fitz.Point(mark["dx"], mark["dy"])
                    center = (annotation.rect.tl + annotation.rect.br) / 2
                    assert abs(center.x - target.x) < .01 and abs(center.y - target.y) < .01
                assert any(chosen_derived["applied_rules"][0]["url"] in annotation.info["content"] for annotation in annotations)
                assert result.embfile_count() > 0
            return before_download

        selected = assert_export("selected-readings-and-meaning")
        page.get_by_role("button", name="직접 표시", exact=True).click()
        page.locator(f'.glyph-target[data-glyph-id="{chosen_number["glyph_ids"][0]}"]').click()
        before_move = deepcopy(marks_for(chosen_number)[0])
        with page.expect_response(lambda response: response.url.endswith("/state") and response.request.method == "PATCH"):
            page.get_by_role("button", name="표시 오른쪽 이동", exact=True).click()
        wait_preview()
        moved = state()
        moved_number = occurrence("24", job=moved)
        moved_mark = next(mark for mark in moved["marks"] if mark["id"] == before_move["id"])
        assert moved_mark["dx"] == before_move["dx"] + 1 and moved_mark["source"] == "manual"
        assert moved_number["reason_code"] == "manual" and moved_number["source"] == "manual"
        through = occurrence("통해", job=job)
        with page.expect_response(lambda response: response.url.endswith("/state") and response.request.method == "PATCH"):
            page.locator(f'.glyph-target[data-glyph-id="{through["glyph_ids"][0]}"]').click()
        wait_preview()
        edited = state()
        manual_added = [mark for mark in edited["marks"] if mark["id"] not in {mark["id"] for mark in moved["marks"]}]
        assert len(manual_added) == 1 and manual_added[0]["source"] == "manual"
        with page.expect_response(lambda response: response.url.endswith("/analyze") and response.request.method == "POST"):
            page.get_by_role("button", name="장음 분석", exact=True).click()
        repeated = wait_complete()
        wait_preview()
        # Automatic marks may receive new IDs when recomputed; every user's
        # actual edit and choice must retain its exact marker state. All mark
        # geometry/provenance must also remain logically identical.
        edited_occurrences = {o["id"] for o in edited["occurrences"] if o.get("source") == "manual"}
        manual_before = {m["id"]: m for m in edited["marks"] if m.get("occurrence_id") in edited_occurrences}
        manual_after = {m["id"]: m for m in repeated["marks"] if m.get("occurrence_id") in edited_occurrences}
        assert manual_after == manual_before, "Reanalysis lost a user move, meaning, reading, or manually added mark"
        logical = lambda marks: {(m["page"], m["glyph_id"], m.get("occurrence_id")):
                                  {k: v for k, v in m.items() if k != "id"} for m in marks}
        assert logical(repeated["marks"]) == logical(edited["marks"]), "Reanalysis changed marker placement or evidence"
        assert occurrence("24", job=repeated) == moved_number
        assert occurrence("선발하며", job=repeated) == chosen_derived
        assert repeated["pages"] == initial_pages
        assert_captured_sources(repeated)
        page.reload(wait_until="networkidle")
        wait_preview()
        assert state()["marks"] == repeated["marks"]
        final = assert_export("manual-edits-reanalyzed-reloaded")
        assert final["marks"] == repeated["marks"]
        page.screenshot(path=str(output / "desktop-final.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="설정 및 검토", exact=True).click()
        page.get_by_role("button", name="검토 목록").click()
        open_occurrence(counter)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile review overflow"
        page.screenshot(path=str(output / "mobile-spaced-counter.png"), full_page=True)
        page.get_by_role("button", name="대본", exact=True).click()
        wait_preview()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile PDF overflow"
        page.screenshot(path=str(output / "mobile-pdf.png"), full_page=True)
        assert not errors, errors
        assert not external_requests, external_requests
        (output / "analysis.json").write_text(json.dumps(final, ensure_ascii=False, indent=2))
        (output / "summary.json").write_text(json.dumps({
            "engine_version": health["engine_version"], "build_commit": health["build_commit"],
            "dictionary_mode": "full captured official responses; offline replay",
            "source_candidates_checked": source_count, "initial_occurrences": len(job["occurrences"]),
            "initial_marks": len(job["marks"]), "selected_marks": len(selected["marks"]),
            "final_marks": len(final["marks"]), "javascript_errors": errors,
            "external_browser_requests": external_requests,
            "macos_preview": "UNRUN", "adobe_reader": "UNRUN",
        }, ensure_ascii=False, indent=2))
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "test-results/site-sentence-browser")
    parser.add_argument("--port", type=int, default=9004)
    parser.add_argument("--serve-fixture", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_fixture:
        serve_fixture(args.port)
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    started = time.monotonic()
    with (output / "fixture-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-fixture", "--port", str(args.port)], cwd=REPOSITORY, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(timeout=1, trust_env=True) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Fixture server failed; inspect fixture-server.log")
                    try:
                        if client.get(base + "/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("Fixture server did not become ready")
            run_browser(base, output)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print(f"PASS in {time.monotonic() - started:.1f}s: natural sentences, full source records, separate repeated/spaced counters, source glyph positions, user reading/meaning/history/manual edits, shared PDF rendering and preservation, desktop/mobile, visible release; no external requests or JS errors")
    print("Artifacts:", output)


if __name__ == "__main__":
    main()
