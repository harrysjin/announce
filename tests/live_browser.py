"""Opt-in browser check against real configured dictionary API.
The five-line PDF is generated test input, not a linguistic benchmark.
"""
import argparse
import json
from pathlib import Path
import time

import fitz
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    parser.add_argument('--output', default='test-results/live-browser')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    sheet = document.new_page()
    for i, text in enumerate(['천천히', '낭독', '눈', '4개', '0.35초']):
        sheet.insert_text((72, 110 + i * 60), text, fontname='korea', fontsize=22)
    data = document.tobytes()
    document.close()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.base, wait_until='networkidle')
        expect(page.get_by_text('표준국어대사전 연결 설정됨', exact=True)).to_be_visible()
        with page.expect_response(lambda r: '/analyze' in r.url and r.request.method=='POST', timeout=15000) as auto_started:
            with page.expect_response(lambda r: r.url.endswith('/api/jobs') and r.request.method=='POST') as result:
                page.get_by_label('PDF 업로드').set_input_files({'name':'실제연동_시험.pdf','mimeType':'application/pdf','buffer':data})
        job = result.value.json()
        assert auto_started.value.status == 200
        assert auto_started.value.json()['analysis']['state'] in ('queued','running','complete')
        page.reload(wait_until='networkidle')
        expect(page.locator('.document-name strong')).to_have_text('실제연동_시험.pdf')
        endpoint = args.base + '/api/jobs/' + job['id']
        deadline = time.monotonic()+180
        started = False
        last = None
        while time.monotonic()<deadline:
            job = page.request.get(endpoint).json()
            state=job['analysis']
            if state['state'] != 'idle': started=True
            if state != last:
                print(json.dumps({'analysis':state},ensure_ascii=False),flush=True)
                last=state.copy()
            if started and state['state'] not in ('queued','running'):break
            page.wait_for_timeout(700)
        (output/'automatic-analysis.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        assert job['analysis']['state']=='complete',job['analysis']
        lookup={o['text']:o for o in job['occurrences']}
        assert lookup['천천히']['status']=='long'
        assert lookup['낭독']['status']=='long' and len(lookup['낭독']['candidates'])>1
        assert lookup['낭독']['meaning_status']=='unresolved'
        assert lookup['낭독']['selected_candidate_id'] is None
        assert lookup['눈']['status']=='unresolved' and len(lookup['눈']['candidates'])>1
        for word in ['4개','0.35초']:
            assert lookup[word]['status']=='unresolved' and lookup[word]['reason_code']=='reading_required'
        expect(page.locator('.mark-layer .mark')).to_have_count(len(job['marks']))
        page.get_by_role('button',name='검토 목록').click()
        expect(page.get_by_text('뜻 선택 미확정',exact=True).first).to_be_visible()
        page.screenshot(path=str(output/'desktop-automatic.png'),full_page=True)
        # Choose the actual reading-aloud sense for 낭독 as a test user; the
        # real API also returns unrelated 狼毒 senses: length agrees, while the
        # meaning must still not be automatically chosen.
        snow = next(c for c in lookup['낭독']['candidates'] if c['definition']=='글을 소리 내어 읽음.')
        print(json.dumps({'manual_test_sense':{'word':snow['word'],'pronunciation':snow['pronunciation'],'definition':snow['definition'],'source':snow['url']}},ensure_ascii=False),flush=True)
        heading=page.locator('.occurrence-heading').filter(has=page.get_by_text('낭독',exact=True))
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='설정 및 검토',exact=True).click()
        heading.click()
        expect(page.get_by_placeholder('이 표현에 관한 메모')).to_be_visible()
        expect(page.locator('.candidate').filter(has_text=snow['definition'])).to_be_visible()
        # Source navigation is explicit and independent from opening details.
        page.get_by_role('button',name='원문 위치 보기 · 1쪽',exact=True).click()
        expect(page.locator('.pdf-paper')).to_be_visible()
        page.get_by_role('button',name='설정 및 검토',exact=True).click()
        expect(page.get_by_placeholder('이 표현에 관한 메모')).to_be_visible()
        page.set_viewport_size({'width':1440,'height':1050})
        memo=page.get_by_placeholder('이 표현에 관한 메모')
        memo.fill('사전 후보의 장단 일치, 뜻은 별도 검토')
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            memo.press('Tab')
        after_note=page.request.get(endpoint).json()
        item=next(o for o in after_note['occurrences'] if o['text']=='낭독')
        assert item['source']=='dictionary_consensus' and item['meaning_status']=='unresolved'
        page.get_by_role('button',name='표시 설정',exact=True).click()
        color=page.get_by_role('textbox',name='HEX 색상')
        color.fill('#F2D4DC')
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            color.press('Tab')
        after_setting=page.request.get(endpoint).json()
        assert next(o for o in after_setting['occurrences'] if o['text']=='낭독')['source']=='dictionary_consensus'
        with page.expect_response(lambda r:'/state' in r.url and r.request.method=='PATCH'):
            page.get_by_role('button',name='기본 설정으로 되돌리기',exact=True).click()
        page.get_by_role('button',name='검토 목록').click()
        card=page.locator('.candidate').filter(has_text=snow['definition'])
        with page.expect_response(lambda r:'/candidate' in r.url and r.request.method=='POST'):
            card.get_by_role('button',name='이 의미 선택',exact=True).click()
        chosen=page.request.get(endpoint).json()
        eye=next(o for o in chosen['occurrences'] if o['text']=='낭독')
        assert eye['selected_candidate_id']==snow['id'] and eye['source']=='manual'
        assert eye['status']=='long'
        expect(page.locator('.mark-layer .mark')).to_have_count(len(chosen['marks']))
        page.screenshot(path=str(output/'desktop-reviewed.png'),full_page=True)
        page.reload(wait_until='networkidle')
        expect(page.locator('.mark-layer .mark')).to_have_count(len(chosen['marks']))
        page.get_by_role('button',name='다운로드',exact=True).click()
        expect(page.get_by_text('현재 검토 결과',exact=True)).to_be_visible()
        with page.expect_download() as result:
            page.get_by_role('button',name='장음 표시 PDF').click()
        result.value.save_as(output/'실제연동_시험_장음표시.pdf')
        with fitz.open(output/'실제연동_시험_장음표시.pdf') as exported:
            assert exported[0].get_text()=='천천히\n낭독\n눈\n4개\n0.35초\n'
            assert len(list(exported[0].annots()))==len(chosen['marks'])
            exported[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(output/'reviewed-pdf.png')
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='설정 및 검토',exact=True).click()
        page.get_by_role('button',name='검토 목록').click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(output/'mobile-reviewed.png'),full_page=True)
        assert not errors,errors
        (output/'analysis.json').write_text(json.dumps(chosen,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
    print('PASS: automatic analysis on upload, mobile review and source navigation, refresh restoration, live analysis, exact automatic mark, ambiguous homonym, number uncertainty, source-backed human selection, PDF download, mobile UI; no JS errors',flush=True)


if __name__=='__main__':main()
