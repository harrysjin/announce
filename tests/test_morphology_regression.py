"""Morphology regressions replaying audited official responses, never live by default.

Controlled candidate sets below select existing official records to isolate a
rule. They do not claim automatic word-sense disambiguation. The full captured
candidate sets are tested separately and must abstain when lengths conflict.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import fitz

from backend.linguistics import occurrence_plans
from backend.main import analyze_job
from backend.pdf import extract, output_pdf, preservation_signature


FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE = json.loads((FIXTURES / "morphology_regression_dictionary_2026-11-08.json").read_text())
ENTRIES = CAPTURE["entries"]
PREVIOUS = json.loads((FIXTURES / "dictionary_snapshot_2026-11-04.json").read_text())


class ReplayDictionary:
    def __init__(self, overrides=None):
        self.entries = deepcopy({**PREVIOUS, **ENTRIES, **(overrides or {})})
        self.queries = []

    async def lookup(self, word):
        self.queries.append(word)
        return deepcopy(self.entries.get(word, []))

    async def lookup_inflections(self, word):
        return []


def official(word, origin):
    return [deepcopy(c) for c in ENTRIES[word] if c["origin"] == origin]


def job_for(target, *, context=None, overrides=None, rotation=0):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((70, 110), context or target, fontname="korea", fontsize=14)
    page.insert_text((70, 145), "원문과 음절 좌표를 보존합니다.", fontname="korea", fontsize=12)
    page.set_rotation(rotation)
    source = document.tobytes()
    document.close()
    pages, occurrences = extract(source)
    job = {"id": "morphology-regression", "filename": "형태소회귀.pdf",
           "sha256": hashlib.sha256(source).hexdigest(), "pages": pages,
           "occurrences": [o for o in occurrences if o["text"] == target],
           "marks": [], "settings": {"color": "#CEE4F5", "opacity": .6, "size": 1},
           "analysis": {"state": "running", "done": 0, "total": 1, "message": "test"},
           "history": [], "revision": 0, "warnings": [],
           "_dictionary": ReplayDictionary(overrides)}
    assert len(job["occurrences"]) == 1, target
    return source, job


def plan_for(target, **kwargs):
    _, job = job_for(target, **kwargs)
    return occurrence_plans(job)[job["occurrences"][0]["id"]]


class MorphologyPlanTests(unittest.TestCase):
    def test_nominal_derivation_restores_verb_lemma_and_changes_pos(self):
        for surface in ("선발하며", "선발하는"):
            with self.subTest(surface=surface):
                plan = plan_for(surface, context="신입 부원을 " + surface)
                self.assertEqual(plan["lemma"], "선발하다")
                self.assertEqual(plan["pos"], "VV", "NNG + XSV is a verb, not the first noun POS")

    def test_compound_components_retain_original_syllable_offsets(self):
        plan = plan_for("보도부", context="보도부 신입 부원")
        actual = [(c["lemma"], c["pos"], c["start"], c["end"]) for c in plan.get("components", [])]
        self.assertEqual(actual, [("보도", "NNG", 0, 2), ("부", "NNG", 2, 3)])

    def test_long_compound_exposes_every_analyzed_component(self):
        plan = plan_for("사회혁신융합전공", context="사회혁신융합전공 신입생")
        actual = [(c["lemma"], c["start"], c["end"]) for c in plan.get("components", [])]
        self.assertEqual(actual, [("사회", 0, 2), ("혁신", 2, 4), ("융합", 4, 6), ("전공", 6, 8)])

    def test_proper_tag_does_not_discard_analyzer_supplied_component_boundaries(self):
        tokens = [{"form": w, "tag": pos, "start": start, "end": end, "surface": w}
                  for w, pos, start, end in [("사회", "NNP", 0, 2), ("혁신", "NNG", 2, 4),
                                             ("융합", "NNG", 4, 6), ("전공", "NNG", 6, 8)]]
        with patch("backend.linguistics.line_analysis", return_value=tokens):
            plan = plan_for("사회혁신융합전공")
        self.assertEqual(len(plan.get("components", [])), 4)


class MorphologyEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_direct_whole_word_wins_over_conflicting_components(self):
        _, job = job_for("보도부")
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(job["marks"][0]["glyph_id"], occurrence["glyph_ids"][0])
        self.assertIn(occurrence["evidence_type"], ("dictionary_direct", "candidate_consensus"))
        self.assertEqual(occurrence["candidates"][0]["word"], "보도부")

    async def test_controlled_selected_verb_sense_retains_initial_length_by_rule(self):
        # Actual 選拔 is long. Full official 先發/選拔 ambiguity is tested below.
        _, job = job_for("선발하며", overrides={"선발하다": official("선발하다", "選拔 · 하다")})
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertIn("선발하다", job["_dictionary"].queries)
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(occurrence["source"], "rule")
        self.assertEqual(occurrence["evidence_type"], "rule_inference")
        self.assertEqual(job["marks"][0]["glyph_id"], occurrence["glyph_ids"][0])
        self.assertTrue(occurrence.get("length_evidence"))
        for candidate in occurrence["candidates"]:
            for key in ("pos", "definition", "pronunciations", "url", "queried_at"):
                self.assertTrue(candidate[key], key)

    async def test_full_actual_derived_homonyms_abstain_with_candidates_retained(self):
        _, job = job_for("선발하며")
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertEqual(occurrence["status"], "unresolved")
        self.assertEqual({c["id"] for c in occurrence["candidates"]}, {c["id"] for c in ENTRIES["선발하다"]})
        self.assertIsNone(occurrence["selected_candidate_id"])
        self.assertEqual(job["marks"], [])

    async def test_controlled_compound_fallback_marks_original_first_syllable(self):
        # Whole lookup miss is controlled; component readings remain official.
        overrides = {"보도부": [], "보도": official("보도", "報道"), "부": official("부", "部")}
        _, job = job_for("보도부", overrides=overrides)
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertTrue({"보도", "부"}.issubset(job["_dictionary"].queries))
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(occurrence["source"], "rule")
        self.assertEqual(occurrence["evidence_type"], "rule_inference")
        self.assertEqual(job["marks"][0]["glyph_id"], occurrence["glyph_ids"][0])

    async def test_partial_short_component_does_not_claim_whole_expression_short(self):
        overrides = {"사회": official("사회", "社會"), "혁신": [], "융합": [], "전공": []}
        _, job = job_for("사회혁신융합전공", overrides=overrides)
        await analyze_job(job)
        self.assertIn("사회", job["_dictionary"].queries)
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["marks"], [])

    async def test_actual_social_compound_never_marks_conflicting_social_prefix_long(self):
        _, job = job_for("사회혁신융합전공")
        await analyze_job(job)
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["marks"], [])

    async def test_single_unknown_proper_morpheme_does_not_trigger_prefix_guessing(self):
        token = {"form": "사회혁신융합전공", "tag": "NNP", "start": 0, "end": 8, "surface": "사회혁신융합전공"}
        _, job = job_for("사회혁신융합전공")
        with patch("backend.linguistics.line_analysis", return_value=[token]):
            await analyze_job(job)
        self.assertNotIn("사회", job["_dictionary"].queries)
        self.assertNotIn("사", job["_dictionary"].queries)
        self.assertEqual(job["marks"], [])

    async def test_particle_homonyms_inflection_change_and_compound_exceptions_remain(self):
        for surface, status in [("말의", "unresolved"), ("알아", "none"), ("끌어", "long"),
                                ("첫눈", "none"), ("밀물", "none")]:
            with self.subTest(surface=surface):
                _, job = job_for(surface)
                await analyze_job(job)
                self.assertEqual(job["occurrences"][0]["status"], status)
                self.assertEqual(len(job["marks"]), int(status == "long"))

    async def test_rule_marker_preserves_pdf_text_geometry_and_manual_adjustment(self):
        overrides = {"선발하다": official("선발하다", "選拔 · 하다")}
        source, job = job_for("선발하며", context="“선발하며” 읽습니다.", overrides=overrides, rotation=90)
        original_pages = deepcopy(job["pages"])
        await analyze_job(job)
        self.assertEqual(len(job["marks"]), 1)
        occurrence = job["occurrences"][0]
        mark = job["marks"][0]
        self.assertEqual(mark["glyph_id"], occurrence["glyph_ids"][0])
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["id"] == mark["glyph_id"])
        self.assertEqual(glyph["text"], "선")
        self.assertEqual(job["pages"], original_pages)
        exported = output_pdf(source, job)
        with fitz.open(stream=source, filetype="pdf") as before, fitz.open(stream=exported, filetype="pdf") as after:
            self.assertEqual(preservation_signature(before), preservation_signature(after))
            annotation = next(after[0].annots())
            self.assertTrue(annotation.rect.intersects(fitz.Rect(glyph["pdf_bbox"])))
        occurrence.update(source="manual", note="선택한 뜻을 확인함")
        mark.update(source="manual", dx=1.5, dy=-2, scale=1.2)
        before_marks = deepcopy(job["marks"])
        await analyze_job(job)
        self.assertEqual(job["marks"], before_marks)
        self.assertEqual(occurrence["note"], "선택한 뜻을 확인함")


if __name__ == "__main__":
    unittest.main()
