"""Offline privacy, evidence and failure atomicity checks (no live dictionary)."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.main import app, SESSIONS, mark_candidate
from backend.learning_auth import POLICY_VERSION
from backend.learning_events import MutationMeta, mutation, official, snapshot, semantic_signature, unresolved_reason
from backend.learning_api import split_for
from test_numeric_api import numeric_pdf

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/dictionary_snapshot_2026-11-04.json').read_text())


def setup_job(client):
    response = client.post('/api/jobs', files={'file': ('private-name.pdf', numeric_pdf('낭독 낭독 24'), 'application/pdf')})
    assert response.status_code == 201, response.text
    job = SESSIONS[response.json()['id']]
    occurrence = job['occurrences'][0]
    occurrence['candidates'] = deepcopy(FIXTURE['낭독'])
    return job, occurrence


def editable(job):
    return {k: deepcopy(job[k]) for k in ('settings', 'marks', 'occurrences')}


def request_for(subject_cookie='a' * 64):
    return Request({'type': 'http', 'method': 'POST', 'path': '/api/jobs/test', 'scheme': 'http',
                    'server': ('testserver', 80), 'headers': [(b'host', b'testserver'),
                    (b'x-learning-record', b'true'), (b'cookie', f'sori_learning={subject_cookie}'.encode())]})


class LearningOfflineTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, client=("learning-" + uuid4().hex, 50000))
        self.job, self.occurrence = setup_job(self.client)

    def tearDown(self):
        self.client.delete('/api/jobs/' + self.job['id'])

    def test_no_database_and_no_consent_keep_pdf_edits_working(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}), patch('backend.learning_db.connection', side_effect=AssertionError('must not connect')):
            response = self.client.post(f"/api/jobs/{self.job['id']}/occurrences/{self.occurrence['id']}/candidate", json={'candidate_id': self.occurrence['candidates'][0]['id']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['learning']['state'], 'not_requested')

    def test_unconfigured_status_is_not_consent(self):
        self.client.cookies.clear()
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            response = self.client.get('/api/learning/status')
        self.assertFalse(response.json()['consent'])
        self.assertFalse(response.json()['available'])
        self.assertIn('HttpOnly', response.headers.get('set-cookie', ''))

    def test_user_cannot_review_without_admin_token(self):
        response = self.client.post(f'/api/learning/cases/{uuid4()}/review', json={'meaning_status': 'approved', 'length_status': 'approved', 'reason': 'unauthorized', 'expected_version': 0})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/learning/cases?scope=review').status_code, 403)
        self.assertEqual(self.client.get('/api/learning/export.jsonl?scope=approved').status_code, 403)

    def test_cross_origin_consent_and_edits_are_forbidden(self):
        r = self.client.put('/api/learning/consent', headers={'Origin': 'https://foreign.example'}, json={'enabled': True, 'policy_version': POLICY_VERSION})
        self.assertEqual(r.status_code, 403)

    def test_reverse_proxy_origin_and_secure_cookie_configuration(self):
        with patch.dict(os.environ, {'DATABASE_URL': '', 'LEARNING_PUBLIC_ORIGIN': 'https://testserver'}):
            client = TestClient(app, base_url='https://testserver')
            result = client.get('/api/learning/status')
            self.assertIn('Secure', result.headers['set-cookie'])
            result = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'Origin': 'https://testserver'}, json={'marks': []})
            self.assertEqual(result.status_code, 200, result.text)
        r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'Origin': 'https://foreign.example'}, json={'marks': []})
        self.assertEqual(r.status_code, 403)

    def test_semantics_and_length_are_separate(self):
        candidate = self.occurrence['candidates'][0]
        candidate.update(pronunciations=[], pronunciation='', inflections=[])
        mark_candidate(self.job, self.occurrence, candidate, user_selected=True)
        record = snapshot(self.job, self.occurrence)
        self.assertTrue(record['meaning_supported'])
        self.assertFalse(record['length_supported'])
        self.assertEqual(record['state']['status'], 'unresolved')
        self.assertEqual(record['unresolved_reason'], 'pronunciation_evidence_missing')

    def test_official_snapshot_has_provenance_and_exact_position(self):
        mark_candidate(self.job, self.occurrence, self.occurrence['candidates'][0], user_selected=True)
        record = snapshot(self.job, self.occurrence)
        self.assertTrue(record['meaning_supported'])
        self.assertTrue(record['length_supported'])
        self.assertEqual(record['marked_character_indices'], record['engine_character_indices'])
        self.assertEqual(len(record['dictionary_provenance']['snapshot_sha256']), 64)
        self.assertIsNone(record['dictionary_provenance']['edition'])
        self.assertFalse(official({'id': '1:1', 'url': 'https://unofficial.example'}))

    def test_manual_position_change_cannot_be_approved_from_old_evidence(self):
        mark_candidate(self.job, self.occurrence, self.occurrence['candidates'][0], user_selected=True)
        self.job['marks'] = []
        self.assertFalse(snapshot(self.job, self.occurrence)['length_supported'])

    def test_snapshot_is_minimal_and_does_not_keep_trailing_context_or_notes(self):
        self.occurrence['morphology'] = {'lemma': '낭독', 'pos': 'NNG', 'trailing_context': 'private entire trailing line', 'following_tokens': [{'form': 'private'}]}
        self.occurrence['note'] = 'private memo not opted into collection'
        record = snapshot(self.job, self.occurrence)
        self.assertLessEqual(len(record['context']), 160)
        serialized = json.dumps(record, ensure_ascii=False)
        for private in ('private-name.pdf', 'private entire trailing line', 'private memo', 'pdf_bbox'):
            self.assertNotIn(private, serialized)

    def test_visual_geometry_does_not_change_semantic_signature(self):
        mark_candidate(self.job, self.occurrence, self.occurrence['candidates'][0], user_selected=True)
        before = semantic_signature(self.occurrence, self.job['marks'])
        self.job['marks'][0]['dx'] = 9
        self.job['settings']['color'] = '#FFFFFF'
        self.assertEqual(before, semantic_signature(self.occurrence, self.job['marks']))

    def test_unresolved_categories_and_document_split_are_distinct(self):
        reasons = {code: unresolved_reason({'reason_code': code, 'status': 'unresolved'}) for code in ('lookup_timeout', 'lookup_failed', 'not_found', 'ambiguous', 'inflection_unverified')}
        self.assertEqual(len(set(reasons.values())), 5)
        self.assertEqual(split_for('00000008' + 'a' * 56), 'validation')
        self.assertEqual(split_for('00000009' + 'a' * 56), 'test')

    def test_rollback_even_for_partial_invalid_restore(self):
        before = editable(self.job)
        bad = editable(self.job)
        bad['occurrences'][0].update(status='long', note='changed')
        bad['occurrences'][1]['numeric'] = {'selected_option_id': 'never-verified'}
        # Force an invalid restore late in the loop after a previous mutation.
        with patch('backend.main.restore_length_evidence', side_effect=HTTPException(422, 'invalid cached evidence')):
            result = self.client.patch(f"/api/jobs/{self.job['id']}/state", json={**bad, 'restore': True})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(editable(self.job), before)
        self.assertEqual(self.job['revision'], 0)

    def test_optimistic_revision_rejects_stale_edit(self):
        result = self.client.patch(f"/api/jobs/{self.job['id']}/state", json={'marks': [], 'expected_revision': 99})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.job['revision'], 0)

    def test_commit_failure_restores_memory_and_never_sets_saved_receipt(self):
        from backend.learning_auth import identity
        req = request_for()
        self.job['_learning_owner'] = identity(req)
        before = editable(self.job)
        class Result:
            def __init__(self, row): self.row = row
            def fetchone(self): return self.row
        class Conn:
            def execute(self, sql, params=()):
                return Result({'enabled': True, 'policy_version': POLICY_VERSION} if 'FROM learning_consents' in sql else None)
        @contextmanager
        def broken_commit():
            yield Conn()
            raise HTTPException(503, 'simulated commit failure')
        meta = MutationMeta(idempotency_key=uuid4(), expected_revision=0)
        with patch('backend.learning_db.connection', broken_commit), patch('backend.learning_events.persist', return_value={'state': 'saved', 'event_id': str(meta.event_id)}):
            with self.assertRaises(HTTPException):
                with mutation(self.job, req, meta, {}, 'manual_edit'):
                    self.job['marks'] = [{'glyph_id': 'temporary'}]
                    self.job['revision'] += 1
        self.assertEqual(editable(self.job), before)
        self.assertEqual(self.job['revision'], 0)
        self.assertNotEqual(self.job.get('_learning_receipt', {}).get('state'), 'saved')




class DictionaryFailureMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_preserves_timeout_category_and_hides_transport_url(self):
        import httpx
        from unittest.mock import AsyncMock
        from backend.dictionary import DictionaryClient, DictionaryError
        with patch.dict(os.environ, {'STDICT_API_KEY': 'fixture-only'}), patch('backend.dictionary.httpx.AsyncClient'), patch.object(DictionaryClient, '_get', AsyncMock(side_effect=httpx.ReadTimeout('private-url-and-key'))):
            with self.assertRaises(DictionaryError) as raised:
                await DictionaryClient().lookup('fixture')
        self.assertEqual(raised.exception.failure_kind, 'lookup_timeout')
        self.assertNotIn('private-url-and-key', str(raised.exception))

    async def test_analysis_retains_old_policy_and_new_timeout_metadata(self):
        from backend.dictionary import DictionaryError
        from backend.main import analyze_job
        from test_numeric_api import numeric_job
        class TimeoutReplay:
            async def lookup(self, word):
                raise DictionaryError('fixture timeout', failure_kind='lookup_timeout')
        _, job = numeric_job('낭독')
        job['_dictionary'] = TimeoutReplay()
        await analyze_job(job)
        self.assertEqual(job['occurrences'][0]['reason_code'], 'lookup_failed')
        self.assertEqual(unresolved_reason(job['occurrences'][0]), 'lookup_timeout')


if __name__ == '__main__':
    unittest.main()
