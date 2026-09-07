import io
import re
from unittest.mock import MagicMock, Mock
import numpy as np
import pymupdf
import pytest
from pdfminer.layout import LTChar, LTLine, LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage as MinerPDFPage

from app.services.pdf2zh_engine import (
    PDFPageInterpreterEx,
    PDFConverterEx,
    TranslateConverter,
    Paragraph,
    OpType,
    patch_page,
)
from app.services.pdf2zh_engine.pdfinterp import safe_float
from app.services.pdf2zh_engine.adapter import BaseTranslator


class DummyTranslator(BaseTranslator):
    def __init__(self, mapping=None, lang_in="en", lang_out="vi"):
        super().__init__(name="dummy", lang_in=lang_in, lang_out=lang_out)
        self.mapping = mapping or {"Hello World": "Xin chào thế giới"}

    def do_translate(self, text: str) -> str:
        return self.mapping.get(text.strip(), f"Dịch: {text}")


class RecordingCoverTranslator(BaseTranslator):
    """Deterministic translator used by sparse cover-page regressions."""

    handles_retries = True

    def __init__(self):
        super().__init__(name="recording-cover", lang_in="en", lang_out="vi")
        self.calls = []

    def do_translate(self, text: str) -> str:
        self.calls.append(text.strip())
        if "Artificial Intelligence" in text:
            return "TIEU DE"
        if "Insights and Recommendations" in text:
            return "KHUYEN NGHI"
        return text


def make_sparse_cover_page():
    """Create cover content in an order that exposed the all-ones fallback mask."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 40), "HEADER", fontsize=9)
    page.insert_text((50, 180), "Artificial Intelligence", fontsize=40)
    page.insert_text((50, 225), "and Learning", fontsize=40)
    page.insert_text(
        (50, 290),
        "Insights and Recommendations",
        fontsize=20,
        fontname="tiit",
    )
    page.insert_text((50, 320), "May 2023", fontsize=10)
    return doc, page


def test_safe_float():
    assert safe_float("12.5") == 12.5
    assert safe_float(42) == 42.0
    assert safe_float("invalid") is None
    assert safe_float(None) is None


def test_converter_ex_basic():
    rsrcmgr = PDFResourceManager()
    converter = PDFConverterEx(rsrcmgr)

    mock_page = Mock()
    mock_page.pageno = 1
    mock_page.cropbox = (0, 0, 100, 200)
    mock_ctm = [1, 0, 0, 1, 0, 0]
    converter.begin_page(mock_page, mock_ctm)
    assert converter.cur_item is not None
    assert converter.cur_item.pageid == 1

    mock_matrix = (1, 2, 3, 4, 5, 6)
    mock_font = Mock()
    mock_font.to_unichr.return_value = "A"
    mock_font.char_width.return_value = 10
    mock_font.char_disp.return_value = (0, 0)
    graphic_state = Mock()
    converter.cur_item = Mock()

    result = converter.render_char(
        mock_matrix,
        mock_font,
        fontsize=12,
        scaling=1.0,
        rise=0,
        cid=65,
        ncs=None,
        graphicstate=graphic_state,
    )
    assert result == 120.0


def test_translate_converter_initialization():
    rsrcmgr = PDFResourceManager()
    dummy_translator = DummyTranslator()

    # Direct translator injection
    converter = TranslateConverter(
        rsrcmgr,
        translator=dummy_translator,
    )
    assert converter.translator is dummy_translator

    # Factory translator via service name
    converter_google = TranslateConverter(
        rsrcmgr,
        service="google",
        lang_in="en",
        lang_out="zh",
    )
    assert converter_google.translator is not None
    assert converter_google.translator.name == "google"
    assert converter_google.translator.lang_out == "zh-CN"

    # Unsupported service should raise ValueError
    with pytest.raises(ValueError, match="Unsupported translation service"):
        TranslateConverter(
            rsrcmgr,
            service="unsupported_service_xyz",
        )


def test_translate_converter_typesetting_and_formula_token():
    rsrcmgr = PDFResourceManager()
    translator = DummyTranslator(mapping={"Hello {v0} World": "Xin chào {v0} thế giới"})
    noto_font = pymupdf.Font("helv")
    layout = {1: np.ones((500, 500), dtype=int)}

    converter = TranslateConverter(
        rsrcmgr,
        translator=translator,
        layout=layout,
        noto_name="helv",
        noto=noto_font,
    )

    ltpage = LTPage(1, (0, 0, 500, 500))

    font_text = Mock()
    font_text.fontname = "Helvetica"
    font_text.char_width.return_value = 0.5

    font_math = Mock()
    font_math.fontname = "CMSY10"  # Math font
    font_math.char_width.return_value = 0.6

    graphic_state = Mock()

    # Add "Hello "
    for i, ch in enumerate("Hello "):
        ltpage.add(
            LTChar(
                (1, 0, 0, 1, 10 + i * 10, 100),
                font_text,
                12,
                1.0,
                0,
                ch,
                10,
                (0, 0),
                None,
                graphic_state,
            )
        )

    # Add math symbol "∑"
    math_char = LTChar(
        (1, 0, 0, 1, 70, 100),
        font_math,
        12,
        1.0,
        0,
        "∑",
        12,
        (0, 0),
        None,
        graphic_state,
    )
    math_char.cid = ord("∑")
    math_char.font = font_math
    ltpage.add(math_char)

    # Add a formula fraction line
    ltline = LTLine(1.0, (70, 95), (82, 95))
    ltpage.add(ltline)

    # Add " World"
    for i, ch in enumerate(" World"):
        ltpage.add(
            LTChar(
                (1, 0, 0, 1, 85 + i * 10, 100),
                font_text,
                12,
                1.0,
                0,
                ch,
                10,
                (0, 0),
                None,
                graphic_state,
            )
        )

    converter.fontmap = {"helv": font_text, "math": font_math}
    converter.fontid = {font_text: "helv", font_math: "math"}

    ops = converter.receive_layout(ltpage)
    assert isinstance(ops, str)
    assert ops.startswith("BT ")
    assert ops.endswith("ET ")
    assert "Tf" in ops
    assert "Tm" in ops
    assert "TJ" in ops


def test_pdf_interpreter_line_filtering():
    from pdfminer.utils import MATRIX_IDENTITY

    rsrcmgr = PDFResourceManager()
    device = Mock()
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    interpreter.init_state(MATRIX_IDENTITY)

    # Horizontal black line of length 2
    interpreter.curpath = [("m", 10, 20), ("l", 50, 20)]
    interpreter.graphicstate.scolor = 0  # Black
    res = interpreter.do_S()
    assert res == "n"
    assert device.paint_path.called

    # Diagonal line (should be filtered)
    device.paint_path.reset_mock()
    interpreter.curpath = [("m", 10, 20), ("l", 50, 40)]
    interpreter.graphicstate.scolor = 0
    res = interpreter.do_S()
    assert res is None
    assert not device.paint_path.called


def test_pdf_interpreter_and_patching_end_to_end():
    # 1. Create a 1-page PDF using PyMuPDF
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Hello World", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    # 2. Re-open and set up doc_zh
    doc_zh = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    p0 = doc_zh[0]
    page_xref = doc_zh.get_new_xref()
    doc_zh.update_object(page_xref, "<<>>")
    doc_zh.update_stream(page_xref, b"")
    p0.set_contents(page_xref)

    # 3. Setup pdfminer parser and interpreter
    parser = PDFParser(io.BytesIO(pdf_bytes))
    miner_doc = PDFDocument(parser)
    miner_pages = list(MinerPDFPage.create_pages(miner_doc))
    assert len(miner_pages) == 1
    miner_page = miner_pages[0]
    miner_page.pageno = 0
    miner_page.page_xref = page_xref

    # 4. Setup TranslateConverter with DummyTranslator
    rsrcmgr = PDFResourceManager()
    translator = DummyTranslator(mapping={"Hello World": "Xin chào thế giới"})
    noto_font = pymupdf.Font("helv")
    h, w = int(p0.rect.height), int(p0.rect.width)
    layout = {0: np.ones((h, w), dtype=int)}

    converter = TranslateConverter(
        rsrcmgr,
        translator=translator,
        layout=layout,
        noto_name="helv",
        noto=noto_font,
    )

    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, converter, obj_patch)
    interpreter.process_page(miner_page)

    assert page_xref in obj_patch
    patched_stream = obj_patch[page_xref]
    assert isinstance(patched_stream, str)
    assert "BT" in patched_stream
    assert "ET" in patched_stream
    assert "Tm" in patched_stream

    # Update stream in doc_zh and verify valid PDF write
    doc_zh.update_stream(page_xref, patched_stream.encode("latin1"))
    output_pdf = doc_zh.tobytes()
    assert len(output_pdf) > 0
    doc_zh.close()


def test_patch_page_with_model():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((50, 50), "Hello", fontsize=12)
    doc_bytes = doc.tobytes()
    doc.close()

    doc_test = pymupdf.open(stream=doc_bytes, filetype="pdf")
    p = doc_test[0]

    # Mock layout prediction model
    mock_model = Mock()
    mock_box = Mock()
    mock_box.cls = 0
    mock_box.xyxy = np.array([[10, 10, 100, 100]])
    mock_pred = Mock()
    mock_pred.boxes = [mock_box]
    mock_pred.names = {0: "text"}
    mock_model.predict.return_value = [mock_pred]

    rsrcmgr = PDFResourceManager()
    translator = DummyTranslator(mapping={"Hello": "Xin chào"})
    converter = TranslateConverter(
        rsrcmgr,
        translator=translator,
        noto_name="helv",
        noto=pymupdf.Font("helv"),
    )

    ops = patch_page(p, model=mock_model, converter=converter)
    assert isinstance(ops, str)
    assert "BT" in ops
    assert "ET" in ops
    assert mock_model.predict.called
    doc_test.close()


def test_patch_page_raw_miner_page_without_pageno():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((40, 40), "Raw Miner Test", fontsize=12)
    doc_bytes = doc.tobytes()
    doc.close()

    parser = PDFParser(io.BytesIO(doc_bytes))
    miner_doc = PDFDocument(parser)
    miner_page = next(MinerPDFPage.create_pages(miner_doc))

    # Verify that raw PDFPage does not have pageno or page_xref attributes
    assert not hasattr(miner_page, "pageno")
    assert not hasattr(miner_page, "page_xref")

    rsrcmgr = PDFResourceManager()
    translator = DummyTranslator(mapping={"Raw Miner Test": "Thử nghiệm Miner Thô"})
    converter = TranslateConverter(
        rsrcmgr,
        translator=translator,
        noto_name="helv",
        noto=pymupdf.Font("helv"),
    )

    # Calling patch_page on raw miner_page should succeed without AttributeError
    ops = patch_page(miner_page, converter=converter)
    assert isinstance(ops, str)
    assert "BT" in ops
    assert "ET" in ops


def test_patch_page_model_none_keeps_sparse_cover_title_vertical_position():
    doc, page = make_sparse_cover_page()
    try:
        translator = RecordingCoverTranslator()
        converter = TranslateConverter(
            PDFResourceManager(),
            translator=translator,
            noto_name="helv",
            noto=pymupdf.Font("helv"),
        )

        ops = patch_page(page, model=None, converter=converter)

        title_matrices = re.findall(
            r"/\S+\s+40\.000000 Tf 1 0 0 1 "
            r"([-\d.]+) ([-\d.]+) Tm",
            ops,
        )
        assert title_matrices
        title_baseline_y = float(title_matrices[0][1])
        original_title_baseline_y = page.rect.height - 180
        assert title_baseline_y == pytest.approx(original_title_baseline_y, abs=1.0)
        assert not any(
            "HEADER" in call and "Artificial Intelligence" in call
            for call in translator.calls
        )
    finally:
        doc.close()


def test_patch_page_model_none_translates_italic_cover_prose():
    doc, page = make_sparse_cover_page()
    try:
        translator = RecordingCoverTranslator()
        converter = TranslateConverter(
            PDFResourceManager(),
            translator=translator,
            noto_name="helv",
            noto=pymupdf.Font("helv"),
        )

        patch_page(page, model=None, converter=converter)

        assert "Insights and Recommendations" in translator.calls
    finally:
        doc.close()


def _add_text_run(ltpage, text, x, y, font, graphic_state):
    """Add a tightly spaced LTChar run with predictable PDFMiner geometry."""
    for index, char in enumerate(text):
        item = LTChar(
            (1, 0, 0, 1, x + index * 6, y),
            font,
            12,
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


def test_translated_paragraphs_preserve_rgb_and_gray_fill_colors():
    translator = DummyTranslator(mapping={"Orange": "Cam", "Gray": "Xam"})
    layout = np.ones((300, 300), dtype=int)
    layout[170:230, :] = 2
    layout[70:130, :] = 3
    converter = TranslateConverter(
        PDFResourceManager(),
        translator=translator,
        layout={0: layout},
        noto_name="helv",
        noto=pymupdf.Font("helv"),
    )

    source_font = Mock()
    source_font.fontname = "SourceSans"
    source_font.char_width.return_value = 0.5
    rgb_state = PDFGraphicState()
    rgb_state.ncolor = (0.906, 0.322, 0.0)
    gray_state = PDFGraphicState()
    gray_state.ncolor = 0.251

    ltpage = LTPage(0, (0, 0, 300, 300))
    _add_text_run(ltpage, "Orange", 20, 200, source_font, rgb_state)
    _add_text_run(ltpage, "Gray", 20, 100, source_font, gray_state)

    ops = converter.receive_layout(ltpage)

    assert re.search(
        r"0\.906(?:0*)?\s+0\.322(?:0*)?\s+0(?:\.0+)?\s+rg",
        ops,
    )
    assert re.search(r"0\.251(?:0*)?\s+g", ops)


def test_translated_vietnamese_uses_one_full_coverage_font():
    translator = DummyTranslator(mapping={"Source": "Trí tuệ"})
    converter = TranslateConverter(
        PDFResourceManager(),
        translator=translator,
        layout={0: np.full((300, 300), 2, dtype=int)},
        noto_name="noto",
        noto=Mock(),
    )
    converter.noto.has_glyph.side_effect = lambda codepoint: codepoint
    converter.noto.char_lengths.side_effect = (
        lambda text, size: [size * 0.5 for _ in text]
    )

    tiro_font = Mock()
    tiro_font.to_unichr.side_effect = chr
    tiro_font.char_width.return_value = 0.5
    converter.fontmap = {"tiro": tiro_font}

    source_font = Mock()
    source_font.fontname = "SourceSans"
    source_font.char_width.return_value = 0.5
    graphic_state = PDFGraphicState()
    graphic_state.ncolor = 0.0
    ltpage = LTPage(0, (0, 0, 300, 300))
    _add_text_run(ltpage, "Source", 20, 200, source_font, graphic_state)

    ops = converter.receive_layout(ltpage)

    assert "/noto " in ops
    assert "/tiro " not in ops


def test_pdfinterp_colorspace_and_scn_sc_operators():
    from pdfminer.pdfcolor import PREDEFINED_COLORSPACE
    from pdfminer.pdfdevice import PDFDevice
    rsrcmgr = PDFResourceManager()
    dev = PDFDevice(rsrcmgr)
    interp = PDFPageInterpreterEx(rsrcmgr, dev, {})
    interp.init_state((1, 0, 0, 1, 0, 0))

    # Test default colorspaces
    assert interp.scs is not None
    assert interp.ncs is not None

    # Test setting ncs and scs via property
    interp.graphicstate.ncs = PREDEFINED_COLORSPACE["DeviceRGB"]
    assert interp.ncs == PREDEFINED_COLORSPACE["DeviceRGB"]
    interp.push(0.1)
    interp.push(0.2)
    interp.push(0.3)
    res_scn = interp.do_scn()
    assert res_scn is not None

    interp.graphicstate.scs = PREDEFINED_COLORSPACE["DeviceRGB"]
    assert interp.scs == PREDEFINED_COLORSPACE["DeviceRGB"]
    interp.push(0.4)
    interp.push(0.5)
    interp.push(0.6)
    res_sc = interp.do_sc()
    assert res_sc is not None

    # Test dup preserves scs and ncs
    child = interp.dup()
    assert child.scs == interp.scs
    assert child.ncs == interp.ncs
