"""Real PostgreSQL integration checks; dedicated test DSN only, isolated schema."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import learning_db as db
from backend.learning_auth import POLICY_VERSION
from backend.main import SESSIONS, app
from test_learning import setup_job, editable


@unittest.skipUnless(os.environ.get('LEARNING_TEST_DATABASE_URL'), 'LEARNING_TEST_DATABASE_URL is not set; real PostgreSQL required')
class LearningPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.dsn = os.environ['LEARNING_TEST_DATABASE_URL']
        cls.schema = 'learning_test_' + uuid4().hex
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA {cls.schema}')
        @contextmanager
        def isolated_connection():
            try:
                with psycopg.connect(cls.dsn, row_factory=dict_row, connect_timeout=2,
                                     options=f'-c search_path={cls.schema} -c statement_timeout=1500 -c lock_timeout=750') as conn:
                    yield conn
            except HTTPException:
                raise
            except psycopg.Error:
                raise HTTPException(503, 'Test database operation failed') from None
        cls.db_patch = patch.object(db, 'connection', isolated_connection)
        cls.db_patch.start()
        cls.env_patch = patch.dict(os.environ, {'DATABASE_URL': cls.dsn, 'LEARNING_DOCUMENT_KEY': 'd' * 64,
                                               'LEARNING_ADMIN_TOKEN': 'r' * 64, 'LEARNING_ADMIN_ID': 'integration-reviewer'})
        cls.env_patch.start()
        cls.first_migration = db.migrate()
        cls.second_migration = db.migrate()

    @classmethod
    def tearDownClass(cls):
        import psycopg
        cls.env_patch.stop(); cls.db_patch.stop()
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {cls.schema} CASCADE')

    def setUp(self):
        with db.connection() as conn:
            conn.execute('TRUNCATE learning_consents CASCADE')
        self.client = TestClient(app, client=("learning-" + uuid4().hex, 50000))
        self.client.get('/api/learning/status')
        self.job, self.occurrence = setup_job(self.client)
        self.admin_headers = {'Authorization': 'Bearer ' + 'r' * 64}
        self.grant()

    def tearDown(self):
        self.client.delete('/api/jobs/' + self.job['id'])

    def grant(self, enabled=True):
        r = self.client.put('/api/learning/consent', json={'enabled': enabled, 'policy_version': POLICY_VERSION})
        self.assertEqual(r.status_code, 200, r.text)

    def metadata(self, **kwargs):
        return {'event_id': str(uuid4()), 'idempotency_key': str(uuid4()), 'expected_revision': self.job['revision'], **kwargs}

    def select(self, body=None):
        payload = body or {'candidate_id': self.occurrence['candidates'][0]['id'], **self.metadata()}
        r = self.client.post(f"/api/jobs/{self.job['id']}/occurrences/{self.occurrence['id']}/candidate", headers={'X-Learning-Record': 'true'}, json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), payload

    def case(self):
        r = self.client.get('/api/learning/cases')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['items'][0]

    def test_deployment_check_reads_schema_and_detects_version_drift(self):
        from backend.learning_setup import check_setup, migration_manifest
        with patch.dict(os.environ, {'LEARNING_PUBLIC_ORIGIN': 'https://sori.example'}):
            report = check_setup()
            self.assertTrue(report['ready'], report)
            expected = migration_manifest()
            changed = {name: 'outdated-checksum' for name in expected}
            with patch('backend.learning_setup.migration_manifest', return_value=changed):
                self.assertFalse(check_setup()['ready'])
        with db.connection() as conn:
            self.assertEqual({row['version']: row['checksum'] for row in conn.execute('SELECT version,checksum FROM learning_schema_migrations').fetchall()}, expected)

    def test_migrations_are_versioned_and_repeatable(self):
        self.assertEqual(self.first_migration, ['001_learning.sql'])
        self.assertEqual(self.second_migration, [])

    def test_selection_duplicate_retry_and_conflicting_key(self):
        selected, body = self.select()
        repeated, _ = self.select(body)
        self.assertTrue(repeated['learning']['replayed'])
        self.assertEqual(selected['revision'], repeated['revision'])
        self.assertEqual(selected['learning']['event_id'], repeated['learning']['event_id'])
        body = {**body, 'candidate_id': 'different'}
        r = self.client.post(f"/api/jobs/{self.job['id']}/occurrences/{self.occurrence['id']}/candidate", headers={'X-Learning-Record': 'true'}, json=body)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.client.get('/api/learning/events').json()['items']), 1)

    def test_undo_redo_append_compensating_events_and_supersede_cases(self):
        before = editable(self.job)
        chosen, _ = self.select()
        undo = {**before, 'restore': True, 'action': 'undo', **self.metadata(target_event_id=chosen['learning']['event_id'])}
        r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json=undo)
        self.assertEqual(r.status_code, 200, r.text)
        undone = r.json()
        self.assertEqual(undone['marks'], [])
        redo = {**{k: chosen[k] for k in ('settings', 'marks', 'occurrences')}, 'restore': True, 'action': 'redo', **self.metadata(target_event_id=undone['learning']['event_id'])}
        r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json=redo)
        self.assertEqual(r.status_code, 200, r.text)
        events = self.client.get('/api/learning/events').json()['items']
        self.assertEqual([e['event_type'] for e in events], ['redo', 'undo', 'meaning_selected'])
        self.assertEqual(events[0]['compensates_event_id'], events[1]['event_id'])
        cases = self.client.get('/api/learning/cases').json()['items']
        self.assertEqual(sum(c['active'] for c in cases), 1)

    def test_visual_only_changes_do_not_generate_language_cases(self):
        self.select()
        changes = editable(self.job)
        changes['settings']['color'] = '#FFFFFF'
        changes['marks'][0]['dx'] = 4
        r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json={**changes, **self.metadata()})
        self.assertEqual(r.status_code, 200, r.text)
        events = self.client.get('/api/learning/events').json()['items']
        self.assertEqual(events[0]['event_type'], 'visual_edit')
        self.assertEqual(events[0]['changes'], [])
        self.assertEqual(len(self.client.get('/api/learning/cases').json()['items']), 1)

    def test_numeric_reading_is_recorded_with_syllable_alignment(self):
        import asyncio
        from backend.main import analyze_job
        from test_numeric_api import NumericReplay
        self.job['_dictionary'] = NumericReplay()
        asyncio.run(analyze_job(self.job))
        occurrence = self.job['occurrences'][-1]
        option = next(o for o in occurrence['numeric']['options'] if o['system'] == 'sino')
        r = self.client.post(f"/api/jobs/{self.job['id']}/occurrences/{occurrence['id']}/reading", headers={'X-Learning-Record': 'true'}, json={'option_id': option['id'], **self.metadata()})
        self.assertEqual(r.status_code, 200, r.text)
        case = self.case()
        self.assertEqual(case['snapshot']['state']['numeric']['selected_option_id'], option['id'])
        self.assertTrue(case['snapshot']['state']['numeric']['options'][0]['alignment'])

    def test_review_requires_evidence_and_admin_and_records_reviewer(self):
        self.select()
        case = self.case()
        body = {'meaning_status': 'approved', 'length_status': 'approved', 'reason': '사전 응답 재생 자료와 원문 음절 대응 확인', 'expected_version': case['review_version']}
        url = f"/api/learning/cases/{case['case_id']}/review"
        self.assertEqual(self.client.post(url, json=body).status_code, 403)
        r = self.client.post(url, headers=self.admin_headers, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'approved')
        case = self.case()
        self.assertEqual(case['reviews'][0]['reviewer_id'], 'integration-reviewer')
        self.assertEqual(self.client.post(url, headers=self.admin_headers, json=body).status_code, 409)

    def test_meaning_approval_without_length_support_stays_needs_review(self):
        self.occurrence['candidates'][0].update(pronunciations=[], pronunciation='', inflections=[])
        self.select()
        case = self.case()
        url = f"/api/learning/cases/{case['case_id']}/review"
        body = {'meaning_status': 'approved', 'length_status': 'approved', 'reason': 'unsupported', 'expected_version': 0}
        self.assertEqual(self.client.post(url, headers=self.admin_headers, json=body).status_code, 422)
        body['length_status'] = 'needs_review'
        r = self.client.post(url, headers=self.admin_headers, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'needs_review')
        self.assertEqual(self.case()['length_status'], 'needs_review')

    def test_owner_submit_and_reject_audit(self):
        self.select(); case = self.case()
        r = self.client.post(f"/api/learning/cases/{case['case_id']}/submit", json={'expected_version': 0})
        self.assertEqual(r.status_code, 200, r.text)
        case = self.case()
        self.assertEqual(case['status'], 'pending_review')
        r = self.client.post(f"/api/learning/cases/{case['case_id']}/review", headers=self.admin_headers,
                             json={'meaning_status': 'rejected', 'length_status': 'needs_review', 'reason': '문맥 의미 재선택 필요', 'expected_version': 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.case()['status'], 'rejected')
        self.assertEqual(len(self.case()['reviews']), 2)

    def test_withdrawal_blocks_new_events_and_excludes_approved_export(self):
        self.select(); case = self.case()
        self.client.post(f"/api/learning/cases/{case['case_id']}/review", headers=self.admin_headers,
                         json={'meaning_status': 'approved', 'length_status': 'approved', 'reason': '확인', 'expected_version': 0})
        self.grant(False)
        before = editable(self.job)
        r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json={'marks': [], **self.metadata()})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(editable(self.job), before)
        self.assertEqual(self.client.get('/api/learning/export.jsonl?scope=approved', headers=self.admin_headers).text, '')
        self.assertEqual(self.client.get('/api/learning/cases?scope=review', headers=self.admin_headers).json()['items'], [])
        # No consent remains a fully usable PDF workspace.
        self.assertEqual(self.client.patch(f"/api/jobs/{self.job['id']}/state", json={'marks': []}).status_code, 200)

    def test_delete_removes_events_cases_reviews_and_consent(self):
        self.select(); case = self.case()
        self.client.post(f"/api/learning/cases/{case['case_id']}/submit", json={'expected_version': 0})
        self.assertEqual(self.client.delete('/api/learning/records').status_code, 200)
        with db.connection() as conn:
            for table in ('learning_consents', 'learning_events', 'learning_cases', 'learning_reviews'):
                self.assertEqual(conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'], 0)
        self.assertFalse(self.client.get('/api/learning/status').json()['consent'])

    def test_other_user_cannot_read_delete_submit_or_capture_my_job(self):
        self.select(); case = self.case()
        other = TestClient(app); other.get('/api/learning/status')
        other.put('/api/learning/consent', json={'enabled': True, 'policy_version': POLICY_VERSION})
        self.assertEqual(other.get('/api/learning/cases').json()['items'], [])
        self.assertEqual(other.get('/api/learning/events').json()['items'], [])
        self.assertEqual(other.post(f"/api/learning/cases/{case['case_id']}/submit", json={'expected_version': 0}).status_code, 404)
        r = other.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json={'marks': [], **self.metadata()})
        self.assertEqual(r.status_code, 403)
        other.delete('/api/learning/records')
        self.assertEqual(len(self.client.get('/api/learning/events').json()['items']), 1)

    def test_jsonl_pagination_and_search_preserve_source_and_doc_split(self):
        self.select(); self.select()
        first = self.client.get('/api/learning/export.jsonl?limit=1')
        self.assertEqual(first.status_code, 200, first.text)
        cursor = first.headers['X-Next-Cursor']; self.assertTrue(cursor)
        second = self.client.get('/api/learning/export.jsonl?limit=1&after=' + cursor)
        a, b = json.loads(first.text), json.loads(second.text)
        self.assertNotEqual(a['event_id'], b['event_id'])
        self.assertEqual(a['dataset_split'], b['dataset_split'])
        self.assertEqual(second.headers['X-Export-Complete'], 'true')
        self.assertIn('dictionary_provenance', a['cases'][0]['snapshot'])
        self.assertNotIn('private-name.pdf', first.text)
        self.assertEqual(len(self.client.get('/api/learning/cases?q=낭독').json()['items']), 2)

    def test_data_survives_session_removal_and_new_process(self):
        self.select()
        job = SESSIONS.pop(self.job['id'])
        try:
            self.assertEqual(len(self.client.get('/api/learning/events').json()['items']), 1)
            # A fresh interpreter/connection represents an app process replaced at deployment.
            script = "import os,psycopg; c=psycopg.connect(os.environ['LEARNING_TEST_DATABASE_URL']); print(c.execute('SELECT count(*) FROM '+os.environ['TEST_SCHEMA']+'.learning_events').fetchone()[0]); c.close()"
            result = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'TEST_SCHEMA': self.schema}, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), '1')
        finally:
            SESSIONS[self.job['id']] = job

    def test_database_failure_does_not_apply_edit(self):
        before = editable(self.job)
        @contextmanager
        def unavailable():
            raise HTTPException(503, 'simulated database outage')
            yield
        with patch.object(db, 'connection', unavailable):
            r = self.client.patch(f"/api/jobs/{self.job['id']}/state", headers={'X-Learning-Record': 'true'}, json={'marks': [], **self.metadata()})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(editable(self.job), before)
        self.assertEqual(self.client.get('/api/learning/events').json()['items'], [])


if __name__ == '__main__':
    unittest.main()
