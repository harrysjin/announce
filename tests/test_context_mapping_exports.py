"""Spaced units and inserted readings must not claim a source digit exists."""
import unittest
from backend.main import analyze_job
from backend.pdf import numeric_record
from test_morphology_regression import job_for
from test_site_reaudit import CombinedReplay


class ContextMappingExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_following_unit_is_labeled_as_context_in_the_verification_record(self):
        _, job = job_for('2', context='2 명을 뽑습니다.')
        job['_dictionary'] = CombinedReplay()
        await analyze_job(job)
        item = job['occurrences'][0]
        self.assertEqual(item['status'], 'long')
        record = numeric_record(item)
        self.assertIn('뒤 문맥 명', record)
        self.assertIn('뒤 문맥 을', record)
        self.assertNotIn('원문  [1:1]', record)
