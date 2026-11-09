"""Numeric punctuation must not swallow ordinary word/list boundaries."""
import unittest
from test_numeric_api import numeric_pdf
from backend.pdf import extract


class NumericBoundaryTests(unittest.TestCase):
    def test_only_valid_numeric_grouping_consumes_colons_and_commas(self):
        _, occurrences = extract(numeric_pdf('사과,배 안내:방송 4개,5개 1,234원 10:25'))
        self.assertEqual([o['text'] for o in occurrences],
                         ['사과','배','안내','방송','4개','5개','1,234원','10:25'])
