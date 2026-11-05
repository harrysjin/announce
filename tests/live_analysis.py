"""Opt-in live integration check. Requires a running configured server.

Uses the app's example PDF and the real official API, never synthetic readings.
python3 tests/live_analysis.py --base http://127.0.0.1:8000
"""
import argparse
import collections
import json
from pathlib import Path
import time

import fitz
import httpx

from backend.dictionary import long_positions
from backend.pdf import preservation_signature


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    parser.add_argument('--output', default='test-results/live-analysis')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    # This client only contacts the local app. The app retains the session proxy
    # and CA verification for every outgoing official dictionary request.
    with httpx.Client(base_url=args.base, timeout=45, trust_env=False) as client:
        health = client.get('/api/health').raise_for_status().json()
        if not health['dictionary_configured']:
            raise SystemExit('BLOCKED: dictionary key is not configured')
        original = client.get('/api/sample').raise_for_status().content
        (output / '소리_예시대본.pdf').write_bytes(original)
        job = client.post('/api/jobs', files={'file': ('소리_예시대본.pdf', original, 'application/pdf')}).raise_for_status().json()
        endpoint = f"/api/jobs/{job['id']}"
        client.post(endpoint + '/analyze').raise_for_status()
        deadline = time.monotonic() + 600
        last = None
        while True:
            job = client.get(endpoint).raise_for_status().json()
            state = job['analysis']
            if (state['state'], state['done']) != last:
                print(json.dumps({'analysis': state}, ensure_ascii=False), flush=True)
                last = (state['state'], state['done'])
            if state['state'] not in ('queued', 'running'):
                break
            if time.monotonic() > deadline:
                client.post(endpoint + '/cancel').raise_for_status()
                raise AssertionError('Live analysis timed out; stopped the test job')
            time.sleep(1)
        (output / 'analysis.json').write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        assert state['state'] == 'complete', 'Dictionary requests did not all complete; inspect analysis.json'
        assert state['done'] == state['total'] > 0
        assert job['marks'], 'No automatic marks were produced; inspect official readings'
        glyphs = {g['id']: g for p in job['pages'] for g in p['glyphs']}
        occurrences = {o['id']: o for o in job['occurrences']}
        marked_syllables = []
        for mark in job['marks']:
            assert mark['source'] in ('dictionary', 'dictionary_consensus', 'dictionary_rule')
            occurrence = occurrences[mark['occurrence_id']]
            assert occurrence['status'] == 'long'
            evidence_ids = occurrence.get('evidence_candidates') or [occurrence['selected_candidate_id']]
            candidates = [c for c in occurrence['candidates'] if c['id'] in evidence_ids]
            assert candidates
            # Position evidence is explicit even when the meaning is not chosen.
            if occurrence.get('long_glyph_ids'):
                assert mark['glyph_id'] in occurrence['long_glyph_ids']
            else:
                offsets = long_positions(candidates[0]['pronunciations'])
                assert offsets is not None and offsets
                assert mark['glyph_id'] in [occurrence['glyph_ids'][i] for i in offsets]
            if 'consensus' in mark['source']:
                assert occurrence['selected_candidate_id'] is None
                assert occurrence.get('meaning_status') == 'unresolved'
                assert len(evidence_ids) > 1
            marked_syllables.append({'text': occurrence['text'], 'syllable': glyphs[mark['glyph_id']]['text'], 'evidence_type': occurrence.get('evidence_type'), 'meaning_status': occurrence.get('meaning_status'), 'pronunciations': occurrence.get('pronunciations') or [c['pronunciation'] for c in candidates], 'sources': list(dict.fromkeys(c['url'] for c in candidates))})
        for kind, name in [('pdf', '소리_예시대본_장음표시.pdf'), ('md', '소리_예시대본_장음검증.md'), ('csv', '소리_예시대본_장음목록.csv')]:
            (output / name).write_bytes(client.get(endpoint + '/export/' + kind).raise_for_status().content)
        with fitz.open(stream=original, filetype='pdf') as before, fitz.open(output / '소리_예시대본_장음표시.pdf') as after:
            assert preservation_signature(before) == preservation_signature(after)
            assert after.embfile_count() == 1
            annotations = [a for p in after for a in p.annots() or []]
            assert len(annotations) == len(job['marks'])
            for page in after:
                for annot in page.annots() or []:
                    assert annot.colors['stroke'] == []
                    assert max(abs(a - b) for a, b in zip(annot.colors['fill'], (206/255, 228/255, 245/255))) < .01
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / f'page-{page.number+1}-marked.png')
            for page in before:
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / f'page-{page.number+1}-original.png')
        # A settings change must reuse analysis and existing occurrence evidence.
        changed = client.patch(endpoint + '/state', json={'settings': {'color': '#F2D4DC', 'opacity': .55, 'size': 1.05}}).raise_for_status().json()
        assert changed['analysis'] == job['analysis']
        assert changed['occurrences'] == job['occurrences']
        pink = client.get(endpoint + '/export/pdf').raise_for_status().content
        with fitz.open(stream=pink, filetype='pdf') as document:
            for page in document:
                for annot in page.annots() or []:
                    assert max(abs(a-b) for a,b in zip(annot.colors['fill'], (242/255, 212/255, 220/255))) < .01
                    assert abs(annot.opacity-.55) < .01
        client.patch(endpoint + '/state', json={'settings': job['settings']}).raise_for_status()
        summary = {'health': health, 'analysis': state, 'counts': job['counts'], 'reason_counts': dict(collections.Counter(o['reason_code'] for o in job['occurrences'])), 'automatic_marks': marked_syllables, 'pdf_preservation': 'passed', 'settings_reuse': 'passed', 'real_api': True, 'ocr_tested': False}
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print('PASS: live search/detail, automatic glyph marks, uncertainty, all exports, original PDF preservation, settings reuse', flush=True)
        print('Artifacts:', output.resolve(), flush=True)


if __name__ == '__main__':
    main()
