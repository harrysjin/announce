"""Real browser + disposable PostgreSQL acceptance flow; never uses production DB."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from playwright.sync_api import sync_playwright, expect
    from test_numeric_api import numeric_pdf
    dsn = os.environ.get('LEARNING_TEST_DATABASE_URL')
    if not dsn:
        raise SystemExit('Set LEARNING_TEST_DATABASE_URL to a disposable PostgreSQL service.')
    name = 'browser_' + uuid4().hex
    output = Path('test-results/learning-browser'); output.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    browser_dsn = make_conninfo(dsn, dbname=name)
    token = uuid4().hex + uuid4().hex
    env = {**os.environ, 'DATABASE_URL': browser_dsn, 'LEARNING_ADMIN_TOKEN': token,
           'LEARNING_DOCUMENT_KEY': uuid4().hex + uuid4().hex, 'LEARNING_ADMIN_ID': 'browser-reviewer',
           'STDICT_API_KEY': '', 'LEARNING_PUBLIC_ORIGIN': ''}
    server = None
    errors = []
    page = None
    try:
        subprocess.run([sys.executable, '-m', 'backend.learning_db'], env=env, check=True, capture_output=True)
        log = (output / 'server.log').open('w')
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8765', '--no-access-log'], env=env, stdout=log, stderr=log)
        for _ in range(100):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=1) as response:
                    if response.status == 200: break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError('Test server did not start')
        with sync_playwright() as p:
            executable = os.environ.get('LEARNING_TEST_CHROMIUM')
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], **({'executable_path': executable} if executable else {}))
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8765/learning', wait_until='networkidle')
            expect(page.get_by_role('heading', name='장음 학습 관리', exact=True)).to_be_visible()
            page.reload(wait_until='networkidle')
            expect(page.get_by_role('heading', name='장음 학습 관리', exact=True)).to_be_visible()
            page.get_by_role('button', name='작업 공간으로 돌아가기').click()
            expect(page).to_have_url('http://127.0.0.1:8765/')
            page.go_back()
            expect(page).to_have_url('http://127.0.0.1:8765/learning')
            consent = page.get_by_label('안내를 확인했으며 이후 수정 기록 제공에 동의합니다.')
            expect(consent).to_be_enabled(); consent.click()
            expect(consent).to_be_checked()
            expect(page.get_by_text('이후의 선택·수정 기록을 제공합니다.', exact=False)).to_be_visible()
            page.get_by_role('button', name='작업 공간으로 돌아가기').click()
            page.locator('input[type=file]').set_input_files({'name': 'browser-synthetic.pdf', 'mimeType': 'application/pdf', 'buffer': numeric_pdf('낭독 연습')})
            page.wait_for_function("document.querySelector('.pdf-paper img')?.naturalWidth > 0")
            page.get_by_role('button', name='직접 표시', exact=True).click()
            page.locator('.glyph-target').first.click()
            expect(page.locator('.mark-layer .mark')).to_have_count(1)
            expect(page.get_by_text('학습 기록 저장 완료', exact=False)).to_be_visible()
            page.get_by_role('button', name='실행 취소', exact=True).click()
            expect(page.locator('.mark-layer .mark')).to_have_count(0)
            page.get_by_role('button', name='다시 실행', exact=True).click()
            expect(page.locator('.mark-layer .mark')).to_have_count(1)
            page.get_by_role('button', name='장음 학습 관리', exact=True).click()
            page.locator('.learning-card').first.get_by_text('사전 후보·선택·숫자 대응·근거 확인', exact=True).click()
            expect(page.locator('.learning-card').first.get_by_text('원문 발음 미확정', exact=True)).to_be_visible()
            expect(page.locator('.learning-card').first.get_by_text('낭 (1번째 글자)', exact=True)).to_be_visible()
            page.get_by_role('button', name='검수 요청', exact=True).click()
            expect(page.locator('.learning-badge').filter(has_text='검수 대기')).to_be_visible()
            with page.expect_download() as downloaded:
                page.get_by_role('button', name='내 기록 JSONL', exact=True).click()
            path = output / 'export.jsonl'; downloaded.value.save_as(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            assert len(rows) == 3, len(rows)
            assert {r['event_type'] for r in rows} == {'manual_edit', 'undo', 'redo'}
            page.locator('.learning-admin summary').click()
            page.get_by_label('관리자 토큰').fill(token)
            page.get_by_role('button', name='인증', exact=True).click()
            expect(page.get_by_role('tab', name='검수 목록')).to_have_attribute('aria-selected', 'true')
            form = page.locator('.learning-review-form')
            expect(form).to_have_count(1)
            form.get_by_label('의미 확인').select_option('needs_review')
            form.get_by_label('발음·장음 위치 확인').select_option('needs_review')
            form.get_by_label('승인·반려·보류 근거').fill('공식 발음 근거가 없어 추가 검수로 보류합니다.')
            form.get_by_role('button', name='검수 저장').click()
            expect(page.get_by_text('검수 상태와 근거를 저장했습니다.', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
            page.set_viewport_size({'width': 1440, 'height': 1100})
            consent.click()
            expect(consent).not_to_be_checked()
            expect(page.get_by_text('제공 동의를 철회했습니다.', exact=False)).to_be_visible()
            page.get_by_label('내 학습 기록 전체를 삭제하겠습니다.').check()
            page.get_by_role('button', name='내 기록 전체 삭제', exact=True).click()
            expect(page.get_by_text('학습 기록과 검수 내역을 삭제하고 제공 동의를 해제했습니다.', exact=True)).to_be_visible()
            assert page.request.get('http://127.0.0.1:8765/api/learning/events').json()['items'] == []
            assert not errors, errors
            browser.close()
        print(json.dumps({'flow': 'consent/manual/undo/redo/export/submit/review/withdraw/delete', 'page_errors': errors, 'result': 'passed'}, ensure_ascii=False))
    except Exception:
        if page:
            try:
                page.screenshot(path=str(output / 'failure.png'), full_page=True)
                print(json.dumps({'visible_errors': page.locator('[role=alert]').all_text_contents()}, ensure_ascii=False))
            except Exception:
                pass
        raise
    finally:
        if server:
            server.terminate(); server.wait(timeout=10)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
