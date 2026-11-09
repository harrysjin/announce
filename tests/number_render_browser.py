"""Local numeric-reading and shared PDF/PNG rendering integration regression.

Run after `npm run build`:
    python3 tests/number_render_browser.py --output test-results/number-render-browser

The loopback-only fixture server replays captured official dictionary records.
No deployed website or live dictionary request is used. This generated PDF is
an integration fixture, not a linguistic benchmark or a macOS viewer test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def serve_fixture(port):
    import uvicorn
    import backend.main as backend
    from test_numeric_api import NumericReplay

    os.environ["STDICT_API_KEY"] = "local-numeric-fixture-only"
    backend.DictionaryClient = NumericReplay
    uvicorn.run(backend.app, host="127.0.0.1", port=port, log_level="warning")


def source_pdf():
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    # Opaque white paint exercises the fallback that must protect dark glyphs.
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_text((72, 55), "숫자와 한글 장음", fontname="korea", fontsize=22)
    for i, text in enumerate(["24", "2명", "4개", "5개", "20", "2번", "10:25", "010-2456-7788"]):
        page.insert_text((72, 115 + i * 75), text, fontname="korea", fontsize=26)
    # Existing document metadata and artwork must survive added marks. This
    # reserved example URI is never opened and is not dictionary evidence.
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(400, 730, 540, 755),
                      "uri": "https://example.invalid/original-link"})
    original_note = page.add_rect_annot(fitz.Rect(430, 45, 545, 70))
    original_note.set_colors(stroke=(.4, .4, .4))
    original_note.set_info(title="원문 주석", content="원문에 있던 검토 주석")
    original_note.update()
    result = document.tobytes()
    document.close()
    return result


def run_browser(base, output):
    from backend.pdf import extract, preservation_signature

    original = source_pdf()
    (output / "original.pdf").write_bytes(original)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="networkidle")
        with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as uploaded:
            page.get_by_label("PDF 업로드").set_input_files({"name": "숫자와_선명도.pdf", "mimeType": "application/pdf", "buffer": original})
        job = uploaded.value.json()
        endpoint = base + "/api/jobs/" + job["id"]

        def state():
            return page.request.get(endpoint).json()

        def wait_preview():
            page.wait_for_function("document.querySelector('.pdf-paper')?.getAttribute('aria-busy') === 'false'")

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = state()
            if job["analysis"]["state"] == "complete":
                break
            assert job["analysis"]["state"] != "error", job["analysis"]
            page.wait_for_timeout(150)
        assert job["analysis"]["state"] == "complete", job["analysis"]
        (output / "initial-analysis.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
        wait_preview()
        words = {o["text"]: o for o in job["occurrences"]}
        for target in ("24", "2명", "4개", "5개", "20", "2번", "10:25", "010-2456-7788"):
            assert target in words, (target, list(words))
            assert "numeric" in words[target], target
        assert words["24"]["status"] == "unresolved"
        assert words["2번"]["status"] == "unresolved" and len(words["2번"]["numeric"]["options"]) >= 2
        assert words["010-2456-7788"]["status"] == "unresolved"
        assert words["10:25"]["numeric"]["kind"] == "time"
        for target in ("24", "2번", "10:25", "010-2456-7788"):
            assert words[target]["status"] == "unresolved"
            assert not any(m.get("occurrence_id") == words[target]["id"] for m in job["marks"])
        for target, reading, pronunciation in (("2명", "두 명", "두ː"), ("4개", "네 개", "네ː")):
            assert words[target]["status"] == "long"
            numeric = words[target]["numeric"]
            assert numeric["choice_source"] == "automatic"
            assert next(o for o in numeric["options"] if o["id"] == numeric["selected_option_id"])["reading"] == reading
            assert any(pronunciation in c["pronunciations"] for c in words[target]["candidates"])
            assert any(m["glyph_id"] == words[target]["glyph_ids"][0] for m in job["marks"])
        assert words["5개"]["status"] == "none"
        assert words["5개"]["numeric"]["selected_option_id"] == "native"
        assert any(c["word"] == "다섯" and c["pronunciations"] == ["다섣"] for c in words["5개"]["candidates"])
        assert not any(m["glyph_id"] in words["5개"]["glyph_ids"] for m in job["marks"])
        for circle in page.locator(".mark-layer .mark > circle:first-child").all():
            expect(circle).to_have_attribute("fill", "transparent")
        page.get_by_role("button", name="검토 목록").click()

        def open_word(target):
            heading = page.locator(".occurrence-heading").filter(has=page.get_by_text(target, exact=True))
            if heading.get_attribute("aria-expanded") != "true":
                heading.click()
            return heading.locator("..")

        def select_sino(target):
            item = next(o for o in state()["occurrences"] if o["text"] == target)
            option = next(o for o in item["numeric"]["options"] if o["system"] == "sino")
            occurrence = open_word(target)
            card = occurrence.locator(".numeric-option").filter(has=page.get_by_text(option["reading"], exact=True))
            with page.expect_response(lambda r: r.url.endswith("/reading") and r.request.method == "POST") as response:
                card.get_by_role("button", name="이 읽기 선택", exact=True).click()
            assert response.value.status == 200
            wait_preview()
            return next(o for o in state()["occurrences"] if o["text"] == target)

        selected24 = select_sino("24")
        selected_state = state()
        assert selected24["numeric"]["choice_source"] == "user"
        assert selected24["status"] == "long" and selected24["evidence_type"] == "numeric_rule"
        numeric_mark = next(m for m in selected_state["marks"] if m.get("occurrence_id") == selected24["id"])
        assert numeric_mark["glyph_id"] == selected24["glyph_ids"][0], "二 length belongs to original digit 2"
        occurrence = open_word("24")
        expect(occurrence.get_by_text("읽기 선택", exact=True)).to_be_visible()
        expect(occurrence.get_by_text("규칙 판정", exact=True)).to_be_visible()
        expect(occurrence.get_by_role("region", name="숫자 읽기와 위치 대응")).to_be_visible()
        mapping = occurrence.locator(".numeric-option.chosen details")
        mapping.locator("summary").click()
        expect(mapping.get_by_text("2 (1번째 글자)", exact=True)).to_be_visible()
        assert occurrence.locator(".candidate-query-time").count() > 0

        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        wait_preview()
        assert next(o for o in state()["occurrences"] if o["text"] == "24")["status"] == "unresolved"
        assert not any(m.get("occurrence_id") == selected24["id"] for m in state()["marks"])
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="다시 실행", exact=True).click()
        wait_preview()
        restored24 = next(o for o in state()["occurrences"] if o["text"] == "24")
        for key in ("numeric", "source", "status", "evidence_type", "length_evidence", "applied_rules", "coverage", "reason"):
            assert restored24[key] == selected24[key], key
        selected20 = select_sino("20")
        assert selected20["status"] == "long"
        assert any(m["glyph_id"] == selected20["glyph_ids"][0] for m in state()["marks"])
        open_word("2번")
        expect(page.locator(".occurrence.active .numeric-option")).to_have_count(len(words["2번"]["numeric"]["options"]))
        page.screenshot(path=str(output / "desktop-reading-options.png"), full_page=True)

        def assert_export(label):
            wait_preview()
            before_download = state()
            img_src = page.locator(".pdf-paper img").get_attribute("src")
            query = parse_qs(urlparse(img_src).query)
            assert query["marked"] == ["true"] and query["revision"] == [str(before_download["revision"])]
            png = page.request.get(base + img_src).body()
            (output / f"{label}-preview.png").write_bytes(png)
            page.get_by_role("button", name="다운로드", exact=True).click()
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="장음 표시 PDF").click()
            filename = output / f"{label}.pdf"
            downloaded.value.save_as(filename)
            preview = fitz.Pixmap(png)
            zoom = float(query["scale"][0])
            with fitz.open(stream=original, filetype="pdf") as source, fitz.open(filename) as result:
                assert preservation_signature(source) == preservation_signature(result)
                assert extract(original)[0] == extract(filename.read_bytes())[0]
                assert [f[1:] for f in source[0].get_fonts(full=True)] == [f[1:] for f in result[0].get_fonts(full=True)]
                exported_links = [{k: v for k, v in link.items() if k != "xref"} for link in result[0].get_links()]
                for link in source[0].get_links():
                    assert {k: v for k, v in link.items() if k != "xref"} in exported_links
                result_page = result[0]
                rendered = result_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                assert (preview.width, preview.height, preview.n) == (rendered.width, rendered.height, rendered.n)
                assert preview.samples == rendered.samples, "Web PNG and exported PDF must share visible rendering"
                result_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / f"{label}-export.png")
                original_pixels = source[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).samples
                actual = rendered.samples
                ink = [i for i in range(0, len(original_pixels), 3) if original_pixels[i:i + 3] != b"\xff\xff\xff"]
                assert len(ink) > 100
                assert all(original_pixels[i:i + 3] == actual[i:i + 3] for i in ink), "Original ink, including antialias edges, must remain unchanged"
                assert original_pixels != actual, "Markers must be visible over opaque white page paint"
                glyphs = {g["id"]: g for p in before_download["pages"] for g in p["glyphs"]}
                def annotation_signature(annotation):
                    return (annotation.type, tuple(annotation.rect), annotation.colors, annotation.opacity,
                            annotation.flags, annotation.info)
                source_annotations = [annotation_signature(a) for a in source[0].annots()]
                existing_annotations = [annotation_signature(a) for a in result_page.annots() if a.info["title"] != "장음 표시"]
                assert source_annotations == existing_annotations
                annotations = [a for a in result_page.annots() if a.info["title"] == "장음 표시"]
                assert len(annotations) == len(before_download["marks"])
                for mark, annotation in zip(before_download["marks"], annotations):
                    rect = fitz.Rect(glyphs[mark["glyph_id"]]["pdf_bbox"])
                    target = (rect.tl + rect.br) / 2 + fitz.Point(mark["dx"], mark["dy"])
                    center = (annotation.rect.tl + annotation.rect.br) / 2
                    assert abs(center.x - target.x) < .01 and abs(center.y - target.y) < .01
                assert result.embfile_count() > 0
            return before_download

        automatic = assert_export("automatic-and-selected")
        page.get_by_role("button", name="직접 표시", exact=True).click()
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.locator(".glyph-target").filter(has=page.locator("title").filter(has_text="숫에 장음 표시")).click()
        wait_preview()
        manual = state()
        assert len(manual["marks"]) == len(automatic["marks"]) + 1
        manual_mark = next(m for m in manual["marks"] if m["id"] not in {old["id"] for old in automatic["marks"]})
        assert manual_mark["source"] == "manual"
        page.get_by_role("button", name="표시 설정", exact=True).click()
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="표시 삭제", exact=True).click()
        wait_preview()
        assert len(state()["marks"]) == len(automatic["marks"])
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        wait_preview()
        assert state()["marks"] == manual["marks"]
        page.locator(".glyph-target").filter(has=page.locator("title").filter(has_text="숫에 장음 표시")).click()
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="표시 오른쪽 이동", exact=True).click()
        wait_preview()
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.locator("#size").press("ArrowRight")
        wait_preview()
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.locator("#opacity").press("ArrowLeft")
        wait_preview()
        color = page.get_by_role("textbox", name="HEX 색상")
        color.fill("#F2D4DC")
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            color.press("Tab")
        wait_preview()
        edited = assert_export("manual-adjusted-settings")
        assert edited["settings"] == {"color": "#F2D4DC", "opacity": .55, "size": 1.05}
        assert next(m for m in edited["marks"] if m["id"] == manual_mark["id"])["dx"] == 1
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        wait_preview()
        assert state()["settings"]["color"] == "#CEE4F5"
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="다시 실행", exact=True).click()
        wait_preview()
        assert state()["settings"] == edited["settings"]
        repeated = assert_export("repeated-download")
        assert repeated["settings"] == edited["settings"] and repeated["marks"] == edited["marks"]
        page.get_by_role("button", name="확대", exact=True).click()
        wait_preview()
        expect(page.locator(".zoom-value")).to_have_text("110%")
        zoomed = assert_export("zoomed-download")
        assert zoomed["marks"] == edited["marks"]
        page.reload(wait_until="networkidle")
        wait_preview()
        assert state()["marks"] == edited["marks"] and state()["settings"] == edited["settings"]
        page.screenshot(path=str(output / "desktop-rendered.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="설정 및 검토", exact=True).click()
        page.get_by_role("button", name="검토 목록").click()
        open_word("24")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile review overflow"
        page.screenshot(path=str(output / "mobile-reading.png"), full_page=True)
        page.get_by_role("button", name="대본", exact=True).click()
        wait_preview()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile PDF overflow"
        page.screenshot(path=str(output / "mobile-rendered.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1050})
        page.get_by_role("button", name="직접 표시", exact=True).click()
        page.locator(f'.glyph-target[data-glyph-id="{selected24["glyph_ids"][0]}"]').click()
        before_numeric_move = next(m for m in state()["marks"] if m.get("occurrence_id") == selected24["id"])
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="표시 오른쪽 이동", exact=True).click()
        wait_preview()
        moved_state = state()
        moved_occurrence = next(o for o in moved_state["occurrences"] if o["id"] == selected24["id"])
        (output / "numeric-moved-state.json").write_text(json.dumps(moved_state, ensure_ascii=False, indent=2))
        moved_mark = next(m for m in moved_state["marks"] if m["id"] == before_numeric_move["id"])
        assert moved_mark["dx"] == before_numeric_move["dx"] + 1
        assert moved_occurrence["reason_code"] == "manual" and moved_occurrence["source"] == "manual"
        assert moved_occurrence["numeric"]["choice_source"] == "user"
        page.get_by_role("button", name="검토 목록").click()
        moved_row = open_word("24")
        expect(moved_row.get_by_text("직접 수정", exact=True)).to_be_visible()
        expect(moved_row.get_by_text("사용자가 선택한 읽기입니다. 사전 근거와 규칙 판정을 아래에 구분합니다.", exact=True)).to_be_visible()
        final = assert_export("numeric-mark-manual-moved")
        page.screenshot(path=str(output / "desktop-moved-numeric-provenance.png"), full_page=True)
        assert not errors, errors
        (output / "analysis.json").write_text(json.dumps(final, ensure_ascii=False, indent=2))
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "test-results/number-render-browser")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--serve-fixture", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_fixture:
        serve_fixture(args.port)
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    with (output / "fixture-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-fixture", "--port", str(args.port)], cwd=REPOSITORY, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(timeout=1, trust_env=True) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("fixture server failed; see fixture-server.log")
                    try:
                        if client.get(base + "/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("fixture server not ready")
            run_browser(base, output)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("PASS: numeric choice/ambiguity/alignment, native counters, time/phone extraction, shared PNG/PDF pixels, all original ink/text/boxes/fonts/links/annotations, manual add/delete/move/settings/history, repeated download/zoom/reload, mobile layout; no live API or JS errors")
    print("Artifacts:", output)


if __name__ == "__main__":
    main()
