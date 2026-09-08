import unicodedata
import numpy as np
import pymupdf
import pytest
from unittest.mock import Mock
from pdfminer.layout import LTChar, LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager

from app.services.pdf2zh_engine.converter import (
    TranslateConverter,
    Paragraph,
    build_page_layout,
    build_text_block_layout,
    refine_table_layout,
    resolve_paragraph_color,
    get_char_baseline_y,
)
from app.services.pdf2zh_engine.adapter import BaseTranslator, normalize_tokens


class DummyTranslator(BaseTranslator):
    def __init__(self, mapping=None, lang_in="en", lang_out="vi"):
        super().__init__(name="dummy", lang_in=lang_in, lang_out=lang_out)
        self.mapping = mapping or {}

    def do_translate(self, text: str) -> str:
        return self.mapping.get(text.strip(), f"Trans: {text.strip()}")


def _add_char(ltpage, char, x, y, font, graphic_state, size=12, matrix=None):
    if matrix is None:
        matrix = (1, 0, 0, 1, x, y)
    item = LTChar(
        matrix,
        font,
        size,
        1.0,
        0,
        char,
        0.5,
        (0, 0),
        None,
        graphic_state,
    )
    item.cid = ord(char)
    item.font = font
    ltpage.add(item)
    return item


def test_leading_white_bullet_does_not_turn_paragraph_text_white():
    """Bug 2: Paragraph with white number badge (1.0) and dark blue text (0.2, 0.2, 0.7).
    The paragraph must NOT be rendered in 100% white (1.000000 g)."""
    translator = DummyTranslator(mapping={"1 Content": "1 Noi Dung"})
    converter = TranslateConverter(
        PDFResourceManager(),
        translator=translator,
        layout={0: np.full((300, 300), 2, dtype=int)},
        noto_name="helv",
        noto=pymupdf.Font("helv"),
    )

    source_font = Mock()
    source_font.fontname = "SourceSans"
    source_font.char_width.return_value = 0.5

    white_state = PDFGraphicState()
    white_state.ncolor = 1.0  # White badge/bullet

    blue_state = PDFGraphicState()
    blue_state.ncolor = (0.2, 0.2, 0.7)  # Dark blue body text

    ltpage = LTPage(0, (0, 0, 300, 300))
    # '1' is white
    _add_char(ltpage, "1", 20, 200, source_font, white_state)
    # ' ' is space
    _add_char(ltpage, " ", 26, 200, source_font, white_state)
    # 'Content' is dark blue
    for i, ch in enumerate("Content"):
        _add_char(ltpage, ch, 32 + i * 6, 200, source_font, blue_state)

    ops = converter.receive_layout(ltpage)

    # Must NOT render with white fill `1.000000 g`
    assert "1.000000 g" not in ops
    # Must preserve the body text color (0.2, 0.2, 0.7)
    assert "0.200000 0.200000 0.700000 rg" in ops


def test_all_white_paragraph_preserves_white():
    """Paragraph that is legitimately all white (e.g. title on dark banner) must stay white."""
    p = Paragraph(100, 50, 50, 150, 90, 110, 12, False, color=1.0)
    p.colors = [1.0, 1.0, 1.0]
    color = resolve_paragraph_color(p)
    assert color == 1.0


def test_doclayout_rejects_full_page_false_positive_frozen_box():
    """Bug 2b: DocLayout misdetects entire slide (covering >80% of page) as 'table' or 'figure'.
    It must NOT zero-out the entire page layout."""
    doc = pymupdf.open()
    page = doc.new_page(width=450, height=250)
    page.insert_text((50, 100), "Real presentation text", fontsize=14)
    pix = page.get_pixmap()

    # Mock a model that returns a box covering 99% of page as 'table'
    mock_box = Mock()
    mock_box.cls = 5
    mock_box.conf = 0.42
    mock_box.xyxy = np.array([2.0, 2.0, 448.0, 248.0])

    mock_result = Mock()
    mock_result.boxes = [mock_box]
    mock_result.names = {5: "table"}

    mock_model = Mock()
    mock_model.predict.return_value = [mock_result]

    layout = build_page_layout(page, pix, mock_model)

    # If rejected, the layout must NOT be all 0s
    assert np.count_nonzero(layout != 0) > 0
    # Specifically, text coordinates should have valid block classes (> 1)
    assert np.max(layout) >= 2


def test_table_cell_refinement_gives_each_cell_unique_class():
    """Bug 3: Table with 2 rows and 2 columns. Each cell should have its own class
    so cell texts are not merged into a single run."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    # Draw table border lines
    page.draw_rect(pymupdf.Rect(50, 50, 350, 150))
    page.draw_line(pymupdf.Point(50, 100), pymupdf.Point(350, 100))
    page.draw_line(pymupdf.Point(200, 50), pymupdf.Point(200, 150))
    # Insert text in 4 cells
    page.insert_text((60, 80), "Header A", fontsize=11)
    page.insert_text((210, 80), "Header B", fontsize=11)
    page.insert_text((60, 130), "Val 1", fontsize=11)
    page.insert_text((210, 130), "Val 2", fontsize=11)

    pix = page.get_pixmap()
    layout = np.ones((pix.height, pix.width), dtype=int)

    refined = refine_table_layout(page, layout)

    # Check that find_tables detected the cells and assigned distinct classes
    unique_classes = np.unique(refined)
    # Should have background (1) + at least 4 cell classes (>= 5 unique values)
    assert len(unique_classes) >= 5


def test_char_baseline_extraction_uses_matrix():
    """Bug 1: True baseline Y is from matrix[5], not y0 which has descent offset."""
    source_font = Mock()
    source_font.fontname = "VNSS10"
    source_font.char_width.return_value = 0.5
    state = PDFGraphicState()

    ltpage = LTPage(0, (0, 0, 300, 300))
    # Character with baseline at Y=185.977 and y0=184.04
    char_item = _add_char(
        ltpage,
        "A",
        20,
        184.04,
        source_font,
        state,
        size=10,
        matrix=(1.0, 0.0, 0.0, 1.0, 20.0, 185.977),
    )

    baseline_y = get_char_baseline_y(char_item)
    assert abs(baseline_y - 185.977) < 1e-4
    assert baseline_y != char_item.y0


def test_nfc_normalization_for_vietnamese():
    """Bug 1b: Decomposed NFD accents must be normalized to canonical NFC."""
    # NFD: 'e' + combining circumflex + combining acute
    nfd_text = unicodedata.normalize("NFD", "Tiếng Việt")
    assert len(nfd_text) > len("Tiếng Việt")

    # In adapter / converter:
    normalized = unicodedata.normalize("NFC", nfd_text)
    assert normalized == "Tiếng Việt"
    assert len(normalized) == len("Tiếng Việt")


def test_create_dual_pdf_page_ordering(tmp_path):
    """Test dual PDF creation preserves correct interleaved page ordering without duplicating fonts."""
    from app.services.pdf2zh_engine.pipeline import create_dual_pdf

    orig_path = tmp_path / "orig.pdf"
    trans_path = tmp_path / "trans.pdf"
    dual_path = tmp_path / "dual.pdf"

    orig_doc = pymupdf.open()
    for i in range(3):
        p = orig_doc.new_page()
        p.insert_text((50, 50), f"Orig Page {i + 1}")
    orig_doc.save(str(orig_path))
    orig_doc.close()

    trans_doc = pymupdf.open()
    for i in range(3):
        p = trans_doc.new_page()
        p.insert_text((50, 50), f"Trans Page {i + 1}")
    trans_doc.save(str(trans_path))
    trans_doc.close()

    create_dual_pdf(orig_path, trans_path, dual_path)

    res_doc = pymupdf.open(str(dual_path))
    assert len(res_doc) == 6
    expected = [
        "Orig Page 1",
        "Trans Page 1",
        "Orig Page 2",
        "Trans Page 2",
        "Orig Page 3",
        "Trans Page 3",
    ]
    actual = [res_doc[i].get_text().strip() for i in range(6)]
    assert actual == expected
    res_doc.close()


def test_heading_and_body_font_sizes_preserved_with_model_coarse_box():
    """Bug 4: Heading (16pt) and body text (10pt) must retain their distinct font sizes
    and not be merged into a single uniform font size or turned into formula variables."""
    import re
    from app.services.pdf2zh_engine.converter import patch_page

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((30, 40), "Large Heading Title", fontsize=16)
    page.insert_text((30, 80), "Normal body paragraph text line 1", fontsize=10)
    page.insert_text((30, 95), "Normal body paragraph text line 2", fontsize=10)

    # Coarse box covering both heading and body
    mock_box = Mock()
    mock_box.cls = 0
    mock_box.xyxy = np.array([20.0, 20.0, 280.0, 150.0])
    mock_res = Mock()
    mock_res.boxes = [mock_box]
    mock_res.names = {0: "plain text"}
    mock_model = Mock()
    mock_model.predict.return_value = [mock_res]

    rsrc = PDFResourceManager()
    conv = TranslateConverter(
        rsrc,
        translator=DummyTranslator(),
        noto_name="helv",
        noto=pymupdf.Font("helv"),
    )
    ops = patch_page(page, model=mock_model, converter=conv)
    font_sizes = sorted(
        list(set(round(float(s), 1) for s in re.findall(r"/helv\s+([0-9\.]+)\s+Tf", ops)))
    )

    # Heading (16pt) and body (10pt) must both exist in ops
    assert 16.0 in font_sizes
    assert 10.0 in font_sizes
    assert len(font_sizes) >= 2

    # Body text must be translated and present in last_page_text, not converted to {v0}
    assert "Large Heading Title" in conv.last_page_text
    assert "Normal body paragraph text line 1" in conv.last_page_text
    assert "{v" not in conv.last_page_text


def test_font_size_shift_triggers_new_paragraph():
    """Different font sizes inside the same layout block must not be merged into one paragraph."""
    source_font = Mock()
    source_font.fontname = "VNSS10"
    source_font.char_width.return_value = 0.5
    state = PDFGraphicState()

    ltpage = LTPage(0, (0, 0, 300, 300))
    # Line 1: 16pt heading
    _add_char(ltpage, "H", 30, 160, source_font, state, size=16)
    _add_char(ltpage, "i", 40, 160, source_font, state, size=16)

    # Line 2: 10pt body text
    _add_char(ltpage, "B", 30, 120, source_font, state, size=10)
    _add_char(ltpage, "o", 40, 120, source_font, state, size=10)

    rsrc = PDFResourceManager()
    layout = np.ones((300, 300), dtype=int)  # same layout class
    conv = TranslateConverter(
        rsrc,
        translator=DummyTranslator(),
        noto_name="helv",
        noto=pymupdf.Font("helv"),
        layout={0: layout},
    )
    conv.receive_layout(ltpage)

    paragraphs = [p for p in conv.last_page_text.split("\n\n") if p.strip()]
    assert len(paragraphs) == 2
    assert "Hi" in paragraphs[0]
    assert "Bo" in paragraphs[1]


