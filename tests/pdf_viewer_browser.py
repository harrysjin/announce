"""Local Chromium PDF-viewer evidence, separate from app PNG tests.

Requires Playwright, Pillow, fonttools, Chromium and an installed Noto CJK TTC.
Source PDFs are generated with an embedded font before either viewer sees them;
the application renderer never rewrites the source text/font.
"""
from __future__ import annotations

import argparse
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from copy import deepcopy
import hashlib

import fitz
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('render_fixture', ROOT / 'tests/test_pdf_rendering.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
from backend.pdf import circle_for, extract, marked_document

OUT = ROOT / "test-results/pdf-rendering"


def embedded_fixture(**args):
    _, job = fixture_module.fixture(**args)
    document = fitz.open()
    page = document.new_page(width=260, height=180)
    if args.get('background'):
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    if args.get('image_background'):
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 260, 180), False)
        image.clear_with(255)
        page.insert_image(page.rect, pixmap=image)
    page.insert_text((60, 90), '장음 표시', fontname='fixture-cjk',
                     fontfile=str(OUT / 'fixture-font.otf'),
                     fontsize=24, color=args.get('color', (0, 0, 0)))
    page.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(10, 10, 40, 25), 'uri': 'https://example.com/original'})
    page.set_rotation(args.get('rotation', 0))
    if args.get('crop'):
        page.set_cropbox(fitz.Rect(10, 10, 250, 170))
    source = document.tobytes(garbage=3, deflate=True)
    document.close()
    pages, occurrences = extract(source)
    first = occurrences[0]
    first.update({k: deepcopy(v) for k, v in job['occurrences'][0].items()
                  if k not in ('id', 'glyph_ids', 'text', 'lemma', 'context')})
    job.update(pages=pages, occurrences=occurrences, sha256=hashlib.sha256(source).hexdigest())
    job['marks'][0].update(glyph_id=first['glyph_ids'][0], occurrence_id=first['id'])
    return source, job


def generated_pdfs():
    unembedded, _ = fixture_module.fixture()
    (OUT / 'nonembedded-cjk-source.pdf').write_bytes(unembedded)
    variants = {
        'transparent': {},
        'opaque': {'background': True},
        'colored': {'background': True, 'color': (.75, .08, .12)},
        'image-crop-rotated': {'image_background': True, 'rotation': 90, 'crop': True},
    }
    for name, args in variants.items():
        source, job = embedded_fixture(**args)
        (OUT / f'{name}-source.pdf').write_bytes(source)
        with marked_document(source, job) as document:
            (OUT / f'{name}-new.pdf').write_bytes(document.tobytes())
        for pdf_name in (f'{name}-source', f'{name}-new'):
            with fitz.open(OUT / f'{pdf_name}.pdf') as document:
                document[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).save(OUT / f'{pdf_name}-mupdf.png')

    source, job = embedded_fixture()
    mark = job['marks'][0]
    with fitz.open(stream=source, filetype='pdf') as document:
        page = document[0]
        circle = circle_for(job['pages'][0]['glyphs'][0], mark, job['settings'], page)
        annotation = page.add_circle_annot(circle)
        annotation.set_colors(stroke=[], fill=(206 / 255, 228 / 255, 245 / 255))
        annotation.set_border(width=0)
        annotation.set_opacity(.6)
        annotation.set_blendmode('Multiply')
        annotation.update()
        (OUT / 'old-multiply.pdf').write_bytes(document.tobytes())
        annotation.set_blendmode('Normal')
        annotation.update()
        (OUT / 'old-multiply-ignored-normal.pdf').write_bytes(document.tobytes())
        document[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).save(OUT / 'old-multiply-ignored-normal-mupdf.png')


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--font-collection', type=Path, default=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'))
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--compare-only', action='store_true')
    args = parser.parse_args()
    OUT = args.output.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.compare_only:
        compare_pixels()
        return
    from fontTools.ttLib import TTCollection
    from fontTools.subset import Options, Subsetter
    collection = TTCollection(args.font_collection)
    font = next(font for font in collection.fonts if font['name'].getDebugName(1) == 'Noto Sans CJK KR')
    options = Options()
    # PDF font CID maps must retain the original CFF glyph IDs. Renumbering
    # produces fixtures that PDFium renders but MuPDF cannot paint correctly.
    options.retain_gids = True
    subsetter = Subsetter(options)
    subsetter.populate(text='장음 표시')
    subsetter.subset(font)
    font.save(OUT / 'fixture-font.otf')
    generated_pdfs()
    compare_mupdf_pixels()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(OUT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    metadata = {
        'mupdf': fitz.__doc__.strip(), 'screenshots': [],
        'platform_unrun': ['macOS Preview', 'Adobe Acrobat Reader'],
        'zoom_percent': 250,
        'viewer': 'Actual Chromium built-in PDF viewer / PDFium, not app PNG preview',
        'fixture_font': 'Installed Noto Sans CJK KR; subset before source PDF creation with original glyph IDs retained',
        'limitations': [
            'The old /Normal appearance simulates a viewer ignoring Multiply; Chromium itself honors Multiply.',
            'The separate nonembedded-cjk-source.pdf source is blank in this Chromium build; MuPDF supplies its built-in CJK font. It is excluded from sharpness counts.',
            'Pixel comparison checks solid original glyph interiors at this zoom; it does not claim pixel identity for antialias edges or every PDF engine.',
            'macOS Preview and Adobe Acrobat Reader were not run on this Linux environment.',
        ],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        metadata['chromium_version'] = browser.version
        context = browser.new_context(viewport={'width': 1400, 'height': 900}, device_scale_factor=1)
        for name in ['transparent-source', 'transparent-new', 'opaque-source', 'opaque-new', 'colored-source', 'colored-new', 'image-crop-rotated-source', 'image-crop-rotated-new', 'old-multiply', 'old-multiply-ignored-normal', 'nonembedded-cjk-source']:
            page = context.new_page()
            url = f'http://127.0.0.1:{server.server_port}/{name}.pdf#zoom=250'
            page.goto(url, wait_until='load')
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / f'{name}-chromium-pdf.png'))
            cdp = context.new_cdp_session(page)
            targets = cdp.send('Target.getTargets')['targetInfos']
            info = {
                'name': name,
                'url': page.url,
                'content_type': page.evaluate('document.contentType'),
                'body': page.locator('body').inner_html(),
                'frames': [frame.url for frame in page.frames],
                'targets': [{k: t.get(k) for k in ['type', 'url', 'title']} for t in targets],
                'screenshot': f'{name}-chromium-pdf.png',
            }
            metadata['screenshots'].append(info)
            page.close()
        browser.close()
    server.shutdown()
    (OUT / 'viewer-evidence.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    compare_pixels()
    print(json.dumps({'chromium_version': metadata['chromium_version'], 'count': len(metadata['screenshots']), 'output': str(OUT)}, indent=2))


def compare_pixels():
    from PIL import Image
    results = {}
    for name in ('transparent', 'opaque', 'colored', 'image-crop-rotated', 'old-multiply', 'old-multiply-ignored-normal'):
        original_name = 'transparent' if name.startswith('old-') else name
        result_name = name if name.startswith('old-') else name + '-new'
        before = Image.open(OUT / f'{original_name}-source-chromium-pdf.png').convert('RGB')
        after = Image.open(OUT / f'{result_name}-chromium-pdf.png').convert('RGB')
        eligible = changed = total_changed = 0
        # Common content-only area excludes viewer toolbar, sidebar, and title.
        for y in range(70, 850):
            for x in range(500, 1200):
                a, b = before.getpixel((x, y)), after.getpixel((x, y))
                total_changed += a != b
                solid = max(a) == 0 if name != 'colored' else a[0] > 150 and a[1] < 50 and a[2] < 60 and a[0] > a[1] * 4
                if solid:
                    eligible += 1
                    changed += a != b
        results[name] = {'original_solid_ink_pixels': eligible, 'changed_ink_pixels': changed, 'changed_content_pixels': total_changed}
        assert eligible > 100, (name, 'source must contain visible original text')
        if not name.startswith('old-'):
            assert changed == 0, (name, 'solid glyph interiors changed')
    (OUT / 'chromium-pixel-comparison.json').write_text(json.dumps(results, indent=2))


def compare_mupdf_pixels():
    from PIL import Image
    results = {}
    for name in ('transparent', 'opaque', 'colored', 'image-crop-rotated'):
        before = Image.open(OUT / f'{name}-source-mupdf.png').convert('RGB')
        after = Image.open(OUT / f'{name}-new-mupdf.png').convert('RGB')
        eligible = changed = 0
        for a, b in zip(before.getdata(), after.getdata()):
            solid = max(a) == 0 if name != 'colored' else a[0] > 150 and a[1] < 50 and a[2] < 60 and a[0] > a[1] * 4
            if solid:
                eligible += 1
                changed += a != b
        assert eligible > 100, (name, 'MuPDF must render original text before mask analysis')
        assert changed == 0, (name, 'solid glyph interiors changed')
        results[name] = {'original_solid_ink_pixels': eligible, 'changed_ink_pixels': changed}
    (OUT / 'mupdf-pixel-comparison.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
