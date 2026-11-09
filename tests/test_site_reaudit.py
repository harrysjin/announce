"""Natural sentence regressions found after isolated morphology/number tests.

All pronunciation records below come from the already checked-in official API
captures. A lookup absent from those captures is not new live-dictionary proof.
"""
from copy import deepcopy
import unittest

from backend.main import analyze_job
from backend.linguistics import occurrence_plans
from test_morphology_regression import ReplayDictionary, job_for
from test_numeric_api import NumericReplay


class CombinedReplay:
    def __init__(self):
        self.morphology = ReplayDictionary()
        self.numeric = NumericReplay()
        self.queries = []

    async def lookup(self, word):
        self.queries.append(word)
        return deepcopy(self.morphology.entries.get(word, self.numeric.entries.get(word, [])))

    async def lookup_inflections(self, word):
        return []


class NaturalSentenceReauditTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_counter_and_year_particles_keep_verified_digit_evidence(self):
        cases = [
            ('2명을', '신입 부원 2명을 선발하며 활동한다.', 'long', 'native', '명'),
            ('2명은', '신입 부원 2명은 방송을 준비한다.', 'long', 'native', '명'),
            ('4개를', '상품 4개를 준비한다.', 'long', 'native', '개'),
            ('4개는', '상품 4개는 남겨 둔다.', 'long', 'native', '개'),
            ('5개도', '상품 5개도 준비한다.', 'none', 'native', '개'),
            ('2026년에는', '2026년에는 새 부원을 뽑는다.', 'long', 'sino', '년'),
        ]
        for surface, sentence, status, system, unit in cases:
            with self.subTest(surface=surface):
                _, job = job_for(surface, context=sentence)
                job['_dictionary'] = dictionary = CombinedReplay()
                pages_before = deepcopy(job['pages'])
                await analyze_job(job)
                occurrence = job['occurrences'][0]
                self.assertEqual(occurrence['status'], status)
                selected = occurrence['numeric']['selected_option_id']
                reading = next(o for o in occurrence['numeric']['options'] if o['id'] == selected)
                self.assertEqual(reading['system'], system)
                self.assertIn(unit, dictionary.queries, 'Look up the attested unit, not unit+particle as a headword')
                self.assertEqual(job['pages'], pages_before)
                self.assertEqual([m['glyph_id'] for m in job['marks']],
                                 [occurrence['glyph_ids'][0]] if status == 'long' else [])

    async def test_arbitrary_counter_prefixes_do_not_become_unit_evidence(self):
        for surface in ('4개발', '4개구리', '2명예'):
            with self.subTest(surface=surface):
                _, job = job_for(surface)
                job['_dictionary'] = CombinedReplay()
                await analyze_job(job)
                self.assertEqual(job['occurrences'][0]['status'], 'unresolved')
                self.assertEqual(job['marks'], [])

    async def test_derived_lemma_is_present_but_real_homonym_conflict_stays_unresolved(self):
        _, job = job_for('선발하며', context='보도부원을 선발하며 활동한다.')
        job['_dictionary'] = CombinedReplay()
        plan = occurrence_plans(job)[job['occurrences'][0]['id']]
        self.assertEqual((plan['lemma'], plan['pos']), ('선발하다', 'VV'))
        await analyze_job(job)
        occurrence = job['occurrences'][0]
        self.assertEqual(occurrence['status'], 'unresolved')
        self.assertEqual({c['origin'] for c in occurrence['candidates']}, {'先發 · 하다', '選拔 · 하다'})
        self.assertEqual(job['marks'], [])
