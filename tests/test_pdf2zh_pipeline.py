from pathlib import Path
import pymupdf
import pytest

from app.services.pdf2zh_engine import (
    process_pdf2zh_stream,
    create_dual_pdf,
    render_pixmap_base64,
)
from app.services.pdf2zh_engine.adapter import BaseTranslator


class MockPipelineTranslator(BaseTranslator):
    def __init__(self, lang_in: str = "en", lang_out: str = "vi"):
        super().__init__(name="mock", lang_in=lang_in, lang_out=lang_out)
        self.call_count = 0

    def do_translate(self, text: str) -> str:
        self.call_count += 1
        return f"[Dịch: {text}]"


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a 2-page sample PDF containing text and math expressions."""
    pdf_path = tmp_path / "sample.pdf"
    doc = pymupdf.open()

    # Page 1: Heading, text, math formula
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Chapter 1: Introduction", fontsize=18)
    p1.insert_text((72, 140), "This is the first page of our document.", fontsize=12)
    p1.insert_text((72, 180), "Einstein discovered that E = mc^2 in 1905.", fontsize=12)

    # Page 2: Summary, formula
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 100), "Chapter 2: Mathematical Foundation", fontsize=18)
    p2.insert_text((72, 140), "The integral of f(x) over dx represents the area under the curve.", fontsize=12)
    p2.insert_text((72, 180), "Let x + y = 10 and 2x - y = 5.", fontsize=12)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_render_pixmap_base64(sample_pdf: Path):
    doc = pymupdf.open(str(sample_pdf))
    page = doc[0]
    b64_str = render_pixmap_base64(page, zoom=1.0)
    doc.close()

    assert isinstance(b64_str, str)
    assert b64_str.startswith("data:image/png;base64,")
    assert len(b64_str) > len("data:image/png;base64,")


def test_create_dual_pdf(tmp_path: Path, sample_pdf: Path):
    # Create a 2-page translated doc
    trans_pdf_path = tmp_path / "sample_trans.pdf"
    doc_trans = pymupdf.open()
    p1 = doc_trans.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Translated Chapter 1: Introduction", fontsize=18)
    p2 = doc_trans.new_page(width=595, height=842)
    p2.insert_text((72, 100), "Translated Chapter 2: Math", fontsize=18)
    doc_trans.save(str(trans_pdf_path))
    doc_trans.close()

    dual_out_path = tmp_path / "sample_dual.pdf"
    result_path = create_dual_pdf(sample_pdf, trans_pdf_path, dual_out_path)

    assert result_path.exists()
    assert result_path == dual_out_path

    doc_dual = pymupdf.open(str(dual_out_path))
    # 2 original + 2 translated = 4 interleaved pages
    assert len(doc_dual) == 4
    # Page 0 (Orig P1): Chapter 1
    assert "Chapter 1" in doc_dual[0].get_text()
    # Page 1 (Trans P1): Translated Chapter 1
    assert "Translated Chapter 1" in doc_dual[1].get_text()
    # Page 2 (Orig P2): Chapter 2
    assert "Chapter 2" in doc_dual[2].get_text()
    # Page 3 (Trans P2): Translated Chapter 2
    assert "Translated Chapter 2" in doc_dual[3].get_text()
    doc_dual.close()


def test_create_dual_pdf_page_subset(tmp_path: Path, sample_pdf: Path):
    # 1. Test case where translated PDF has full page count (trans_count != len(page_indices))
    trans_pdf_path = tmp_path / "sample_subset_trans.pdf"
    doc_trans = pymupdf.open()
    p1 = doc_trans.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Untranslated Page 1", fontsize=18)
    p2 = doc_trans.new_page(width=595, height=842)
    p2.insert_text((72, 100), "Translated Chapter 2: Math", fontsize=18)
    doc_trans.save(str(trans_pdf_path))
    doc_trans.close()

    dual_out_path = tmp_path / "sample_subset_dual.pdf"
    result_path = create_dual_pdf(sample_pdf, trans_pdf_path, dual_out_path, page_indices=[1])

    assert result_path.exists()
    doc_dual = pymupdf.open(str(dual_out_path))
    # Must have exactly 2 pages (1 orig, 1 trans), NOT all 4 pages
    assert len(doc_dual) == 2
    assert "Chapter 2" in doc_dual[0].get_text()
    assert "Translated Chapter 2" in doc_dual[1].get_text()
    doc_dual.close()

    # 2. Test case where translated PDF contains only the translated page subset (trans_count == len(page_indices))
    single_trans_path = tmp_path / "sample_single_subset_trans.pdf"
    doc_single = pymupdf.open()
    sp = doc_single.new_page(width=595, height=842)
    sp.insert_text((72, 100), "Single Translated Chapter 2", fontsize=18)
    doc_single.save(str(single_trans_path))
    doc_single.close()

    single_dual_out = tmp_path / "sample_single_subset_dual.pdf"
    create_dual_pdf(sample_pdf, single_trans_path, single_dual_out, page_indices=[1])

    doc_single_dual = pymupdf.open(str(single_dual_out))
    assert len(doc_single_dual) == 2
    assert "Chapter 2" in doc_single_dual[0].get_text()
    assert "Single Translated Chapter 2" in doc_single_dual[1].get_text()
    doc_single_dual.close()


@pytest.mark.asyncio
async def test_process_pdf2zh_stream_end_to_end(tmp_path: Path, sample_pdf: Path):
    mono_out = tmp_path / "mono.pdf"
    dual_out = tmp_path / "dual.pdf"
    translator = MockPipelineTranslator(lang_in="en", lang_out="vi")

    events = []
    async for event in process_pdf2zh_stream(
        file_path=sample_pdf,
        page_indices=[0, 1],
        target_lang="vi",
        translator=translator,
        mono_out_path=mono_out,
        dual_out_path=dual_out,
        thread=2,
    ):
        events.append(event)

    # 1. Check yielded events
    event_types = [e["event"] for e in events]
    assert event_types.count("page_progress") == 2
    assert event_types.count("page_completed") == 2
    assert event_types[-1] == "completed"

    # Check page_progress events
    progress_events = [e for e in events if e["event"] == "page_progress"]
    assert progress_events[0]["page_number"] == 1
    assert progress_events[1]["page_number"] == 2

    # Check page_completed events
    completed_page_events = [e for e in events if e["event"] == "page_completed"]
    for idx, cp in enumerate(completed_page_events):
        assert cp["page_number"] == idx + 1
        assert cp["original_image"].startswith("data:image/png;base64,")
        assert cp["translated_image"].startswith("data:image/png;base64,")
        assert "translated_text" in cp

    # Check final completed event
    final_event = events[-1]
    assert final_event["event"] == "completed"
    assert final_event["mono_pdf"] == str(mono_out)
    assert final_event["dual_pdf"] == str(dual_out)
    assert final_event["total_pages"] == 2

    # 2. Check mono_out_path exists and has 2 pages
    assert mono_out.exists()
    doc_mono = pymupdf.open(str(mono_out))
    assert len(doc_mono) == 2
    doc_mono.close()

    # 3. Check dual_out_path exists and has 4 pages (interleaved)
    assert dual_out.exists()
    doc_dual = pymupdf.open(str(dual_out))
    assert len(doc_dual) == 4
    doc_dual.close()


@pytest.mark.asyncio
async def test_process_pdf2zh_stream_cancellation(tmp_path: Path, sample_pdf: Path):
    import asyncio
    mono_out = tmp_path / "mono_cancel.pdf"
    dual_out = tmp_path / "dual_cancel.pdf"
    translator = MockPipelineTranslator(lang_in="en", lang_out="vi")
    cancel_evt = asyncio.Event()
    cancel_evt.set()

    with pytest.raises(asyncio.CancelledError):
        async for _ in process_pdf2zh_stream(
            file_path=sample_pdf,
            page_indices=[0, 1],
            target_lang="vi",
            translator=translator,
            mono_out_path=mono_out,
            dual_out_path=dual_out,
            cancellation_event=cancel_evt,
        ):
            pass


@pytest.mark.asyncio
async def test_process_pdf2zh_stream_single_page(tmp_path: Path, sample_pdf: Path):
    mono_out = tmp_path / "mono_single.pdf"
    dual_out = tmp_path / "dual_single.pdf"
    translator = MockPipelineTranslator(lang_in="en", lang_out="vi")

    events = []
    async for event in process_pdf2zh_stream(
        file_path=sample_pdf,
        page_indices=[1],
        target_lang="vi",
        translator=translator,
        mono_out_path=mono_out,
        dual_out_path=dual_out,
    ):
        events.append(event)

    progress_events = [e for e in events if e["event"] == "page_progress"]
    assert len(progress_events) == 1
    assert progress_events[0]["page_number"] == 2
    assert progress_events[0]["total_pages"] == 1

    completed_events = [e for e in events if e["event"] == "page_completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["page_number"] == 2

    final_event = events[-1]
    assert final_event["event"] == "completed"
    assert final_event["total_pages"] == 1

    assert mono_out.exists()
    doc_mono = pymupdf.open(str(mono_out))
    assert len(doc_mono) == 1
    mono_text = doc_mono[0].get_text()
    assert "Chapter 2" in mono_text
    assert "Chapter 1" not in mono_text
    doc_mono.close()

    assert dual_out.exists()
    doc_dual = pymupdf.open(str(dual_out))
    assert len(doc_dual) == 2
    assert "Chapter 2" in doc_dual[0].get_text()
    assert "Chapter 1" not in doc_dual[0].get_text()
    assert "Chapter 2" in doc_dual[1].get_text()
    doc_dual.close()


@pytest.mark.asyncio
async def test_process_pdf2zh_stream_model_none_fallback(tmp_path: Path, sample_pdf: Path, monkeypatch):
    monkeypatch.setattr("app.services.pdf2zh_engine.pipeline.load_layout_model", lambda: None)
    mono_out = tmp_path / "mono_nomodel.pdf"
    dual_out = tmp_path / "dual_nomodel.pdf"
    translator = MockPipelineTranslator(lang_in="en", lang_out="vi")

    events = []
    async for event in process_pdf2zh_stream(
        file_path=sample_pdf,
        page_indices=[0],
        target_lang="vi",
        translator=translator,
        mono_out_path=mono_out,
        dual_out_path=dual_out,
    ):
        events.append(event)

    final_event = events[-1]
    assert final_event["event"] == "completed"
    assert mono_out.exists()

