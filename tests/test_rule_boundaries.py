"""Rule scope and history provenance using recorded official candidates."""
from copy import deepcopy
import unittest

from backend.main import analyze_job, mark_candidate, patch_state, StatePatch
from test_morphology_regression import job_for, official, editable_snapshot, stored_job


class RuleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_rule_circle_remains_replaceable_after_redo(self):
        source, job = job_for('선발하며')
        await analyze_job(job)
        before = editable_snapshot(job)
        occurrence = job['occurrences'][0]
        mark_candidate(job, occurrence, official('선발하다', '選拔 · 하다')[0], user_selected=True)
        selected = editable_snapshot(job)
        with stored_job(source, job):
            patch_state(job['id'], StatePatch(**before, restore=True))
            patch_state(job['id'], StatePatch(**selected, restore=True))
        mark_candidate(job, occurrence, official('선발하다', '先發 · 하다')[0], user_selected=True)
        self.assertEqual(occurrence['status'], 'none')
        self.assertEqual(job['marks'], [])

    async def test_unedited_automatic_results_keep_source_and_meaning_after_restore(self):
        for word, overrides in [('보도부', None), ('선발하며', {'선발하다': official('선발하다', '選拔 · 하다')})]:
            with self.subTest(word=word):
                source, job = job_for(word, overrides=overrides)
                await analyze_job(job)
                before = deepcopy(job['occurrences'][0])
                snapshot = editable_snapshot(job)
                with stored_job(source, job):
                    result = patch_state(job['id'], StatePatch(**snapshot, restore=True))
                after = result['occurrences'][0]
                for key in ('source', 'meaning_status', 'reason', 'reason_code', 'evidence_type', 'applied_rules'):
                    self.assertEqual(after[key], before[key], key)

    async def test_vowel_endings_contractions_and_changed_stems_need_actual_form_evidence(self):
        for surface, overrides in [('선발하였으며', {'선발하다': official('선발하다', '選拔 · 하다')}),
                                    ('선발한다고', {'선발하다': official('선발하다', '選拔 · 하다')}),
                                    ('끌어서', None)]:
            with self.subTest(surface=surface):
                _, job = job_for(surface, overrides=overrides)
                await analyze_job(job)
                self.assertEqual(job['occurrences'][0]['status'], 'unresolved')
                self.assertEqual(job['marks'], [])
