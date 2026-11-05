"""Integration checks for real PDF processing; dictionary responses are tested separately.

These generated fixtures are not linguistic accuracy benchmarks.
Run: python3 -m unittest discover -s tests -v
"""
import io
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient


def make_pdf(*, mixed=False, rotated=False, encrypted=False):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 110), "오늘의 뉴스입니다. 눈이 내립니다. 밤이 깊어집니다.",
                     fontname="korea", fontsize=15)
    page.insert_text((72, 145), "알다 알아 끌다 끌어 사고 4개 0.35초", fontname="korea", fontsize=15)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 95, 140, 115),
                      "uri": "https://example.com/original"})
    annotation = page.add_text_annot((55, 180), "기존 메모 보존")
    annotation.set_info(title="original")
    annotation.update()
    if rotated:
        page.set_cropbox(fitz.Rect(20, 30, 575, 812))
        page.set_rotation(90)
    if mixed:
        document.new_page(width=400, height=500)
    if encrypted:
        output = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                  owner_pw="owner-secret", user_pw="reader-secret")
    else:
        output = document.tobytes()
    document.close()
    return output


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.main import app
        cls.client = TestClient(app)

    def setUp(self):
        self.jobs = []

    def tearDown(self):
        for job_id in self.jobs:
            self.client.delete(f"/api/jobs/{job_id}")

    def upload(self, content=None, name="대본.pdf"):
        response = self.client.post("/api/jobs", files={"file": (name, content or make_pdf(), "application/pdf")})
        self.assertIn(response.status_code, (200, 201), response.text[:500])
        job = response.json()
        self.jobs.append(job["id"])
        return job

    def test_upload_extracts_searchable_hangul_and_every_occurrence(self):
        job = self.upload()
        self.assertEqual(job["filename"], "대본.pdf")
        self.assertEqual(len(job["pages"]), 1)
        page = job["pages"][0]
        self.assertIn("눈이 내립니다", page["text"])
        self.assertTrue(page["glyphs"])
        self.assertTrue(job["occurrences"], "Zero extracted vocabulary does not validate analysis")
        for glyph in page["glyphs"]:
            self.assertEqual(len(glyph["bbox"]), 4)
        self.assertEqual(job["marks"], [], "Upload must not invent dictionary-confirmed marks")

    def test_invalid_and_encrypted_files_are_rejected_distinctly(self):
        malformed = self.client.post("/api/jobs", files={"file": ("broken.pdf", b"%PDF-corrupt", "application/pdf")})
        encrypted = self.client.post("/api/jobs", files={"file": ("locked.pdf", make_pdf(encrypted=True), "application/pdf")})
        self.assertTrue(400 <= malformed.status_code < 500, malformed.text)
        self.assertTrue(400 <= encrypted.status_code < 500, encrypted.text)
        self.assertNotEqual(malformed.json(), encrypted.json())

    def test_mixed_document_identifies_blank_or_scanned_page(self):
        job = self.upload(make_pdf(mixed=True))
        self.assertEqual(len(job["pages"]), 2)
        self.assertNotEqual(job["pages"][0]["quality"], job["pages"][1]["quality"])
        self.assertEqual(job["pages"][1]["glyphs"], [])

    def test_no_key_analysis_is_honest_and_preserves_manual_marks(self):
        job = self.upload()
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["text"] == "눈")
        mark = {"id": "manual-test", "glyph_id": glyph["id"], "page": 1, "source": "manual", "dx": 0, "dy": 0, "scale": 1}
        edited = self.client.patch(f"/api/jobs/{job['id']}/state", json={"marks": [mark]})
        self.assertEqual(edited.status_code, 200, edited.text)
        with patch.dict(os.environ, {"STDICT_API_KEY": ""}):
            response = self.client.post(f"/api/jobs/{job['id']}/analyze")
            self.assertEqual(response.status_code, 200, response.text)
            result = self.client.get(f"/api/jobs/{job['id']}").json()
        self.assertEqual(result["analysis"]["state"], "waiting_key")
        self.assertFalse(any(mark["source"] == "dictionary" for mark in result["marks"]))
        self.assertTrue(all(item["status"] == "unresolved" for item in result["occurrences"]
                            if glyph["id"] not in item["glyph_ids"]))
        edited_occurrence = next(item for item in result["occurrences"] if glyph["id"] in item["glyph_ids"])
        self.assertEqual(edited_occurrence["source"], "manual")
        self.assertEqual(len(result["marks"]), 1)

    def test_export_preserves_text_geometry_links_annots_and_embeds_log(self):
        source = make_pdf(rotated=True, mixed=True)
        job = self.upload(source)
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["text"] == "눈")
        mark = {"id": "manual-export", "glyph_id": glyph["id"], "page": 1, "source": "manual", "dx": 0, "dy": 0, "scale": 1}
        edited = self.client.patch(f"/api/jobs/{job['id']}/state", json={
            "marks": [mark], "settings": {"color": "#CEE4F5", "opacity": 0.65, "size": 1.1}})
        self.assertEqual(edited.status_code, 200, edited.text)
        response = self.client.get(f"/api/jobs/{job['id']}/export/pdf")
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        original = fitz.open(stream=source, filetype="pdf")
        output = fitz.open(stream=response.content, filetype="pdf")
        self.assertEqual(len(original), len(output))
        for before, after in zip(original, output):
            self.assertEqual(before.get_text(), after.get_text())
            self.assertEqual(before.rect, after.rect)
            self.assertEqual(before.cropbox, after.cropbox)
            self.assertEqual(before.rotation, after.rotation)
        self.assertTrue(any(link.get("uri") == "https://example.com/original" for link in output[0].get_links()))
        self.assertTrue(any(annot.info["title"] == "original" for annot in output[0].annots()))
        marked_page = output[0]
        marker = next(annot for annot in marked_page.annots() if annot.info["title"] == "장음 표시")
        self.assertEqual(marker.colors["stroke"], [], "A zero-width stroke still renders a PDF hairline")
        self.assertGreater(output.embfile_count(), 0)
        self.assertNotEqual(original[0].get_pixmap().samples, output[0].get_pixmap().samples,
                            "The exported PDF must contain a visible marker")
        original.close()
        output.close()
        log = self.client.get(f"/api/jobs/{job['id']}/export/md")
        csv = self.client.get(f"/api/jobs/{job['id']}/export/csv")
        self.assertEqual(log.status_code, 200)
        self.assertEqual(csv.status_code, 200)
        self.assertIn("사용자", log.content.decode("utf-8-sig"))
        self.assertGreater(len(csv.content.decode("utf-8-sig").splitlines()), 1)

    def test_rotated_glyph_boxes_fit_display_page_and_preview_is_image(self):
        job = self.upload(make_pdf(rotated=True))
        page = job["pages"][0]
        self.assertEqual(page["rotation"], 90)
        for glyph in page["glyphs"]:
            x0, y0, x1, y1 = glyph["bbox"]
            self.assertTrue(0 <= x0 <= x1 <= page["width"], glyph)
            self.assertTrue(0 <= y0 <= y1 <= page["height"], glyph)
        response = self.client.get(f"/api/jobs/{job['id']}/pages/1/preview.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_position_adjustment_matches_display_coordinates_after_rotation(self):
        job = self.upload(make_pdf(rotated=True))
        glyph = next(g for g in job["pages"][0]["glyphs"] if g["text"] == "눈")
        mark = {"id": "rotated-offset", "glyph_id": glyph["id"], "page": 1,
                "source": "manual", "dx": 7, "dy": -3, "scale": 1}
        response = self.client.patch(f"/api/jobs/{job['id']}/state", json={"marks": [mark]})
        self.assertEqual(response.status_code, 200, response.text)
        exported = self.client.get(f"/api/jobs/{job['id']}/export/pdf")
        self.assertEqual(exported.status_code, 200)
        document = fitz.open(stream=exported.content, filetype="pdf")
        page = document[0]
        circle = next(a for a in page.annots() if a.info["title"] == "장음 표시")
        visible = circle.rect * page.rotation_matrix
        x0, y0, x1, y1 = glyph["bbox"]
        self.assertAlmostEqual((visible.x0 + visible.x1) / 2, (x0 + x1) / 2 + 7, delta=0.1)
        self.assertAlmostEqual((visible.y0 + visible.y1) / 2, (y0 + y1) / 2 - 3, delta=0.1)
        document.close()

    def test_deletion_revokes_job_and_exports(self):
        job = self.upload()
        deleted = self.client.delete(f"/api/jobs/{job['id']}")
        self.assertTrue(200 <= deleted.status_code < 300)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/export/pdf").status_code, 404)

    def test_candidate_choice_undo_and_redo_restore_selection_and_marks(self):
        from backend.main import SESSIONS
        job = self.upload()
        target = next(o for o in SESSIONS[job["id"]]["occurrences"] if o["text"] == "사고")
        # Synthetic server-side evidence fixture; not an actual API entry.
        target["candidates"] = [{"id": "999999:1", "word": "사고", "origin": "합성 fixture",
                                  "pronunciations": ["사ː고"], "pronunciation": "사ː고", "inflections": [],
                                  "definition": "실제 사전 근거가 아닌 회귀 시험 응답", "url": "https://stdict.korean.go.kr/"}]
        before = self.client.get(f"/api/jobs/{job['id']}").json()
        response = self.client.post(f"/api/jobs/{job['id']}/occurrences/{target['id']}/candidate",
                                    json={"candidate_id": "999999:1"})
        self.assertEqual(response.status_code, 200, response.text[:500])
        chosen = response.json()
        self.assertEqual(len(chosen["marks"]), 1)
        snapshot = lambda state: {key: deepcopy(state[key]) for key in ("settings", "marks", "occurrences")}
        undone = self.client.patch(f"/api/jobs/{job['id']}/state", json={**snapshot(before), "restore": True})
        self.assertEqual(undone.status_code, 200, undone.text[:500])
        restored = next(o for o in undone.json()["occurrences"] if o["id"] == target["id"])
        self.assertIsNone(restored["selected_candidate_id"])
        self.assertEqual(restored["status"], "unresolved")
        self.assertEqual(undone.json()["marks"], [])
        redone = self.client.patch(f"/api/jobs/{job['id']}/state", json={**snapshot(chosen), "restore": True})
        self.assertEqual(redone.status_code, 200, redone.text[:500])
        restored = next(o for o in redone.json()["occurrences"] if o["id"] == target["id"])
        self.assertEqual(restored["selected_candidate_id"], "999999:1")
        self.assertEqual(restored["status"], "long")
        self.assertEqual(len(redone.json()["marks"]), 1)


if __name__ == "__main__":
    unittest.main()
