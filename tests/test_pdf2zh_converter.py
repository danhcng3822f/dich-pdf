import io
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
