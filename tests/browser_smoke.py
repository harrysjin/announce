"""Local Chromium interaction check. Start both servers before running.

python3 tests/browser_smoke.py --base http://127.0.0.1:5173 --output /tmp/sori-browser
Requires Playwright Python and an installed Chromium. No external website is used.
"""
import argparse
from pathlib import Path

import fitz
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    parser.add_argument("--output", default="/tmp/sori-browser")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                                              args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base, wait_until="networkidle")
        expect(page.get_by_role("button", name="PDF 파일 선택", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "desktop-empty.png"), full_page=True)
        with page.expect_response(lambda response: response.url.endswith("/api/jobs") and response.request.method == "POST") as uploaded:
            page.get_by_role("button", name="예시 대본으로 둘러보기").click()
        job = uploaded.value.json()
        expect(page.locator(".document-name strong")).to_have_text("소리_예시대본.pdf")
        page.wait_for_function("document.querySelector('.pdf-paper img')?.naturalWidth > 0")
        page.get_by_role("button", name="직접 표시", exact=True).click()
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.locator(".glyph-target").first.click()
        expect(page.locator(".mark-layer .mark")).to_have_count(1)
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        expect(page.locator(".mark-layer .mark")).to_have_count(0)
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.get_by_role("button", name="다시 실행", exact=True).click()
        expect(page.locator(".mark-layer .mark")).to_have_count(1)
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.get_by_role("button", name="표시 삭제", exact=True).click()
        expect(page.locator(".mark-layer .mark")).to_have_count(0)
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.get_by_role("button", name="실행 취소", exact=True).click()
        expect(page.locator(".mark-layer .mark")).to_have_count(1)
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.locator("#size").press("ArrowRight")
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            page.locator("#opacity").press("ArrowLeft")
        color = page.get_by_role("textbox", name="HEX 색상")
        color.fill("#F2D4DC")
        with page.expect_response(lambda response: "/state" in response.url and response.request.method == "PATCH"):
            color.press("Tab")
        expect(page.locator(".mark-layer .mark circle").first).to_have_attribute("fill", "#F2D4DC")
        page.screenshot(path=str(output / "desktop-document.png"), full_page=True)
        page.get_by_role("button", name="장음 분석", exact=True).click()
        expect(page.get_by_text("사전 연결을 기다리고 있어요", exact=True)).to_be_visible()
        expect(page.locator(".mark-layer .mark")).to_have_count(1)
        page.get_by_role("button", name="다운로드", exact=True).click()
        with page.expect_download() as download_info:
            page.get_by_role("button", name="장음 표시 PDF").click()
        download = download_info.value
        assert download.suggested_filename == "소리_예시대본_장음표시.pdf", download.suggested_filename
        exported = output / download.suggested_filename
        download.save_as(str(exported))
        document = fitz.open(exported)
        exported_page = document[0]
        assert "낭독 연습" in exported_page.get_text()
        circle = next(a for a in exported_page.annots() if a.info["title"] == "장음 표시")
        assert max(abs(a - b) for a, b in zip(circle.colors["fill"], (242/255, 212/255, 220/255))) < 0.01
        assert circle.colors["stroke"] == [], "PDF circles must be borderless"
        assert abs(circle.opacity - 0.55) < 0.01
        assert abs((circle.rect + circle.rect_delta).width - 27 * 1.05) < 0.1
        assert document.embfile_count() > 0
        document.close()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("button", name="설정 및 검토", exact=True)).to_be_visible()
        page.get_by_role("button", name="설정 및 검토", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
        page.screenshot(path=str(output / "mobile-review.png"), full_page=True)
        page.get_by_role("button", name="대본", exact=True).click()
        expect(page.locator(".pdf-paper")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile PDF horizontal overflow"
        page.screenshot(path=str(output / "mobile-pdf.png"), full_page=True)
        page.get_by_role("button", name="작업 삭제", exact=True).click()
        expect(page.locator(".document-name strong")).to_have_text("새로운 대본")
        assert page.request.get(args.base + f"/api/jobs/{job['id']}").status == 404
        page.get_by_label("PDF 업로드").set_input_files({"name": "broken.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-corrupt"})
        expect(page.get_by_role("alert")).to_be_visible()
        assert not errors, errors
        browser.close()
    print("PASS: desktop upload, glyph mark, undo/redo, HEX, no-key state, real PDF download, mobile layout, deletion, invalid input; no page errors")
    print("Screenshots:", output)


if __name__ == "__main__":
    main()
