"""Analysis logic against explicitly synthetic evidence, never a live API claim."""
from copy import deepcopy
import unittest

import fitz

from backend.dictionary import DictionaryError
from backend.main import analyze_job, mark_candidate
from backend.pdf import extract


def candidate(word, reading, *, code="999999", inflections=None):
    return {"id": code + ":1", "target_code": code, "sense_code": "1", "word": word,
            "display_word": word, "origin": "시험 fixture", "pronunciations": [reading],
            "pronunciation": reading, "definition": "합성 테스트 응답이며 실제 사전 근거가 아님",
            "inflections": inflections or [], "url": "https://stdict.korean.go.kr/", "queried_at": "fixture"}


class FixtureDictionary:
    def __init__(self, entries=None, failing=False):
        self.entries = entries or {}
        self.failing = failing
        self.queries = []

    async def lookup(self, word):
        self.queries.append(word)
        if self.failing:
            raise DictionaryError("합성 fixture 연결 실패")
        return deepcopy(self.entries.get(word, []))


def make_job(text, entries=None, failing=False):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 100), text, fontname="korea", fontsize=12)
    pages, occurrences = extract(document.tobytes())
    document.close()
    return {"id": "analysis-fixture", "pages": pages, "occurrences": occurrences, "marks": [],
            "settings": {"color": "#CEE4F5", "opacity": 0.6, "size": 1},
            "analysis": {"state": "running", "done": 0, "total": len(occurrences), "message": "fixture"},
            "history": [], "revision": 0, "_dictionary": FixtureDictionary(entries, failing)}


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_sense_exact_match_marks_only_long_syllable(self):
        job = make_job("가상", {"가상": [candidate("가상", "가ː상")]})
        await analyze_job(job)
        self.assertEqual(job["analysis"]["state"], "complete")
        self.assertEqual(job["analysis"]["done"], 1)
        self.assertEqual(job["occurrences"][0]["status"], "long")
        self.assertEqual(len(job["marks"]), 1)
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["id"] == job["marks"][0]["glyph_id"])
        self.assertEqual(glyph["text"], "가")

    async def test_homonyms_require_per_occurrence_choice(self):
        long = candidate("사고", "사ː고", code="999998")
        short = candidate("사고", "사고", code="999997")
        job = make_job("사고 사고", {"사고": [long, short]})
        await analyze_job(job)
        self.assertEqual(job["marks"], [])
        self.assertTrue(all(o["reason_code"] == "ambiguous" for o in job["occurrences"]))
        mark_candidate(job, job["occurrences"][0], long, user_selected=True)
        mark_candidate(job, job["occurrences"][1], short, user_selected=True)
        self.assertEqual([o["status"] for o in job["occurrences"]], ["long", "none"])
        self.assertEqual(len(job["marks"]), 1)
        self.assertEqual(job["occurrences"][0]["source"], "manual")

    async def test_supplied_inflection_can_shorten_or_keep_length(self):
        shorten = candidate("알다", "알ː다", inflections=[{"form": "알아", "pronunciations": ["아라"]}])
        exception = candidate("끌다", "끌ː다", code="999998",
                              inflections=[{"form": "끌어", "pronunciations": ["끄ː러"]}])
        job = make_job("알아 끌어", {"알아": [shorten], "끌어": [exception]})
        await analyze_job(job)
        self.assertEqual([o["status"] for o in job["occurrences"]], ["none", "long"])
        self.assertEqual(len(job["marks"]), 1)
        self.assertEqual(job["marks"][0]["occurrence_id"], job["occurrences"][1]["id"])

    async def test_candidate_discovery_does_not_inherit_base_length(self):
        job = make_job("가상을", {"가상": [candidate("가상", "가ː상")]})
        await analyze_job(job)
        self.assertIn("가상", job["_dictionary"].queries)
        self.assertEqual(job["marks"], [])
        self.assertEqual(job["occurrences"][0]["reason_code"], "inflection_unverified")

    async def test_particle_form_does_not_silently_select_unrelated_exact_entry(self):
        # Synthetic evidence for the collision observed during the live check.
        job = make_job("말의", {"말의": [candidate("말의", "마리")],
                               "말": [candidate("말", "말ː", code="999998")]})
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertEqual(occurrence["status"], "unresolved")
        self.assertEqual(occurrence["reason_code"], "surface_ambiguous")
        self.assertIsNone(occurrence["selected_candidate_id"])
        self.assertEqual(len(occurrence["candidates"]), 2)
        self.assertEqual(job["marks"], [])

    async def test_one_syllable_base_candidates_are_not_omitted(self):
        job = make_job("눈이", {"눈": [candidate("눈", "눈ː")]})
        await analyze_job(job)
        self.assertIn("눈", job["_dictionary"].queries)
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["marks"], [])

    async def test_numbers_discover_readings_without_inventing_marks_or_splitting_decimal(self):
        job = make_job("4개 0.35초", {"4개": [candidate("4개", "네ː개")]})
        await analyze_job(job)
        self.assertEqual([o["text"] for o in job["occurrences"]], ["4개", "0.35초"])
        self.assertTrue(all(o["status"] == "unresolved" for o in job["occurrences"]))
        self.assertEqual(job["marks"], [])
        self.assertTrue(all(o.get("numeric") for o in job["occurrences"]), "Numbers need reviewable reading plans")
        self.assertTrue(job["_dictionary"].queries, "Numeric readings must consult official word pronunciations")

    async def test_lookup_error_has_failed_state_not_short_result(self):
        job = make_job("가상", failing=True)
        await analyze_job(job)
        self.assertEqual(job["analysis"]["state"], "error")
        self.assertEqual(job["occurrences"][0]["reason_code"], "lookup_failed")
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["marks"], [])

    async def test_manual_number_choice_survives_reanalysis(self):
        job = make_job("4개")
        occurrence = job["occurrences"][0]
        occurrence.update(status="long", source="manual", reason="사용자가 직접 표시")
        job["marks"] = [{"id": "manual-number", "glyph_id": occurrence["glyph_ids"][0],
                         "page": 1, "source": "manual", "dx": 0, "dy": 0, "scale": 1}]
        await analyze_job(job)
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(occurrence["reason"], "사용자가 직접 표시")
        self.assertEqual(len(job["marks"]), 1)


if __name__ == "__main__":
    unittest.main()
