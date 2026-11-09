"""Opt-in, bounded Chromium smoke test of an already deployed release.

Uploads one generated five-token PDF, makes one meaning and one reading choice,
downloads the result, and deletes only the job it created in ``finally``.
The remote app, not this process, uses its configured official dictionary key.
No deployment, settings, other jobs, or dictionary source links are modified.

Example::
    python3 tests/production_smoke.py --live \
      --base https://sori-studio.onrender.com \
      --expected-commit 276ac0a6e46acac96a519167e0aeb380bbf18c11

Browser HTTP requests use httpx with the inherited proxy and verified CA trust.
MuPDF verifies the generated PDF; this is not a macOS/Adobe viewer result.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urljoin, urlparse

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.pdf import extract, preservation_signature

ENGINE_VERSION = "2026.11.09.2"
EXPECTED_COMMIT = "276ac0a6e46acac96a519167e0aeb380bbf18c11"
LINES = ("보도부를 2명을 4개를 선발하며", "24")


def make_pdf() -> bytes:
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
        for index, line in enumerate(LINES):
            page.insert_text((42, 120 + index * 90), line, fontname="korea", fontsize=19)
        return document.tobytes()


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(base: str, expected_commit: str, output: Path, timeout: int):
    original = make_pdf()
    original_pages, original_items = extract(original)
    assert [item["text"] for item in original_items] == ["보도부를", "2명을", "4개를", "선발하며", "24"]
    (output / "original.pdf").write_bytes(original)
    write_json(output / "original-glyphs.json", original_pages)
    origin = urlparse(base)
    errors, blocked_requests = [], []
    endpoint = None
    summary = {"base": base, "started_at": datetime.now(timezone.utc).isoformat(),
               "engine_version_expected": ENGINE_VERSION, "build_commit_expected": expected_commit,
               "dictionary_mode": "requested app's configured dictionary; browser supplies no candidate fixtures",
               "source_pdf_sha256": hashlib.sha256(original).hexdigest(),
               "source_tokens": [item["text"] for item in original_items],
               "macos_preview": "UNRUN", "adobe_reader": "UNRUN", "passed": False}
    with httpx.Client(timeout=45, trust_env=True, follow_redirects=True) as client:
        health_response = client.get(base + "/api/health")
        health_response.raise_for_status()
        health = health_response.json()
        write_json(output / "health.json", health)
        assert health["ok"] and health["dictionary_configured"], health
        assert health["engine_version"] == ENGINE_VERSION, health
        assert health["build_commit"] == expected_commit, health
        assert {"numeric_reading_selection", "particle_boundaries", "original_glyph_alignment"} <= set(health["capabilities"])
        summary["health"] = health
        requests_sent = 1

        def request(method, url, *, cleanup=False, **kwargs):
            nonlocal requests_sent
            # Reserve two requests for DELETE and its GET-404 verification.
            assert requests_sent < (180 if cleanup else 178), "Smoke-test HTTP request budget exhausted"
            assert urlparse(url).netloc == origin.netloc, "Only the requested site may be contacted"
            requests_sent += 1
            return client.request(method, url, **kwargs)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.set_default_timeout(30000)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def forward(route):
                incoming = route.request
                if urlparse(incoming.url).netloc != origin.netloc:
                    blocked_requests.append(incoming.url)
                    route.abort()
                    return
                headers = {key: value for key, value in incoming.all_headers().items()
                           if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}}
                response = request(incoming.method, incoming.url, headers=headers,
                                   content=incoming.post_data_buffer)
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() not in {"content-encoding", "content-length", "transfer-encoding", "connection"}}
                route.fulfill(status=response.status_code, headers=headers, body=response.content)

            page.route("http://**/*", forward)
            page.route("https://**/*", forward)

            def state():
                response = request("GET", endpoint)
                response.raise_for_status()
                return response.json()

            def item(text, job=None):
                matches = [entry for entry in (job or state())["occurrences"] if entry["text"] == text]
                assert len(matches) == 1, (text, matches)
                return matches[0]

            def marks(entry, job):
                return [mark for mark in job["marks"] if mark.get("occurrence_id") == entry["id"]]

            def assert_first_mark(text, job):
                entry = item(text, job)
                assert entry["status"] == "long", entry
                assert [mark["glyph_id"] for mark in marks(entry, job)] == [entry["glyph_ids"][0]], entry

            def wait_preview():
                revision = state()["revision"]
                page.wait_for_function("""revision => {
                  const paper = document.querySelector('.pdf-paper');
                  const image = paper?.querySelector('img');
                  return paper?.getAttribute('aria-busy') === 'false' && image?.naturalWidth > 0 &&
                    new URL(image.src).searchParams.get('revision') === String(revision);
                }""", arg=revision)

            def open_entry(entry):
                heading = page.locator(f'.occurrence-heading[aria-controls="detail-{entry["id"]}"]')
                if heading.get_attribute("aria-expanded") != "true":
                    heading.click()
                return heading.locator("..")

            try:
                page.goto(base, wait_until="networkidle")
                expect(page.get_by_label("분석 엔진 버전")).to_contain_text(ENGINE_VERSION)
                expect(page.get_by_text("표준국어대사전 연결 설정됨", exact=True)).to_be_visible()
                with page.expect_response(lambda response: response.url.endswith("/api/jobs") and response.request.method == "POST") as uploaded:
                    page.get_by_label("PDF 업로드").set_input_files({
                        "name": "배포확인_다섯항목.pdf", "mimeType": "application/pdf", "buffer": original})
                assert uploaded.value.status == 201, uploaded.value.status
                endpoint = base + "/api/jobs/" + uploaded.value.json()["id"]
                summary["created_job_id"] = endpoint.rsplit("/", 1)[1]
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    job = state()
                    analysis = job["analysis"]
                    if analysis["state"] == "complete":
                        break
                    assert analysis["state"] != "error", analysis
                    page.wait_for_timeout(750)
                else:
                    raise AssertionError(f"Analysis did not complete in {timeout}s: {analysis}")
                write_json(output / "initial-analysis.json", job)
                assert job["pages"] == original_pages, "Remote analysis changed original text or glyph coordinates"
                assert len(job["occurrences"]) == 5
                for text in ("보도부를", "2명을", "4개를"):
                    assert_first_mark(text, job)
                noun = item("보도부를", job)
                assert noun["morphology"]["lemma"] == "보도부"
                assert "rule" in noun["evidence_type"] and noun["applied_rules"]
                for text in ("2명을", "4개를"):
                    number = item(text, job)
                    assert number["numeric"]["choice_source"] == "automatic"
                    chosen = next(option for option in number["numeric"]["options"]
                                  if option["id"] == number["numeric"]["selected_option_id"])
                    assert chosen["system"] == "native"
                    assert number["coverage"]["coordinate_system"] == "characters"
                for text in ("선발하며", "24"):
                    entry = item(text, job)
                    assert entry["status"] == "unresolved" and not marks(entry, job), entry
                wait_preview()
                page.screenshot(path=str(output / "desktop-automatic.png"), full_page=True)
                page.get_by_role("button", name="검토 목록").click()
                verb = item("선발하며", job)
                assert verb["morphology"]["lemma"] == "선발하다"
                assert {"先發", "選拔"} <= {hanja for candidate in verb["candidates"]
                                         for hanja in ("先發", "選拔") if hanja in candidate.get("origin", "")}
                candidate = next(candidate for candidate in verb["candidates"] if "選拔" in candidate.get("origin", ""))
                row = open_entry(verb)
                card = row.locator(".candidate").filter(has_text=candidate["definition"])
                with page.expect_response(lambda response: response.url.endswith("/candidate") and response.request.method == "POST") as choice:
                    card.get_by_role("button", name="이 의미 선택", exact=True).click()
                assert choice.value.status == 200
                chosen_verb_job = state()
                chosen_verb = item("선발하며", chosen_verb_job)
                assert chosen_verb["selected_candidate_id"] == candidate["id"]
                assert chosen_verb["source"] == "manual" and chosen_verb["meaning_status"] == "user_selected"
                assert chosen_verb["evidence_type"] == "rule_inference" and chosen_verb["applied_rules"]
                assert_first_mark("선발하며", chosen_verb_job)
                number = item("24", chosen_verb_job)
                sino = next(option for option in number["numeric"]["options"] if option["system"] == "sino")
                row = open_entry(number)
                card = row.locator(".numeric-option").filter(has=page.get_by_text(sino["reading"], exact=True))
                with page.expect_response(lambda response: response.url.endswith("/reading") and response.request.method == "POST") as reading:
                    card.get_by_role("button", name="이 읽기 선택", exact=True).click()
                assert reading.value.status == 200, reading.value.status
                final = state()
                chosen_number = item("24", final)
                assert chosen_number["numeric"]["choice_source"] == "user" and chosen_number["source"] == "manual"
                assert chosen_number["evidence_type"] == "numeric_rule"
                assert_first_mark("24", final)
                assert final["pages"] == original_pages
                assert len(final["marks"]) == 5
                for entry in final["occurrences"]:
                    evidence_ids = set(entry.get("evidence_candidates", []))
                    candidates = [candidate for candidate in entry["candidates"] if candidate["id"] in evidence_ids]
                    assert candidates, entry["text"]
                    assert all(candidate["url"].startswith("https://stdict.korean.go.kr/") and candidate["queried_at"]
                               and candidate["pronunciations"] for candidate in candidates)
                wait_preview()
                page.screenshot(path=str(output / "desktop-selected.png"), full_page=True)
                img_url = urljoin(base, page.locator(".pdf-paper img").get_attribute("src"))
                query = parse_qs(urlparse(img_url).query)
                assert query["marked"] == ["true"] and query["revision"] == [str(final["revision"])]
                png_response = request("GET", img_url)
                png_response.raise_for_status()
                png = png_response.content
                (output / "web-preview.png").write_bytes(png)
                page.get_by_role("button", name="다운로드", exact=True).click()
                with page.expect_download() as downloaded:
                    page.get_by_role("button", name="장음 표시 PDF").click()
                exported_path = output / "marked.pdf"
                downloaded.value.save_as(exported_path)
                assert extract(exported_path.read_bytes())[0] == original_pages
                with fitz.open(stream=original, filetype="pdf") as source, fitz.open(exported_path) as result:
                    result_page = result[0]
                    assert preservation_signature(source) == preservation_signature(result)
                    rendered = result_page.get_pixmap(matrix=fitz.Matrix(float(query["scale"][0]), float(query["scale"][0])), alpha=False)
                    preview = fitz.Pixmap(png)
                    assert (preview.width, preview.height, preview.samples) == (rendered.width, rendered.height, rendered.samples)
                    ink = source[0].get_pixmap(matrix=fitz.Matrix(float(query["scale"][0]), float(query["scale"][0])), alpha=False).samples
                    assert ink != rendered.samples
                    assert all(ink[index:index + 3] == rendered.samples[index:index + 3]
                               for index in range(0, len(ink), 3) if ink[index:index + 3] != b"\xff\xff\xff")
                    glyphs = {glyph["id"]: glyph for pdf_page in final["pages"] for glyph in pdf_page["glyphs"]}
                    annotations = [annotation for annotation in result_page.annots() if annotation.info["title"] == "장음 표시"]
                    assert len(annotations) == 5
                    for mark, annotation in zip(final["marks"], annotations):
                        rect = fitz.Rect(glyphs[mark["glyph_id"]]["pdf_bbox"])
                        center = (annotation.rect.tl + annotation.rect.br) / 2
                        target = (rect.tl + rect.br) / 2 + fitz.Point(mark["dx"], mark["dy"])
                        assert abs(center.x - target.x) < .01 and abs(center.y - target.y) < .01
                        assert max(abs(actual - expected) for actual, expected in zip(annotation.colors["fill"], (206/255, 228/255, 245/255))) < .01
                    result_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(output / "marked-mupdf.png")
                write_json(output / "final-analysis.json", final)
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="설정 및 검토", exact=True).click()
                page.get_by_role("button", name="검토 목록").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "mobile-review.png"), full_page=True)
                assert not errors, errors
                assert not blocked_requests, blocked_requests
                summary.update(checks_passed=True, reading_response_status=reading.value.status,
                               numeric_choice_source=chosen_number["numeric"]["choice_source"],
                               numeric_source=chosen_number["source"], numeric_evidence_type=chosen_number["evidence_type"],
                               initial_marks=len(job["marks"]), final_marks=len(final["marks"]),
                               original_text_and_glyph_coordinates_preserved=True, preview_export_pixels_identical=True,
                               original_ink_pixels_preserved=True)
            except Exception as error:
                summary["failure"] = {"type": type(error).__name__, "message": str(error)}
                raise
            finally:
                try:
                    if endpoint:
                        deleted = request("DELETE", endpoint, cleanup=True)
                        summary["own_job_deleted"] = deleted.status_code == 200 and deleted.json().get("deleted") is True
                        if summary["own_job_deleted"]:
                            summary["own_job_absent_after_delete"] = request("GET", endpoint, cleanup=True).status_code == 404
                except Exception as error:
                    summary["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
                summary["passed"] = all(summary.get(key) is True for key in (
                    "checks_passed", "own_job_deleted", "own_job_absent_after_delete"))
                summary.update(http_requests=requests_sent, javascript_errors=errors,
                               blocked_external_requests=blocked_requests,
                               finished_at=datetime.now(timezone.utc).isoformat())
                write_json(output / "summary.json", summary)
                browser.close()
    assert summary["passed"], "Smoke-test checks or cleanup failed; see summary.json"
    print("PASS: deployed identity, visible engine, five-token actual API analysis, original glyphs, meaning/reading UI HTTP 200, numeric user provenance, PDF/PNG pixels, original ink, mobile layout, and deletion of own job")
    print("Artifacts:", output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow one generated job and real dictionary analysis on the requested site")
    parser.add_argument("--base", default="https://sori-studio.onrender.com")
    parser.add_argument("--expected-commit", default=EXPECTED_COMMIT)
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/production-2026-11-09")
    parser.add_argument("--analysis-timeout", type=int, default=150)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because this test creates one real job")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", args.expected_commit):
        parser.error("--expected-commit must be the full 40-character deployed Git commit")
    base = args.base.rstrip("/")
    if urlparse(base).scheme not in {"http", "https"} or urlparse(base).path:
        parser.error("--base must be an HTTP(S) site origin")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run(base, args.expected_commit.lower(), output, args.analysis_timeout)


if __name__ == "__main__":
    main()
