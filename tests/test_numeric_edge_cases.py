"""Numeric regressions discovered independently of the initial example list.

The official response fixture supplies vowel lengths; expected readings below
only exercise numeral spelling, semantics, and original-character alignment.
"""
import unittest

from backend.numbers import evaluate_number, number_plan
from test_numbers import NumberDictionary, option


class NumericSemanticEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_grouped_money_preserves_written_digit_offsets(self):
        plan = number_plan('20,004원')
        self.assertEqual(plan['kind'], 'money')
        chosen = option(plan, 'sino')
        self.assertEqual(chosen['reading'], '이만사 원')
        last_digit = next(s for s in chosen['segments'] if s['text'] == '사')
        self.assertEqual((last_digit['source_start'], last_digit['source_end']), (5, 6))
        result = await evaluate_number(plan, NumberDictionary())
        self.assertEqual(result['status'], 'long')
        self.assertEqual(result['positions'], [0])

    async def test_modern_won_does_not_cite_historical_or_japanese_currency(self):
        result = await evaluate_number(number_plan('2원'), NumberDictionary())
        evidence = [c for c in result['candidates']
                    if c['id'] in result['evidence_candidates'] and c['word'] == '원']
        self.assertTrue(evidence)
        self.assertTrue(all(not (c.get('hanja') or c.get('origin')) for c in evidence))
        self.assertTrue(all('1962' in c['definition'] for c in evidence))

    async def test_honorific_people_and_minutes_require_different_reading_choices(self):
        plan = number_plan('3분', '손님 3분이 3분 동안 기다린다')
        self.assertIsNone(plan['default_option_id'])
        automatic = await evaluate_number(plan, NumberDictionary())
        self.assertEqual(automatic['status'], 'unresolved')
        native = await evaluate_number(plan, NumberDictionary(), option(plan, 'native')['id'])
        self.assertEqual(native['status'], 'long')
        self.assertEqual(native['positions'], [0])
        evidence = [c for c in native['candidates']
                    if c['id'] in native['evidence_candidates'] and c['word'] == '분']
        self.assertTrue(evidence)
        self.assertTrue(all(not (c.get('hanja') or c.get('origin')) for c in evidence))

    async def test_month_name_is_not_verified_from_a_duration_unit_entry(self):
        plan = number_plan('11월')
        self.assertIsNone(plan['default_option_id'])
        result = await evaluate_number(plan, NumberDictionary(), option(plan, 'sino')['id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['positions'], [])

    async def test_complete_native_tens_modifier_uses_its_actual_part_of_speech(self):
        result = await evaluate_number(number_plan('30개'), NumberDictionary())
        self.assertEqual(result['status'], 'none')
        evidence = [c for c in result['candidates']
                    if c['id'] in result['evidence_candidates'] and c['word'] == '서른']
        self.assertTrue(evidence)
        self.assertTrue(all(c['pos'] == '관형사' for c in evidence))

    async def test_line_keywords_cannot_choose_repeated_counter_senses(self):
        for context in ('2번 버스를 2번 탔다', '번호 2번으로 두 차례 방문했다'):
            with self.subTest(context=context):
                plan = number_plan('2번', context)
                self.assertIsNone(plan['default_option_id'])
                result = await evaluate_number(plan, NumberDictionary())
                self.assertEqual(result['status'], 'unresolved')

    async def test_invalid_calendar_month_is_not_automatically_verified(self):
        for surface in ('0월', '13월', '0일', '32일'):
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                self.assertIsNone(plan['default_option_id'])
                result = await evaluate_number(plan, NumberDictionary())
                self.assertEqual(result['status'], 'unresolved')

    async def test_unresolved_result_keeps_verified_evidence_and_applied_rules(self):
        plan = number_plan('24미터')
        result = await evaluate_number(plan, NumberDictionary(), option(plan, 'sino')['id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['positions'], [])
        self.assertTrue(result['length_evidence'])
        self.assertTrue(result['evidence_candidates'])
        self.assertTrue(result['pronunciations'])
        self.assertTrue(result['applied_rules'])

    async def test_explicit_default_choice_is_still_recorded_as_user_choice(self):
        plan = number_plan('25퍼센트')
        result = await evaluate_number(plan, NumberDictionary(), plan['default_option_id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['numeric']['choice_source'], 'user')

    async def test_overlapping_coarse_and_exact_maps_are_not_resolved_by_guessing(self):
        plan = number_plan('24')
        chosen = option(plan, 'sino')
        chosen['alignment'].append({'reading_start': 0, 'reading_end': 1,
                                    'source_start': 1, 'source_end': 2, 'exact': False})
        result = await evaluate_number(plan, NumberDictionary(), chosen['id'])
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['positions'], [])


class NumericPlanEdgeTests(unittest.TestCase):
    def test_bad_grouping_is_not_silently_normalized(self):
        for surface in ('2,00원', '20,00,004원', ',200원', '200,원'):
            with self.subTest(surface=surface):
                plan = number_plan(surface)
                self.assertFalse(plan['options'])
                self.assertIsNone(plan['default_option_id'])

    def test_identifier_reading_preserves_its_written_unit(self):
        plan = number_plan('02분')
        self.assertTrue(all(o['reading'].endswith(' 분') for o in plan['options']))

    def test_day_count_outside_calendar_range_still_offers_duration_reading(self):
        plan = number_plan('365일')
        self.assertTrue(plan['options'])
        self.assertIsNone(plan['default_option_id'])

    def test_four_place_integer_blocks_cover_zero_and_nonzero_groups(self):
        examples = {'10000': '만', '100000': '십만', '100000000': '일억',
                    '100010001': '일억일만일', '1000000000000': '일조',
                    '1000000010001': '일조일만일'}
        for digits, reading in examples.items():
            with self.subTest(digits=digits):
                self.assertEqual(option(number_plan(digits), 'sino')['reading'], reading)
        # This is an alignment invariant over 3,999 generated numeral forms,
        # not a list of vowel-length answers.
        for value in range(1, 4000):
            surface = str(value)
            chosen = option(number_plan(surface), 'sino')
            for segment in chosen['segments']:
                if segment['exact']:
                    self.assertEqual(segment['source_end'] - segment['source_start'], 1)
                    self.assertEqual(len(segment['text']), 1)
                    self.assertTrue(surface[segment['source_start']].isdigit())

    def test_padded_clock_minutes_keep_their_actual_digit_positions(self):
        plan = number_plan('02:04')
        clock = next(o for o in plan['options'] if o['id'] == 'time')
        four = next(s for s in clock['segments'] if s['text'] == '사')
        self.assertEqual((four['source_start'], four['source_end']), (4, 5))

    def test_inserted_clock_units_never_claim_an_exact_original_glyph(self):
        plan = number_plan('2:04:05')
        clock = next(o for o in plan['options'] if o['id'] == 'time')
        for segment in clock['segments']:
            if segment['role'] == 'unit':
                with self.subTest(unit=segment['text']):
                    self.assertFalse(segment['exact'])

    def test_twenty_four_hour_clock_has_sino_reading_instead_of_thirteen_native(self):
        plan = number_plan('13:04')
        readings = [o['reading'] for o in plan['options']]
        self.assertIn('십삼 시 사 분', readings)
        self.assertFalse(any(r.startswith('열세') for r in readings))


if __name__ == '__main__':
    unittest.main()
