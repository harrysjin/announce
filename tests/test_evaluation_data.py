"""Keep the historical evaluation and its dated official supplements distinct."""
import json
from pathlib import Path
import unittest
from evaluation import load_dictionary_snapshot

ROOT = Path(__file__).parent / 'fixtures'


class EvaluationDataTests(unittest.TestCase):
    def test_dated_official_supplements_supply_queries_without_inventing_missing_ones(self):
        corpus = json.loads((ROOT / 'audited_corpus.json').read_text())
        corpus['supplemental_snapshots'] = ['numeric_dictionary_2026-11-08.json', 'numeric_evaluation_dictionary_2026-11-09.json']
        words, hashes = load_dictionary_snapshot(ROOT / 'audited_corpus.json', corpus)
        self.assertTrue(words['네'])
        self.assertIn('이천이십육', words)
        self.assertEqual(words['이천이십육'], [])
        self.assertNotIn('uncaptured-query', words)
        self.assertEqual(len(hashes), 3)
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))
