"""Local Chromium morphology workflow against audited dictionary snapshots.

Run after `npm run build`:
    python3 tests/morphology_browser.py --output test-results/morphology-browser

Starts a loopback-only fixture API and stops it on exit. This never contacts
the live dictionary or the deployed website. The fixture contains actual API
records; this is an integration regression, not a linguistic accuracy estimate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def serve_fixture(port: int):
    import uvicorn
    import backend.main as backend
    from test_morphology_regression import ReplayDictionary

    # The key is only used by the UI's configuration flag. The client factory
    # is replaced before any upload, so no credential or network API is used.
    os.environ["STDICT_API_KEY"] = "local-fixture-only"
    backend.DictionaryClient = ReplayDictionary
    uvicorn.run(backend.app, host="127.0.0.1", port=port, log_level="warning")


def document_bytes():
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    for i, word in enumerate(["선발하며", "보도부", "사회혁신융합전공", "알아", "끌어", "첫눈", "밀물", "말의"]):
        page.insert_text((72, 100 + i * 65), word, fontname="korea", fontsize=20)
    result = document.tobytes()
    document.close()
    return result


def run_browser(base: str, output: Path):
    from backend.pdf import extract, preservation_signature

    source = document_bytes()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(12000)
        page.goto(base, wait_until="networkidle")
        with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as uploaded:
            page.get_by_label("PDF 업로드").set_input_files({"name": "형태소_통합시험.pdf", "mimeType": "application/pdf", "buffer": source})
        job = uploaded.value.json()
        endpoint = base + "/api/jobs/" + job["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = page.request.get(endpoint).json()
            if job["analysis"]["state"] == "complete":
                break
            assert job["analysis"]["state"] != "error", job["analysis"]
            page.wait_for_timeout(150)
        assert job["analysis"]["state"] == "complete", job["analysis"]
        words = {o["text"]: o for o in job["occurrences"]}
        expected = {"선발하며": "unresolved", "보도부": "long", "사회혁신융합전공": "unresolved",
                    "알아": "none", "끌어": "long", "첫눈": "none", "밀물": "none", "말의": "unresolved"}
        assert {word: item["status"] for word, item in words.items()} == expected
        expect(page.locator(".mark-layer .mark")).to_have_count(2)
        page.get_by_role("button", name="검토 목록").click()

        def open_word(word):
            heading = page.locator(".occurrence-heading").filter(has=page.get_by_text(word, exact=True))
            if heading.get_attribute("aria-expanded") != "true":
                heading.click()
            return heading.locator("..")

        compound = open_word("사회혁신융합전공")
        expect(compound.get_by_text("일부 범위만 확인 · 전체 장단 미확정", exact=True)).to_be_visible()
        expect(compound.get_by_role("region", name="구성요소별 근거")).to_be_visible()
        details = compound.locator(".component-evidence details").first
        details.locator("summary").click()
        expect(details.get_by_text("기본형 사회 · NNG", exact=True)).to_be_visible()
        assert details.locator(".component-candidate a").count() > 0
        assert details.locator(".component-candidate small").first.inner_text().startswith("조회 ")
        page.screenshot(path=str(output / "desktop-compound.png"), full_page=True)

        derived = open_word("선발하며")
        choice = next(c for c in words["선발하며"]["candidates"] if c["origin"] == "選拔 · 하다")
        card = derived.locator(".candidate").filter(has_text=choice["definition"])
        with page.expect_response(lambda r: r.url.endswith("/candidate") and r.request.method == "POST"):
            card.get_by_role("button", name="이 의미 선택", exact=True).click()
        selected = page.request.get(endpoint).json()
        item = next(o for o in selected["occurrences"] if o["text"] == "선발하며")
        assert item["status"] == "long" and item["source"] == "manual"
        assert item["evidence_type"] == "rule_inference" and item["selected_candidate_id"] == choice["id"]
        assert len(item["applied_rules"]) > 0
        assert next(m for m in selected["marks"] if m.get("occurrence_id") == item["id"])["glyph_id"] == item["glyph_ids"][0]
        expect(page.locator(".mark-layer .mark")).to_have_count(3)
        expect(derived.get_by_text("직접 수정", exact=True)).to_be_visible()
        expect(derived.locator(".rule-source")).to_have_count(len(item["applied_rules"]))
        page.screenshot(path=str(output / "desktop-selected-rule.png"), full_page=True)

        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        undone = page.request.get(endpoint).json()
        assert next(o for o in undone["occurrences"] if o["text"] == "선발하며")["status"] == "unresolved"
        expect(page.locator(".mark-layer .mark")).to_have_count(2)
        with page.expect_response(lambda r: r.url.endswith("/state") and r.request.method == "PATCH"):
            page.get_by_role("button", name="다시 실행", exact=True).click()
        redone = page.request.get(endpoint).json()
        restored = next(o for o in redone["occurrences"] if o["text"] == "선발하며")
        for key in ("status", "selected_candidate_id", "evidence_type", "applied_rules", "pronunciation_scope", "coverage", "source", "meaning_status", "reason", "reason_code"):
            assert restored[key] == item[key], (key, restored.get(key), item[key])
        # Undo of one meaning choice must not turn untouched automatic results
        # into user edits merely because a single candidate was auto-selected.
        untouched = next(o for o in redone["occurrences"] if o["text"] == "보도부")
        for key in ("source", "meaning_status", "reason", "reason_code", "evidence_type"):
            assert untouched[key] == words["보도부"][key], ("untouched 보도부", key, untouched[key], words["보도부"][key])
        expect(page.locator(".mark-layer .mark")).to_have_count(3)

        page.reload(wait_until="networkidle")
        expect(page.locator(".mark-layer .mark")).to_have_count(3)
        page.get_by_role("button", name="다운로드", exact=True).click()
        with page.expect_download() as downloaded:
            page.get_by_role("button", name="장음 표시 PDF").click()
        exported_path = output / "형태소_통합시험_장음표시.pdf"
        downloaded.value.save_as(exported_path)
        with fitz.open(stream=source, filetype="pdf") as before, fitz.open(exported_path) as after:
            assert preservation_signature(before) == preservation_signature(after)
            glyphs = {g["id"]: g for p in redone["pages"] for g in p["glyphs"]}
            exported_page = after[0]
            annotations = list(exported_page.annots())
            assert len(annotations) == len(redone["marks"])
            for mark, annotation in zip(redone["marks"], annotations):
                glyph_rect = fitz.Rect(glyphs[mark["glyph_id"]]["pdf_bbox"])
                assert annotation.rect.intersects(glyph_rect)
                marker_center = (annotation.rect.tl + annotation.rect.br) / 2
                glyph_center = (glyph_rect.tl + glyph_rect.br) / 2
                assert abs(marker_center.x - glyph_center.x) < .01
                assert abs(marker_center.y - glyph_center.y) < .01
            assert any(item["applied_rules"][0]["url"] in a.info["content"] for a in annotations)
            assert after.embfile_count() > 0
            exported_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / "exported-pdf.png")
        assert extract(source)[0] == extract(exported_path.read_bytes())[0], "original glyph text/boxes changed"

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="설정 및 검토", exact=True).click()
        page.get_by_role("button", name="검토 목록").click()
        open_word("사회혁신융합전공")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile overflow"
        page.screenshot(path=str(output / "mobile-compound.png"), full_page=True)
        page.get_by_role("button", name="대본", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile PDF overflow"
        assert not errors, errors
        (output / "analysis.json").write_text(json.dumps(redone, ensure_ascii=False, indent=2))
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "test-results/morphology-browser")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--serve-fixture", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_fixture:
        serve_fixture(args.port)
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    with (output / "fixture-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-fixture", "--port", str(args.port)],
                                   cwd=REPOSITORY, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(trust_env=True, timeout=1) as client:
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
                    raise RuntimeError("fixture server did not become ready")
            run_browser(base, output)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("PASS: actual snapshot morphology, whole-word priority, ambiguity, partial evidence UI, human rule selection, undo/redo, PDF preservation/coordinates, mobile layout; no live dictionary calls or JS errors")
    print("Artifacts:", output)


if __name__ == "__main__":
    main()
