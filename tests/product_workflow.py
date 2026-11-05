"""Product regression in Chromium, without live dictionary requests.

Start the app, then python3 tests/product_workflow.py --base http://127.0.0.1:8000
Only PATCH failure responses are simulated; PDFs and successful edits use real API.
"""
import argparse
from pathlib import Path

import fitz
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='http://127.0.0.1:8000')
    parser.add_argument('--output',default='test-results/product-workflow')
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    document=fitz.open();sheet=document.new_page()
    sheet.insert_text((72,100),'눈 눈',fontname='korea',fontsize=20)
    source=document.tobytes();document.close()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.base,wait_until='networkidle')
        page.get_by_label('업로드 후 자동으로 장음 분석').uncheck()
        with page.expect_response(lambda r:r.url.endswith('/api/jobs') and r.request.method=='POST') as result:
            page.get_by_label('PDF 업로드').set_input_files({'name':'제품흐름.pdf','mimeType':'application/pdf','buffer':source})
        job=result.value.json();endpoint=args.base+'/api/jobs/'+job['id']
        assert page.request.get(endpoint).json()['analysis']['state']=='idle'
        page.get_by_role('button',name='검토 목록').click()
        page.get_by_label('같은 표현 모아 보기').check()
        expect(page.locator('.occurrence-heading')).to_have_count(2)
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='설정 및 검토',exact=True).click()
        page.locator('.occurrence-heading').first.click()
        expect(page.get_by_placeholder('이 표현에 관한 메모')).to_be_visible()
        page.get_by_role('button',name='원문 위치 보기 · 1쪽',exact=True).click()
        expect(page.locator('.pdf-paper')).to_be_visible()
        page.get_by_role('button',name='설정 및 검토',exact=True).click()
        page.get_by_role('button',name='표시 설정',exact=True).click()
        page.set_viewport_size({'width':1440,'height':1000})
        def failed_patch(route):
            if route.request.method=='PATCH':
                route.fulfill(status=500,json={'detail':'시험용 저장 실패'})
            else: route.continue_()
        page.route('**/state',failed_patch)
        color=page.get_by_role('textbox',name='HEX 색상');color.fill('#F2D4DC')
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            color.press('Tab')
        expect(page.get_by_role('alert')).to_contain_text('시험용 저장 실패')
        expect(color).to_have_value('#CEE4F5')
        expect(page.get_by_role('button',name='실행 취소',exact=True)).to_be_disabled()
        page.unroute('**/state',failed_patch)
        page.get_by_role('button',name='직접 표시',exact=True).click()
        glyph=page.locator('.glyph-target').first
        glyph.focus()
        glyph.press('ArrowRight')
        expect(page.locator('.glyph-target').nth(1)).to_be_focused()
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            page.locator('.glyph-target').nth(1).press('Enter')
        expect(page.locator('.mark-layer .mark')).to_have_count(1)
        state=page.request.get(endpoint).json()
        assert state['marks'][0]['glyph_id']==state['pages'][0]['glyphs'][-1]['id']
        page.route('**/state',failed_patch)
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            page.get_by_role('button',name='실행 취소',exact=True).click()
        expect(page.locator('.mark-layer .mark')).to_have_count(1)
        expect(page.get_by_role('button',name='실행 취소',exact=True)).to_be_enabled()
        page.unroute('**/state',failed_patch)
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            page.get_by_role('button',name='실행 취소',exact=True).click()
        expect(page.locator('.mark-layer .mark')).to_have_count(0)
        expect(page.get_by_role('button',name='다시 실행',exact=True)).to_be_enabled()
        page.get_by_role('button',name='다운로드',exact=True).click()
        expect(page.get_by_text('자동 분석 전',exact=True)).to_be_visible()
        page.screenshot(path=str(output/'desktop.png'),full_page=True)
        page.get_by_role('button',name='장음 표시 PDF').click()
        assert page.request.get(endpoint).json()['analysis']['state']=='idle'
        page.get_by_role('button',name='작업 삭제',exact=True).click()
        expect(page.locator('.document-name strong')).to_have_text('새로운 대본')
        assert page.request.get(endpoint).status==404
        assert not errors,errors
        browser.close()
    print('PASS: unresolved contexts preserved, mobile details/source split, failed save/undo rollback, keyboard glyph navigation, unanalysed export scope, deletion; no live dictionary calls')

if __name__=='__main__':main()
