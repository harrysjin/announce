"""Literal noun + particle evidence, using captured official noun records.

Analyzer tags propose boundaries; the dictionary's explicit noun POS and all
admissible meanings still establish length. No suffix stripping is permitted.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

import fitz

from backend.linguistics import form_consensus, form_evidence
from backend.main import analyze_job
from backend.pdf import output_pdf, preservation_signature
from test_morphology_regression import ENTRIES, PREVIOUS, job_for, plan_for


def token(form, tag, start, end):
    return {"form": form, "tag": tag, "start": start, "end": end, "surface": form}


def noun_plan(surface="보도부를", *, tokens=None, pos="NNG"):
    return {"kind": "nominal_particle", "lemma": "보도부", "pos": pos,
            "base_start": 0, "base_end": 3, "particle": surface[3:],
            "tokens": tokens or [token("보도부", pos, 0, 3), token("를", "JKO", 3, 4)]}


class NominalParticlePlanTests(unittest.TestCase):
    def test_natural_context_restores_complete_literal_noun_before_particle(self):
        plan = plan_for("보도부를", context="보도부를 통해 소식을 전합니다.")
        self.assertEqual(plan["kind"], "nominal_particle")
        self.assertEqual((plan["lemma"], plan["base_start"], plan["base_end"], plan["particle"]),
                         ("보도부", 0, 3, "를"))

    def test_nnp_noun_particle_is_a_whole_stem_hypothesis(self):
        tokens = [token("보도부", "NNP", 0, 3), token("를", "JKO", 3, 4)]
        with patch("backend.linguistics.line_analysis", return_value=tokens):
            plan = plan_for("보도부를")
        self.assertEqual(plan["kind"], "nominal_particle")
        self.assertEqual(plan["lemma"], "보도부")
        self.assertEqual(plan["pos"], "NNP")


class NominalParticleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.candidate = deepcopy(ENTRIES["보도부"][0])

    def test_explicit_official_noun_keeps_length_with_literal_particle(self):
        evidence = form_evidence(self.candidate, "보도부를", noun_plan())
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["positions"], [0])
        self.assertEqual(evidence["evidence_type"], "rule_inference")
        self.assertEqual(evidence["pronunciations"], ["보ː도부"])
        self.assertEqual(evidence["pronunciation_scope"], "보도부")
        self.assertIn("조사", evidence["explanation"])
        self.assertEqual([r["id"] for r in evidence["applied_rules"]], ["standard-pronunciation-6"])

    def test_chained_literal_particles_and_multiple_noun_tokens(self):
        tokens = [token("보도", "NNG", 0, 2), token("부", "NNG", 2, 3),
                  token("만", "JX", 3, 4), token("으로", "JKB", 4, 6), token("는", "JX", 6, 7)]
        evidence = form_evidence(self.candidate, "보도부만으로는", noun_plan("보도부만으로는", tokens=tokens))
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["positions"], [0])

    def test_missing_or_nonnoun_dictionary_pos_never_supplies_nominal_rule(self):
        for pos in (None, "", "동사", "대명사", "수사", "부사", "접사"):
            with self.subTest(pos=pos):
                candidate = deepcopy(self.candidate)
                candidate["pos"] = pos
                self.assertIsNone(form_evidence(candidate, "보도부를", noun_plan()))

    def test_copulas_suffixes_overlaps_gaps_and_changed_tokens_abstain(self):
        invalid = [
            [token("보도부", "NNG", 0, 3), token("를", "VCP", 3, 4)],
            [token("보도부", "NNG", 0, 3), token("를", "XSN", 3, 4)],
            [token("보도부", "NNG", 0, 3), token("를", "EC", 3, 4)],
            [token("보도부", "NNG", 0, 3), token("를", "JKO", 2, 4)],
            [token("보도", "NNG", 0, 2), token("를", "JKO", 3, 4)],
            [token("보도부", "NNG", 0, 3), token("을", "JKO", 3, 4)],
            [token("보도", "NNG", 0, 2), token("부", "XSN", 2, 3), token("를", "JKO", 3, 4)],
        ]
        for tokens in invalid:
            with self.subTest(tokens=tokens):
                self.assertIsNone(form_evidence(self.candidate, "보도부를", noun_plan(tokens=tokens)))

    def test_direct_actual_form_pronunciation_precedes_nominal_inference(self):
        candidate = deepcopy(self.candidate)
        candidate["inflections"] = [{"form": "보도부를", "pronunciations": ["보도부를"]}]
        evidence = form_evidence(candidate, "보도부를", noun_plan())
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["positions"], [])
        self.assertEqual(evidence["evidence_type"], "dictionary_inflection")

    def test_consensus_requires_every_noun_meaning_and_records_rule_scope(self):
        other = {**deepcopy(self.candidate), "id": "controlled-second-meaning"}
        agreed = form_consensus([self.candidate, other], "보도부를", noun_plan())
        self.assertIsNotNone(agreed)
        self.assertEqual(agreed["evidence_type"], "rule_consensus")
        self.assertEqual(agreed["evidence_candidates"], [self.candidate["id"], other["id"]])
        other.update(pronunciations=["보도부"], pronunciation="보도부")
        self.assertIsNone(form_consensus([self.candidate, other], "보도부를", noun_plan()))


class NominalParticleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_natural_noun_particle_marks_original_first_glyph(self):
        source, job = job_for("보도부를", context="보도부를 통해 소식을 전합니다.", rotation=90)
        pages_before = deepcopy(job["pages"])
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertIn("보도부", job["_dictionary"].queries)
        self.assertEqual(occurrence["lemma"], "보도부")
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(occurrence["source"], "rule")
        self.assertEqual(occurrence["evidence_type"], "rule_inference")
        self.assertEqual(occurrence["candidates"], ENTRIES["보도부"])
        self.assertEqual(job["marks"][0]["glyph_id"], occurrence["glyph_ids"][0])
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["id"] == job["marks"][0]["glyph_id"])
        self.assertEqual(glyph["text"], "보")
        self.assertEqual(job["pages"], pages_before)
        exported = output_pdf(source, job)
        with fitz.open(stream=source, filetype="pdf") as before, fitz.open(stream=exported, filetype="pdf") as after:
            self.assertEqual(preservation_signature(before), preservation_signature(after))
            marked_page = after[0]
            annotation = next(marked_page.annots())
            self.assertTrue(annotation.rect.intersects(fitz.Rect(glyph["pdf_bbox"])))
            self.assertIn("조사", annotation.info["content"])

    async def test_known_official_common_noun_survives_nnp_tag(self):
        _, job = job_for("보도부를")
        tokens = [token("보도부", "NNP", 0, 3), token("를", "JKO", 3, 4)]
        with patch("backend.linguistics.line_analysis", return_value=tokens):
            await analyze_job(job)
        self.assertEqual(job["occurrences"][0]["status"], "long")
        self.assertEqual(job["occurrences"][0]["source"], "rule")

    async def test_official_homonym_collision_and_person_name_context_abstain(self):
        for target, context in [("말의", "말의 의미를 살핍니다."), ("기쁨", "기쁨 씨")]:
            with self.subTest(target=target):
                _, job = job_for(target, context=context)
                await analyze_job(job)
                self.assertEqual(job["occurrences"][0]["status"], "unresolved")
                self.assertEqual(job["marks"], [])

    async def test_actual_conflicting_noun_meanings_do_not_gain_length(self):
        _, job = job_for("말을")
        await analyze_job(job)
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["marks"], [])
        self.assertTrue(PREVIOUS["말"])

    async def test_unknown_nnp_particle_never_looks_up_arbitrary_prefix(self):
        _, job = job_for("사회혁신융합전공을")
        tokens = [token("사회혁신융합전공", "NNP", 0, 8), token("을", "JKO", 8, 9)]
        with patch("backend.linguistics.line_analysis", return_value=tokens):
            await analyze_job(job)
        self.assertNotIn("사회", job["_dictionary"].queries)
        self.assertNotIn("사", job["_dictionary"].queries)
        self.assertEqual(job["marks"], [])


if __name__ == "__main__":
    unittest.main()
