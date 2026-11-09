"""Natural sentence regressions found after isolated morphology/number tests.

All pronunciation records below come from the already checked-in official API
captures. A lookup absent from those captures is not new live-dictionary proof.
"""
from copy import deepcopy
import unittest

from backend.main import analyze_job
from backend.linguistics import occurrence_plans
from test_morphology_regression import ReplayDictionary, job_for
from test_numeric_api import NumericReplay, numeric_job


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
    async def test_spaced_counters_use_their_own_immediately_following_morphemes(self):
        _, job = numeric_job('신입 부원 2 명을 선발하고 상품 4 개를 준비한다.')
        job['occurrences'] = [o for o in job['occurrences'] if o['text'] in ('2', '4')]
        job['_dictionary'] = CombinedReplay()
        await analyze_job(job)
        for occurrence, expected in zip(job['occurrences'], ('두 명을', '네 개를')):
            with self.subTest(surface=occurrence['text']):
                self.assertEqual(occurrence['status'], 'long')
                selected = occurrence['numeric']['selected_option_id']
                reading = next(o for o in occurrence['numeric']['options'] if o['id'] == selected)
                self.assertEqual(reading['reading'], expected)
                self.assertEqual(reading['system'], 'native')
                following = [s for s in reading['segments'] if s['role'] in ('unit', 'particle')]
                self.assertTrue(following)
                for segment in following:
                    self.assertEqual((segment['source_start'], segment['source_end']), (len(occurrence['text']),) * 2)
                    self.assertFalse(segment['exact'], 'The unit is not a glyph inside the numeric occurrence')
                    self.assertEqual(segment['source_location'], 'following_context')
                marks = [m for m in job['marks'] if m.get('occurrence_id') == occurrence['id']]
                self.assertEqual([m['glyph_id'] for m in marks], [occurrence['glyph_ids'][0]])

    async def test_repeated_identical_digits_do_not_share_a_different_occurrences_unit(self):
        for sentence, units, states in [
            ('2 번 방문하고 2 명을 뽑는다.', ('번', '명'), ('unresolved', 'long')),
            ('2 명을 뽑고 2 번 방문한다.', ('명', '번'), ('long', 'unresolved')),
        ]:
            with self.subTest(sentence=sentence):
                _, job = numeric_job(sentence)
                job['occurrences'] = [o for o in job['occurrences'] if o['text'] == '2']
                job['_dictionary'] = CombinedReplay()
                await analyze_job(job)
                self.assertEqual(tuple(o['status'] for o in job['occurrences']), states)
                for occurrence, unit in zip(job['occurrences'], units):
                    self.assertTrue(occurrence['numeric']['options'])
                    self.assertTrue(all(any(s['text'] == unit for s in option['segments'] if s['role'] == 'unit')
                                        for option in occurrence['numeric']['options']))

    async def test_spaced_arbitrary_noun_prefixes_do_not_become_units(self):
        for sentence in ('2 명예 회장', '4 개발 계획', '4 개구리'):
            with self.subTest(sentence=sentence):
                _, job = numeric_job(sentence)
                job['occurrences'] = [job['occurrences'][0]]
                job['_dictionary'] = CombinedReplay()
                await analyze_job(job)
                self.assertEqual(job['occurrences'][0]['status'], 'unresolved')
                self.assertEqual(job['marks'], [])

    async def test_punctuation_after_a_spaced_counter_is_a_word_boundary(self):
        for sentence in ('부원은 2 명을, 상품은 남긴다.', '상품은 4 개를. 다음은 부원이다.'):
            with self.subTest(sentence=sentence):
                _, job = numeric_job(sentence)
                job['occurrences'] = [o for o in job['occurrences'] if o['text'] in ('2', '4')]
                job['_dictionary'] = CombinedReplay()
                await analyze_job(job)
                self.assertEqual(job['occurrences'][0]['status'], 'long')
                self.assertEqual(len(job['marks']), 1)

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
