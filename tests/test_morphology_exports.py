"""Exports retain candidate evidence without choosing an ambiguous meaning."""
import csv
import io
import unittest

from backend.main import analyze_job
from backend.pdf import csv_export, verification
from test_morphology_regression import ENTRIES, job_for


class MorphologyExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_derived_verb_exports_every_candidate_as_candidate(self):
        _, job = job_for("선발하며")
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertEqual(occurrence["status"], "unresolved")
        self.assertIsNone(occurrence["selected_candidate_id"])
        markdown = verification(job)
        self.assertIn("기본형·전체 단어 후보 조회 기록", markdown)
        self.assertIn("특정 뜻을 선택한 결과가 아닙니다", markdown)
        csv_row = next(csv.DictReader(io.StringIO(csv_export(job).decode("utf-8-sig"))))
        self.assertEqual(csv_row["선택한 뜻"], "")
        details = csv_row["기본형·전체 단어 후보 조회 기록"]
        for candidate in ENTRIES["선발하다"]:
            for field in ("id", "word", "pos", "definition", "pronunciation", "url", "queried_at"):
                self.assertIn(candidate[field], markdown, field)
                self.assertIn(candidate[field], details, field)
        self.assertIn("후보", details)


if __name__ == "__main__":
    unittest.main()
