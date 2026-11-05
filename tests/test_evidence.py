"""Targeted regressions using audited official data; no network by default."""
import asyncio
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
import xml.etree.ElementTree as ET

import fitz

from backend.dictionary import parse_detail, pronunciation_for
from backend.main import SESSIONS, StatePatch, analyze_job, mark_candidate, patch_state
from backend.pdf import csv_export, extract, output_pdf, preservation_signature, verification

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = json.loads((FIXTURES / "dictionary_snapshot_2026-11-04.json").read_text(encoding="utf-8"))


class SnapshotDictionary:
    async def lookup(self, word):
        from backend.dictionary import DictionaryError
        if word not in SNAPSHOT:
            raise DictionaryError("시험 snapshot에 없는 조회입니다.")
        return deepcopy(SNAPSHOT[word])


def job_for(context="낭독 연습입니다.", target="낭독", *, rotation=0):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((70, 110), context, fontname="korea", fontsize=14)
    page.insert_text((70, 145), "원문 두 번째 줄을 보존합니다.", fontname="korea", fontsize=12)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 160, 150, 180), "uri": "https://example.com/original"})
    page.set_rotation(rotation)
    source = document.tobytes()
    document.close()
    pages, occurrences = extract(source)
    return source, {"id": "evidence-" + uuid.uuid4().hex, "filename": "근거시험.pdf",
                    "sha256": hashlib.sha256(source).hexdigest(), "pages": pages,
                    "occurrences": [o for o in occurrences if o["text"] == target],
                    "marks": [], "settings": {"color": "#CEE4F5", "opacity": .6, "size": 1},
                    "analysis": {"state": "running", "done": 0, "total": 1, "message": "test"},
                    "history": [], "revision": 0, "warnings": [], "_dictionary": SnapshotDictionary()}


class ActualSchemaTests(unittest.TestCase):
    def test_actual_sibling_conjugation_and_abbreviation_are_separate_forms(self):
        excerpt = ET.fromstring((FIXTURES / "stdict_hada_conjugations.xml").read_bytes())
        channel = ET.Element("channel")
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "target_code").text = excerpt.attrib["target_code"]
        word = ET.SubElement(item, "word_info")
        ET.SubElement(word, "word").text = excerpt.attrib["word"]
        for child in excerpt:
            word.append(deepcopy(child))
        # The envelope is synthetic; conju_info fields are official, with indentation normalized.
        candidate = parse_detail(ET.tostring(channel), excerpt.attrib["target_code"])[0]
        self.assertEqual(pronunciation_for(candidate, "하여")[0], ["하여"])
        self.assertEqual(pronunciation_for(candidate, "해")[0], ["해ː"])
        self.assertEqual(pronunciation_for(candidate, "하니")[0], ["하니"])
        self.assertIsNone(pronunciation_for(candidate, "해서"))


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_nangdok_consensus_does_not_choose_reading_aloud_sense(self):
        _, job = job_for()
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        self.assertEqual(occurrence["status"], "long")
        self.assertEqual(occurrence["meaning_status"], "unresolved")
        self.assertIsNone(occurrence["selected_candidate_id"])
        self.assertEqual(occurrence["evidence_type"], "candidate_consensus")
        self.assertEqual(set(occurrence["evidence_candidates"]), {c["id"] for c in SNAPSHOT["낭독"]})
        self.assertEqual(len(job["marks"]), 1)

    async def test_consensus_exports_all_sources_without_fabricating_selected_definition(self):
        source, job = job_for(rotation=90)
        await analyze_job(job)
        occurrence = job["occurrences"][0]
        record = verification(job)
        for candidate in SNAPSHOT["낭독"]:
            self.assertIn(candidate["id"], record)
            self.assertIn(candidate["url"], record)
            self.assertNotIn(candidate["definition"], record, "No meaning was selected")
        csv_row = next(csv.DictReader(io.StringIO(csv_export(job).decode("utf-8-sig"))))
        self.assertEqual(csv_row["선택한 뜻"], "")
        self.assertEqual(csv_row["뜻 상태"], "unresolved")
        for url in {c["url"] for c in SNAPSHOT["낭독"]}:
            self.assertIn(url, csv_row["출처"])
        exported = output_pdf(source, job)
        with fitz.open(stream=source, filetype="pdf") as original, fitz.open(stream=exported, filetype="pdf") as marked:
            self.assertEqual(preservation_signature(original), preservation_signature(marked))
            page = marked[0]
            annotation = next(page.annots())
            for url in {c["url"] for c in SNAPSHOT["낭독"]}:
                self.assertIn(url, annotation.info["content"])
            links = [link.get("uri") for link in page.get_links()]
            self.assertEqual(links, ["https://example.com/original"], "A consensus across entries must not link arbitrarily to the first one")
            self.assertEqual(marked.embfile_get(0).decode(), record)

    async def test_context_particle_collisions_and_proper_name_are_conservative(self):
        for context, target in [("눈이 내립니다.", "눈이"), ("말의 길이입니다.", "말의"), ("기쁨 씨가 왔습니다.", "기쁨")]:
            with self.subTest(context=context):
                _, job = job_for(context, target)
                await analyze_job(job)
                self.assertEqual(job["occurrences"][0]["status"], "unresolved")
                self.assertEqual(job["marks"], [])
                self.assertIsNone(job["occurrences"][0]["selected_candidate_id"])

    async def test_curly_quote_offset_marks_the_actual_first_hangul_glyph(self):
        _, job = job_for("“천천히” 읽습니다.", "천천히", rotation=270)
        await analyze_job(job)
        self.assertEqual(len(job["marks"]), 1)
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["id"] == job["marks"][0]["glyph_id"])
        self.assertEqual(glyph["text"], "천")
        target = job["occurrences"][0]
        self.assertEqual(job["marks"][0]["glyph_id"], target["glyph_ids"][0])

    async def test_full_snapshot_memo_edit_preserves_consensus_evidence_source(self):
        source, job = job_for()
        await analyze_job(job)
        before = deepcopy(job["occurrences"][0])
        before_marks = deepcopy(job["marks"])
        edited = deepcopy(job["occurrences"])
        edited[0]["note"] = "다음 낭독 때 다시 확인"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.pdf"
            path.write_bytes(source)
            job.update(_path=path, _touched=time.time())
            SESSIONS[job["id"]] = job
            try:
                result = patch_state(job["id"], StatePatch(settings=job["settings"], marks=deepcopy(job["marks"]), occurrences=edited))
            finally:
                SESSIONS.pop(job["id"], None)
        after = result["occurrences"][0]
        for key in ("status", "source", "meaning_status", "selected_candidate_id", "evidence_type", "evidence_candidates", "reason"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["note"], "다음 낭독 때 다시 확인")
        self.assertEqual(result["marks"], before_marks)

    async def test_failed_actual_form_does_not_claim_meaning_verified(self):
        _, job = job_for("눈이 내립니다.", "눈이")
        candidate = deepcopy(SNAPSHOT["눈"][0])
        candidate["inflections"] = []
        mark_candidate(job, job["occurrences"][0], candidate)
        self.assertEqual(job["occurrences"][0]["status"], "unresolved")
        self.assertEqual(job["occurrences"][0]["meaning_status"], "unresolved")
        self.assertIsNone(job["occurrences"][0]["selected_candidate_id"])

    async def test_candidate_undo_restores_all_consensus_sources(self):
        source, job = job_for()
        await analyze_job(job)
        snapshot = {k: deepcopy(job[k]) for k in ("settings", "marks", "occurrences")}
        chosen = next(c for c in job["occurrences"][0]["candidates"] if c["origin"] == "朗讀")
        mark_candidate(job, job["occurrences"][0], chosen, user_selected=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.pdf"
            path.write_bytes(source)
            job.update(_path=path, _touched=time.time())
            SESSIONS[job["id"]] = job
            try:
                restored = patch_state(job["id"], StatePatch(**snapshot, restore=True))
            finally:
                SESSIONS.pop(job["id"], None)
        occurrence = restored["occurrences"][0]
        self.assertIsNone(occurrence["selected_candidate_id"])
        self.assertEqual(occurrence["meaning_status"], "unresolved")
        self.assertEqual(occurrence["evidence_type"], "candidate_consensus")
        self.assertEqual(set(occurrence["evidence_candidates"]), {c["id"] for c in SNAPSHOT["낭독"]})

    async def test_cancellation_during_lookup_keeps_manual_result_and_exits_running(self):
        _, job = job_for("낭독 기쁨", "낭독")
        entered = asyncio.Event()

        class BlockingDictionary:
            async def lookup(self, word):
                entered.set()
                await asyncio.Event().wait()

        job["_dictionary"] = BlockingDictionary()
        task = asyncio.create_task(analyze_job(job))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(job["analysis"]["state"], "cancelled")
        self.assertEqual(job["marks"], [])


if __name__ == "__main__":
    unittest.main()
