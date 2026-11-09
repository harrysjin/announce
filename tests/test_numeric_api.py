"""Numeric reading selection and original PDF digit placement regressions."""
from copy import deepcopy
import csv
import io
import hashlib
import json
from pathlib import Path
import unittest

import fitz
from fastapi.testclient import TestClient

from backend.main import app, analyze_job, StatePatch, patch_state
from backend.pdf import extract, output_pdf, preservation_signature, verification, csv_export
from test_morphology_regression import editable_snapshot, stored_job


FIXTURE = Path(__file__).parent / 'fixtures' / 'numeric_dictionary_2026-11-08.json'


class NumericReplay:
    def __init__(self):
        self.entries = json.loads(FIXTURE.read_text())['entries']

    async def lookup(self, word):
        return deepcopy(self.entries.get(word, []))

    async def lookup_inflections(self, word):
        return []


def numeric_pdf(text):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((70, 110), text, fontname='korea', fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def numeric_job(text):
    data = numeric_pdf(text)
    pages, occurrences = extract(data)
    return data, {'id': 'numeric-api-regression', 'filename': '숫자회귀.pdf',
                  'sha256': hashlib.sha256(data).hexdigest(), 'pages': pages,
                  'occurrences': occurrences, 'marks': [],
                  'settings': {'color': '#CEE4F5', 'opacity': .6, 'size': 1},
                  'analysis': {'state': 'running', 'done': 0, 'total': len(occurrences), 'message': 'test'},
                  'history': [], 'revision': 0, 'warnings': [], '_dictionary': NumericReplay()}


class NumericExtractionTests(unittest.TestCase):
    def test_time_phone_percent_and_decimal_keep_one_original_expression(self):
        data = numeric_pdf('0.35초 10:25 010-2456-7788 25% 2026년 제2')
        pages, occurrences = extract(data)
        expected = ['0.35초', '10:25', '010-2456-7788', '25%', '2026년', '제2']
        self.assertEqual([o['text'] for o in occurrences], expected)
        glyphs = {g['id']: g for g in pages[0]['glyphs']}
        for occurrence in occurrences:
            self.assertEqual(''.join(glyphs[gid]['text'] for gid in occurrence['glyph_ids']), occurrence['text'])

    def test_numeric_exports_keep_user_reading_and_exact_original_character_mapping(self):
        # A deliberately explicit metadata fixture isolates export formatting;
        # it is not an assertion of a dictionary pronunciation for the phrase.
        _, job = numeric_job('24')
        occurrence = job['occurrences'][0]
        numeric = {'kind': 'cardinal', 'surface': '24',
                   'options': [{'id': 'test-sino-choice', 'system': 'sino', 'label': '한자어 수사',
                                'reading': '이십사',
                                'alignment': [{'reading_start': 0, 'reading_end': 1,
                                               'source_start': 0, 'source_end': 1, 'exact': True}]}],
                   'selected_option_id': 'test-sino-choice', 'choice_source': 'user'}
        occurrence.update(numeric=numeric, status='long', source='manual', reason='사용자 읽기 선택',
                          coverage={'complete': True, 'checked_ranges': [[0, 1]], 'unresolved_ranges': []})
        report = verification(job)
        row = next(csv.DictReader(io.StringIO(csv_export(job).decode('utf-8-sig'))))
        self.assertIn('숫자 읽기와 원문 대응', report)
        details = row['숫자 읽기와 원문 대응']
        for text in ('test-sino-choice', '이십사', 'user', 'exact', '[0:1]'):
            self.assertIn(text, report)
            self.assertIn(text, details)
        self.assertIn('2 (', report, 'Numeric ranges must describe original characters, not absent Hangul syllables')
        self.assertIn('2 (', row['검증 범위'])

    def test_unknown_reading_endpoint_is_not_successful_html(self):
        response = TestClient(app).post('/api/jobs/unknown/occurrences/o0/reading', json={'option_id': 'sino'})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.headers.get('content-type', '').startswith('application/json'))


class NumericAPITests(unittest.IsolatedAsyncioTestCase):
    async def select_sino(self, job):
        await analyze_job(job)
        occurrence = job['occurrences'][0]
        self.assertIn('numeric', occurrence, 'Number occurrences need reviewable reading options')
        selected = next(o for o in occurrence['numeric']['options'] if o['system'] == 'sino')
        response = TestClient(app).post(f"/api/jobs/{job['id']}/occurrences/{occurrence['id']}/reading",
                                        json={'option_id': selected['id']})
        self.assertEqual(response.status_code, 200, response.text)
        return occurrence, selected, response.json()

    async def test_user_reading_selection_marks_original_digit_and_preserves_pdf(self):
        source, job = numeric_job('24')
        original_pages = deepcopy(job['pages'])
        with stored_job(source, job):
            occurrence, selected, result = await self.select_sino(job)
            self.assertEqual(occurrence['source'], 'manual')
            self.assertEqual(occurrence['numeric']['selected_option_id'], selected['id'])
            self.assertEqual(occurrence['status'], 'long')
            self.assertEqual(occurrence['evidence_type'], 'numeric_rule')
            self.assertEqual([m['glyph_id'] for m in result['marks']], [occurrence['glyph_ids'][0]])
            exported = output_pdf(source, job)
        self.assertEqual(job['pages'], original_pages)
        with fitz.open(stream=source, filetype='pdf') as before, fitz.open(stream=exported, filetype='pdf') as after:
            self.assertEqual(preservation_signature(before), preservation_signature(after))
        self.assertEqual(extract(source)[0], extract(exported)[0])

    async def test_reading_choice_survives_reanalysis_and_history_restore(self):
        source, job = numeric_job('24')
        with stored_job(source, job):
            await analyze_job(job)
            before = editable_snapshot(job)
            occurrence, selected, _ = await self.select_sino(job)
            chosen = editable_snapshot(job)
            await analyze_job(job)
            self.assertEqual(occurrence['numeric']['selected_option_id'], selected['id'])
            self.assertEqual(job['marks'], chosen['marks'])
            patch_state(job['id'], StatePatch(**before, restore=True))
            self.assertEqual(occurrence['status'], 'unresolved')
            self.assertEqual(job['marks'], [])
            patch_state(job['id'], StatePatch(**chosen, restore=True))
            for key in ('source', 'status', 'evidence_type', 'evidence_candidates', 'numeric', 'length_evidence', 'applied_rules', 'coverage'):
                self.assertEqual(occurrence.get(key), chosen['occurrences'][0].get(key), key)
            self.assertEqual([m['glyph_id'] for m in job['marks']], [occurrence['glyph_ids'][0]])

    async def test_invalid_reading_choice_cannot_create_a_mark(self):
        source, job = numeric_job('2번')
        with stored_job(source, job):
            await analyze_job(job)
            occurrence = job['occurrences'][0]
            response = TestClient(app).post(f"/api/jobs/{job['id']}/occurrences/{occurrence['id']}/reading",
                                            json={'option_id': 'not-a-server-option'})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(job['marks'], [])
            self.assertEqual(occurrence['status'], 'unresolved')


if __name__ == '__main__':
    unittest.main()
