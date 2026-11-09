"""Arabic-number regressions using captured official candidate responses.

Reading selection and dictionary/rule evidence are separate. These controlled
expressions are not a benchmark of semantic number interpretation.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from backend.numbers import number_plan, evaluate_number


FIXTURE = Path(__file__).parent / 'fixtures' / 'numeric_dictionary_2026-11-08.json'


class NumberDictionary:
    def __init__(self, overrides=None):
        self.entries = deepcopy(json.loads(FIXTURE.read_text())['entries'])
        self.entries.update(overrides or {})
        self.queries = []

    async def lookup(self, word):
        self.queries.append(word)
        return deepcopy(self.entries.get(word, []))


def option(plan, system):
    return next(item for item in plan['options'] if item['system'] == system)


class NumberPlanTests(unittest.TestCase):
    def test_bare_digits_and_counter_index_collision_offer_reading_choice(self):
        for surface in ('2', '4', '5', '2번', '772번'):
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                self.assertGreaterEqual(len(plan['options']), 2)
                self.assertIsNone(plan.get('default_option_id'))

    def test_positional_numeral_mapping_retains_original_digit_offsets(self):
        plan = number_plan('24')
        sino = option(plan, 'sino')
        self.assertEqual(sino['reading'], '이십사')
        first = [a for a in sino['alignment'] if a['reading_start'] == 0 and a['exact']]
        self.assertEqual([(a['source_start'], a['source_end']) for a in first], [(0, 1)])
        for alignment in sino['alignment']:
            self.assertTrue(0 <= alignment['source_start'] < alignment['source_end'] <= 2)
            self.assertTrue(0 <= alignment['reading_start'] < alignment['reading_end'] <= 3)

    def test_counter_readings_are_native_and_multiple_syllable_digits_are_not_exact(self):
        for surface, prefix in [('2명', '두'), ('4개', '네'), ('5개', '다섯')]:
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                chosen = next(o for o in plan['options'] if o['id'] == plan['default_option_id'])
                self.assertEqual(chosen['system'], 'native')
                self.assertTrue(chosen['reading'].startswith(prefix))
                if surface == '5개':
                    source = [a for a in chosen['alignment'] if a['source_start'] == 0]
                    self.assertFalse(any(a['exact'] for a in source), 'A single 5 does not uniquely identify one of 다/섯')

    def test_expression_categories_and_reviewable_alternatives(self):
        expected = {'2026년': {'year'}, '0.35초': {'decimal'}, '10:25': {'time'},
                    '010-2456-7788': {'phone'}, '25%': {'percent'}, '2500원': {'money'},
                    '11월8일': {'date'}, '제2': {'ordinal'}}
        for surface, kinds in expected.items():
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                self.assertIn(plan['kind'], kinds)
                self.assertTrue(plan['options'] or plan.get('reason'))


class NumberEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_reading_never_auto_confirms(self):
        for surface in ('2', '4', '5', '2번', '772번'):
            with self.subTest(surface=surface):
                result = await evaluate_number(number_plan(surface), NumberDictionary())
                self.assertEqual(result['status'], 'unresolved')
                self.assertEqual(result['positions'], [])

    async def test_official_sino_two_four_five_have_initial_long_evidence(self):
        for digit, reading, hanja in [('2', '이', '二'), ('4', '사', '四'), ('5', '오', '五')]:
            with self.subTest(digit=digit):
                plan = number_plan(digit)
                result = await evaluate_number(plan, NumberDictionary(), option(plan, 'sino')['id'])
                self.assertEqual(result['status'], 'long')
                self.assertEqual(result['positions'], [0])
                self.assertEqual(result['pronunciation_scope'], reading)
                self.assertEqual(result['evidence_type'], 'numeric_dictionary')
                evidence = [c for c in result['candidates'] if c['id'] in result['evidence_candidates']]
                self.assertTrue(evidence)
                self.assertTrue(all(c.get('pos') == '수사' and hanja in (c.get('hanja') or c.get('origin', '')) for c in evidence))
                for c in evidence:
                    for key in ('pos', 'definition', 'pronunciations', 'url', 'queried_at'):
                        self.assertTrue(c.get(key), key)

    async def test_other_sino_digits_do_not_gain_false_long_marks(self):
        for digit in ('1', '3', '6', '7', '8', '9'):
            with self.subTest(digit=digit):
                plan = number_plan(digit)
                result = await evaluate_number(plan, NumberDictionary(), option(plan, 'sino')['id'])
                self.assertEqual(result['status'], 'none')
                self.assertEqual(result['positions'], [])

    async def test_compound_numerals_only_initial_syllable_can_be_long(self):
        for surface in ('20', '40', '50', '24', '25', '45'):
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                result = await evaluate_number(plan, NumberDictionary(), option(plan, 'sino')['id'])
                self.assertEqual(result['status'], 'long')
                self.assertEqual(result['positions'], [0], 'Later 四/五 cannot receive copied long marks')
                self.assertEqual(result['evidence_type'], 'numeric_dictionary' if surface in ('20', '40', '50') else 'numeric_rule')
                if result['evidence_type'] == 'numeric_rule':
                    self.assertTrue(result['applied_rules'])

    async def test_native_counter_requires_native_dictionary_evidence(self):
        for surface in ('2명', '4개', '5개'):
            with self.subTest(surface=surface):
                dictionary = NumberDictionary()
                result = await evaluate_number(number_plan(surface), dictionary)
                self.assertIn(result['status'], ('none', 'long'))
                native = {'2명': '두', '4개': '네', '5개': '다섯'}[surface]
                self.assertIn(native, dictionary.queries)
                self.assertTrue(result['length_evidence'])
                if surface == '5개':
                    self.assertEqual(result['status'], 'none')
                    self.assertEqual(result['positions'], [], 'An uncertain one-digit/two-syllable mapping cannot receive a circle')
                else:
                    self.assertEqual(result['status'], 'long', 'Official 두/네 determiners are long')
                    self.assertEqual(result['positions'], [0])

    async def test_unknown_dictionary_or_unmappable_long_stays_unresolved(self):
        plan = number_plan('2')
        result = await evaluate_number(plan, NumberDictionary({'이': []}), option(plan, 'sino')['id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['positions'], [])
        # Deliberately remove the original-digit correspondence from a valid
        # reading: source length cannot be guessed from reading offset alone.
        broken = deepcopy(plan)
        option(broken, 'sino')['alignment'] = []
        result = await evaluate_number(broken, NumberDictionary(), option(broken, 'sino')['id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['positions'], [])

    async def test_ambiguous_or_multi_digit_long_alignment_is_not_guessed(self):
        for spans in ([{'reading_start': 0, 'reading_end': 1, 'source_start': 0, 'source_end': 2, 'exact': True}],
                      [{'reading_start': 0, 'reading_end': 1, 'source_start': 0, 'source_end': 1, 'exact': True},
                       {'reading_start': 0, 'reading_end': 1, 'source_start': 1, 'source_end': 2, 'exact': True}]):
            with self.subTest(spans=spans):
                plan = number_plan('24')
                chosen = option(plan, 'sino')
                chosen['alignment'] = spans
                result = await evaluate_number(plan, NumberDictionary(), chosen['id'])
                self.assertEqual(result['status'], 'unresolved')
                self.assertEqual(result['positions'], [])

    async def test_phone_decimal_time_uncertainty_never_creates_arbitrary_marks(self):
        for surface in ('010-2456-7788', '0.35초', '10:25'):
            with self.subTest(surface=surface):
                result = await evaluate_number(number_plan(surface), NumberDictionary())
                self.assertTrue(all(surface[p].isdigit() for p in result['positions']))
                if number_plan(surface).get('default_option_id') is None:
                    self.assertEqual(result['status'], 'unresolved')
                    self.assertEqual(result['positions'], [])
                elif result['status'] == 'unresolved':
                    self.assertEqual(result['positions'], [])


if __name__ == '__main__':
    unittest.main()
