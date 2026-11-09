"""Vector highlight rendering regressions; generated PDFs are not macOS tests."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

import fitz

from backend.pdf import circle_for, extract, marked_document, output_pdf, preservation_signature


def fixture(*, background=False, color=(0, 0, 0), rotation=0, image_background=False, crop=False,
            textured_background=False, existing_annotation=False):
    document = fitz.open()
    page = document.new_page(width=260, height=180)
    if background:
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    if image_background:
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 260, 180), False)
        image.clear_with(255)
        page.insert_image(page.rect, pixmap=image)
    if textured_background:
        samples = bytes(channel for y in range(180) for x in range(260)
                        for channel in (100 + x % 130, 100 + y % 130, 220))
        image = fitz.Pixmap(fitz.csRGB, 260, 180, samples, False)
        page.insert_image(page.rect, pixmap=image)
    page.insert_text((60, 90), '장음 표시', fontname='korea', fontsize=24, color=color)
    page.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(10, 10, 40, 25), 'uri': 'https://example.com/original'})
    if existing_annotation:
        note = page.add_circle_annot(fitz.Rect(190, 110, 220, 140))
        note.set_colors(stroke=(.3, .4, .2), fill=(.8, .9, .7))
        note.set_opacity(.35)
        note.set_info(title='Original reviewer', content='Original circle note')
        note.update()
    page.set_rotation(rotation)
    if crop:
        page.set_cropbox(fitz.Rect(10, 10, 250, 170))
    source = document.tobytes()
    document.close()
    pages, occurrences = extract(source)
    first = occurrences[0]
    first.update(status='long', source='dictionary', reason='공식 사전 발음',
                 evidence_type='dictionary_direct', evidence_candidates=['1:1'],
                 candidates=[{'id':'1:1','word':'장음','origin':'','pronunciations':['장ː음'],
                              'pronunciation':'장ː음','definition':'렌더링 합성 fixture',
                              'url':'https://stdict.korean.go.kr/','queried_at':'fixture'}])
    marks = [{'id':'automatic','page':1,'glyph_id':first['glyph_ids'][0],
              'occurrence_id':first['id'],'source':'dictionary','dx':0,'dy':0,'scale':1}]
    job = {'id':'rendering-fixture','filename':'선명도.pdf','sha256':hashlib.sha256(source).hexdigest(),
           'created_at':'fixture','history':[],'pages':pages,'occurrences':occurrences,
           'settings':{'color':'#CEE4F5','opacity':.6,'size':1},'marks':marks,'warnings':[]}
    return source, job


def pixels(document, *, annots=True, zoom=3):
    return document[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=annots).samples


def assert_core_ink_unchanged(test, before, after):
    original, result = pixels(before), pixels(after)
    dark = [i for i in range(0, len(original), 3) if max(original[i:i+3]) == 0]
    test.assertGreater(len(dark), 30)
    changed = sum(original[i:i+3] != result[i:i+3] for i in dark)
    test.assertEqual(changed, 0, 'Original dark glyph pixels must not be composited over')


class PDFRenderingTests(unittest.TestCase):
    def test_digit_antialias_edges_remain_unchanged_across_preview_scales(self):
        source, job = fixture(background=True)
        document = fitz.open()
        page = document.new_page(width=300, height=160)
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
        page.insert_text((72, 80), '24 2명 4개 20', fontname='korea', fontsize=26)
        source = document.tobytes()
        document.close()
        job['pages'], job['occurrences'] = extract(source)
        job['marks'] = [dict(job['marks'][0], id=f'm{i}', glyph_id=word['glyph_ids'][0],
                             occurrence_id=None, source='manual')
                        for i, word in enumerate(job['occurrences'])]
        for adjusted in (False, True):
            edited = deepcopy(job)
            if adjusted:
                edited['settings'].update(size=1.05, opacity=.55, color='#F2D4DC')
                for mark in edited['marks']:
                    mark['dx'] = 1
            with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, edited) as result:
                # At tiny thumbnail scales glyph and marker share a pixel, so
                # antialias RGB necessarily depends on the nearby background.
                for zoom in (1, 1.5, 1.65, 2, 3, 4):
                    with self.subTest(zoom=zoom, adjusted=adjusted):
                        before, after = pixels(original, zoom=zoom), pixels(result, zoom=zoom)
                        changed = sum(before[i:i+3] != after[i:i+3]
                                      for i in range(0, len(before), 3) if before[i:i+3] != b'\xff\xff\xff')
                        self.assertEqual(changed, 0, 'Protect antialias edges as well as solid glyph interiors')
                before, after = pixels(original, zoom=.2), pixels(result, zoom=.2)
                cores = [i for i in range(0, len(before), 3) if before[i:i+3] == b'\0\0\0']
                self.assertTrue(all(before[i:i+3] == after[i:i+3] for i in cores))
                self.assertEqual(preservation_signature(original), preservation_signature(result))

    def test_visible_circle_is_page_content_without_annotation_paint(self):
        source, job = fixture()
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            self.assertFalse(pixels(original, annots=False) == pixels(result, annots=False),
                                'Marker must be page vector content, not viewer-dependent annotation fill')
            self.assertTrue(pixels(result, annots=False) == pixels(result, annots=True),
                             'Evidence annotation must not repaint the vector circle')
            self.assertTrue(any(d.get('fill') for d in result[0].get_drawings()))
            assert_core_ink_unchanged(self, original, result)

    def test_simulated_viewer_normal_blend_cannot_wash_out_glyphs(self):
        source, job = fixture()
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            for annotation in result[0].annots():
                # Emulate a viewer which does not honor Multiply on Circle AP.
                result.xref_set_key(annotation.xref, 'BM', '/Normal')
                ap_type, ap_value = result.xref_get_key(annotation.xref, 'AP/N')
                if ap_type == 'xref':
                    ap_xref = int(ap_value.split()[0])
                    ap = result.xref_stream(ap_xref).replace(b'/Multiply', b'/Normal')
                    result.update_stream(ap_xref, ap)
            assert_core_ink_unchanged(self, original, result)

    def test_opaque_background_fallback_is_visible_and_preserves_ink(self):
        source, job = fixture(background=True)
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            self.assertFalse(pixels(original, annots=False) == pixels(result, annots=False),
                                'An opaque page background must not hide the marker')
            self.assertTrue(pixels(result, annots=False) == pixels(result, annots=True))
            assert_core_ink_unchanged(self, original, result)
            self.assertEqual(result[0].get_images(), [], 'Fallback must not rasterize the original page')

    def test_automatic_manual_and_adjusted_marks_share_geometry_and_rendering(self):
        source, job = fixture(background=True)
        first = job['marks'][0]
        for source_name in ('dictionary', 'rule', 'manual'):
            with self.subTest(source=source_name):
                edited = deepcopy(job)
                edited['marks'][0].update(source=source_name, dx=2.5, dy=-1.5, scale=1.1)
                edited['settings'].update(color='#F2D4DC', opacity=.45, size=1.05)
                with marked_document(source, edited) as result:
                    page = result[0]
                    circle = circle_for(edited['pages'][0]['glyphs'][0], edited['marks'][0], edited['settings'], page)
                    annotation = next(page.annots())
                    self.assertAlmostEqual((annotation.rect.x0 + annotation.rect.x1) / 2, (circle.x0 + circle.x1) / 2, delta=.1)
                    self.assertAlmostEqual((annotation.rect.y0 + annotation.rect.y1) / 2, (circle.y0 + circle.y1) / 2, delta=.1)
                    self.assertAlmostEqual(annotation.opacity, .45, delta=.01)
                    self.assertEqual(annotation.colors['stroke'], [])
                    render = pixels(result)
                if source_name == 'dictionary':
                    expected_render = render
                else:
                    self.assertEqual(render, expected_render, 'Source must not choose a different renderer')
        self.assertEqual(first['dx'], 0, 'Rendering must not mutate mark state')

    def test_export_keeps_fonts_text_layout_links_and_annotation_provenance(self):
        source, job = fixture(background=True, rotation=90)
        output = output_pdf(source, job)
        with fitz.open(stream=source, filetype='pdf') as original, fitz.open(stream=output, filetype='pdf') as result:
            self.assertEqual(preservation_signature(original), preservation_signature(result))
            self.assertEqual(extract(source)[0], extract(output)[0])
            self.assertEqual([f[1:] for f in original[0].get_fonts(full=True)], [f[1:] for f in result[0].get_fonts(full=True)])
            self.assertIn('https://example.com/original', [link.get('uri') for link in result[0].get_links()])
            self.assertIn('https://stdict.korean.go.kr/', [link.get('uri') for link in result[0].get_links()])
            annotation = next(result[0].annots())
            self.assertIn('dictionary_direct', annotation.info['content'])
            self.assertIn('https://stdict.korean.go.kr/', annotation.info['content'])
            self.assertGreater(result.embfile_count(), 0)

    def test_original_content_streams_and_font_colors_are_unchanged(self):
        source, job = fixture(background=True, color=(.75, .08, .12))
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            for xref in original[0].get_contents():
                self.assertEqual(original.xref_stream(xref), result.xref_stream(xref))
            original_colors = [span['color'] for span in original[0].get_texttrace()]
            self.assertEqual(original_colors, [span['color'] for span in result[0].get_texttrace()])
            old_pixels, new_pixels = pixels(original), pixels(result)
            # The exact, solid original glyph color occurs repeatedly in its
            # interiors, independently from antialiasing against its background.
            from collections import Counter
            solid = Counter(old_pixels[i:i+3] for i in range(0, len(old_pixels), 3)).most_common(2)[1][0]
            offsets = [i for i in range(0, len(old_pixels), 3) if old_pixels[i:i+3] == solid]
            self.assertGreater(len(offsets), 20)
            self.assertTrue(all(old_pixels[i:i+3] == new_pixels[i:i+3] for i in offsets))

    def test_original_circle_annotation_appearance_and_metadata_are_preserved(self):
        source, job = fixture(background=True, existing_annotation=True)
        output = output_pdf(source, job)
        with fitz.open(stream=source, filetype='pdf') as original, fitz.open(stream=output, filetype='pdf') as result:
            source_page, output_page = original[0], result[0]
            before = next(source_page.annots())
            after = next(annot for annot in output_page.annots() if annot.info['title'] == 'Original reviewer')
            for attribute in ('type', 'rect', 'colors', 'border', 'opacity', 'info'):
                self.assertEqual(getattr(before, attribute), getattr(after, attribute))
            before_ap = int(original.xref_get_key(before.xref, 'AP/N')[1].split()[0])
            after_ap = int(result.xref_get_key(after.xref, 'AP/N')[1].split()[0])
            self.assertEqual(original.xref_stream(before_ap), result.xref_stream(after_ap))

    def test_image_background_and_rotated_crop_preserve_ink_without_new_images(self):
        source, job = fixture(image_background=True, rotation=90, crop=True)
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            self.assertEqual(len(original[0].get_images()), len(result[0].get_images()))
            self.assertFalse(pixels(original, annots=False) == pixels(result, annots=False))
            assert_core_ink_unchanged(self, original, result)
            self.assertEqual(extract(source)[0], extract(result.tobytes())[0])

    def test_uncertain_image_background_protects_colored_glyph_boxes(self):
        source, job = fixture(textured_background=True, color=(.75, .08, .12), rotation=270, crop=True)
        with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
            old = original[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            new = result[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            self.assertTrue(old.samples != new.samples, 'Marker must remain visible on an image background')
            for glyph in job['pages'][0]['glyphs']:
                box = (fitz.Rect(glyph['bbox']) * fitz.Matrix(3, 3)).irect & old.irect
                for y in range(box.y0, box.y1):
                    start, end = (y * old.width + box.x0) * 3, (y * old.width + box.x1) * 3
                    self.assertEqual(old.samples[start:end], new.samples[start:end])
            self.assertEqual(original[0].get_images(), result[0].get_images())

    def test_adjusted_circle_geometry_matches_rotated_cropped_pages(self):
        for rotation in (0, 90, 180, 270):
            for background in (False, True):
                with self.subTest(rotation=rotation, background=background):
                    source, job = fixture(background=background, rotation=rotation, crop=True)
                    job['marks'][0].update(dx=3, dy=-2, scale=1.2)
                    with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
                        old = original[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        new = result[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        before, after = old.samples, new.samples
                        changed = [i // 3 for i in range(0, len(before), 3) if before[i:i+3] != after[i:i+3]]
                        self.assertTrue(changed)
                        painted = fitz.Rect(min(p % old.width for p in changed) / 2,
                                            min(p // old.width for p in changed) / 2,
                                            (max(p % old.width for p in changed) + 1) / 2,
                                            (max(p // old.width for p in changed) + 1) / 2)
                        expected = circle_for(job['pages'][0]['glyphs'][0], job['marks'][0],
                                              job['settings'], result[0]) * result[0].rotation_matrix
                        if background:
                            # Ink exclusion can trim the circle's extrema.
                            self.assertTrue((expected + (-1, -1, 1, 1)).contains(painted))
                        else:
                            for actual, intended in zip(painted, expected):
                                self.assertAlmostEqual(actual, intended, delta=1)
                        output_page = result[0]
                        annotation = next(output_page.annots())
                        expected_pdf = expected * output_page.derotation_matrix
                        # PyMuPDF retains a 1pt /RD review inset even for the
                        # nonpainting, zero-border metadata annotation.
                        for actual, intended in zip(annotation.rect + annotation.rect_delta, expected_pdf):
                            self.assertAlmostEqual(actual, intended, delta=.1)

    def test_mask_budget_falls_back_to_vector_text_box_protection(self):
        source, job = fixture(background=True)
        with patch('backend.pdf._MAX_MASK_TILES', 0), patch('backend.pdf._MAX_CLIP_RECTS', 1):
            with fitz.open(stream=source, filetype='pdf') as original, marked_document(source, job) as result:
                self.assertFalse(pixels(original, annots=False) == pixels(result, annots=False))
                assert_core_ink_unchanged(self, original, result)
                self.assertEqual(result[0].get_images(), [])

    def test_repeated_download_settings_and_deletion_are_consistent(self):
        source, job = fixture(background=True)
        with marked_document(source, job) as one, marked_document(source, job) as two:
            self.assertEqual(pixels(one), pixels(two))
            first_render = pixels(one)
        edited = deepcopy(job)
        edited['marks'][0].update(dx=8, dy=4, scale=1.2, source='manual')
        edited['settings'].update(opacity=.3, color='#F2D4DC', size=.8)
        with marked_document(source, edited) as result:
            self.assertNotEqual(first_render, pixels(result))
        edited['marks'] = []
        with marked_document(source, edited) as result, fitz.open(stream=source, filetype='pdf') as original:
            self.assertEqual(pixels(result), pixels(original))


class PDFClipBudgetTests(unittest.TestCase):
    def test_no_detected_ink_is_uncertain_and_protects_the_glyph_box(self):
        from backend.pdf import _InkProtection
        source, job = fixture(background=True)
        with fitz.open(stream=source, filetype='pdf') as original:
            protector = _InkProtection(original[0], job['pages'][0]['glyphs'])
            circle = circle_for(job['pages'][0]['glyphs'][0], job['marks'][0], job['settings'], original[0])
            circle += (-5, -5, 5, 5)
            blank = (0, 0, 1024, 1024, b'\xff' * (1024 * 1024 * 3), b'\xff\xff\xff', True)
            with patch.object(protector, '_tile', return_value=blank):
                clips = protector.rectangles(circle)
            box = fitz.Rect(job['pages'][0]['glyphs'][0]['pdf_bbox']) & circle
            self.assertTrue(clips)
            self.assertTrue(all(not rect.intersects(box) for rect in clips),
                            'A font another viewer can paint must not be treated as empty background')

    def test_conservative_fallback_terminates_with_a_single_rectangle_budget(self):
        from backend.pdf import _complement_rectangles
        bounds = fitz.Rect(0, 0, 100, 100)
        protected = fitz.Rect(40, 40, 60, 60)
        with patch('backend.pdf._MAX_CLIP_RECTS', 1):
            clips = _complement_rectangles(bounds, [protected])
        self.assertGreater(len(clips), 0, 'Keep visible marker area when safe space exists')
        self.assertLessEqual(len(clips), 1)
        self.assertTrue(all(not rect.intersects(protected) for rect in clips))

    def test_repeated_glyph_marks_reuse_original_page_tiles(self):
        from backend.pdf import _InkProtection
        source, job = fixture(background=True)
        with fitz.open(stream=source, filetype='pdf') as original:
            page = original[0]
            circle = circle_for(job['pages'][0]['glyphs'][0], job['marks'][0], job['settings'], page)
            protector = _InkProtection(page, job['pages'][0]['glyphs'])
            with patch.object(fitz.Page, 'get_pixmap', autospec=True, side_effect=fitz.Page.get_pixmap) as render:
                first = protector.rectangles(circle)
                rendered_tiles = render.call_count
                second = protector.rectangles(circle)
                self.assertGreater(rendered_tiles, 0)
                self.assertEqual(render.call_count, rendered_tiles, 'Repeated marks must not rasterize tiles again')
                self.assertEqual(first, second)

    def test_preview_paints_only_requested_page_and_matches_full_export(self):
        source, job = fixture(background=True)
        with fitz.open(stream=source, filetype='pdf') as first, fitz.open(stream=source, filetype='pdf') as document:
            document.insert_pdf(first)
            source = document.tobytes()
        job['pages'], job['occurrences'] = extract(source)
        job['marks'] = [dict(job['marks'][0], id=f'm{page["number"]}', page=page['number'],
                             glyph_id=page['glyphs'][0]['id'], source='manual', occurrence_id=None)
                        for page in job['pages']]
        from backend.pdf import _InkProtection
        analyzed_pages = []
        original_analysis = _InkProtection.rectangles
        def record_analysis(protector, circle):
            analyzed_pages.append(protector.page.number)
            return original_analysis(protector, circle)
        with patch.object(_InkProtection, 'rectangles', autospec=True, side_effect=record_analysis) as analysis:
            with marked_document(source, job, page_numbers={2}) as selected:
                self.assertEqual(analysis.call_count, 1, 'Thumbnail requests must not analyze other marked pages')
                self.assertEqual(analyzed_pages, [1])
                selected_render = selected[1].get_pixmap().samples
                with fitz.open(stream=source, filetype='pdf') as original:
                    self.assertEqual(pixels(original), pixels(selected))
                self.assertEqual(list(selected[0].annots() or []), [])
        with marked_document(source, job) as full:
            self.assertEqual(full[1].get_pixmap().samples, selected_render)


if __name__ == '__main__':
    unittest.main()
