import fitz
import pytest
from pathlib import Path
from app.services.pdf_service import parse_page_ranges, get_pdf_metadata, extract_page_content

@pytest.fixture
def sample_pdf(tmp_path) -> Path:
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    # Page 1
    page1 = doc.new_page()
    page1.insert_text((50, 72), "Hello World Page 1", fontsize=12)
    # Page 2
    page2 = doc.new_page()
    page2.insert_text((50, 72), "Second Page Content", fontsize=12)
    # Page 3
    page3 = doc.new_page()
    page3.insert_text((50, 72), "Third Page Text", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path

def test_parse_page_ranges():
    assert parse_page_ranges("", 5) == [0, 1, 2, 3, 4]
    assert parse_page_ranges("all", 5) == [0, 1, 2, 3, 4]
    assert parse_page_ranges("1-3", 5) == [0, 1, 2]
    assert parse_page_ranges("1, 3, 5", 5) == [0, 2, 4]
    assert parse_page_ranges("2-4, 5", 5) == [1, 2, 3, 4]

def test_get_pdf_metadata(sample_pdf):
    meta = get_pdf_metadata(sample_pdf)
    assert meta["total_pages"] == 3
    assert "file_size" in meta

def test_extract_page_content(sample_pdf):
    content = extract_page_content(sample_pdf, 0)
    assert "Hello World Page 1" in content["text"]
    assert content["page_number"] == 1
    assert content["image_base64"].startswith("data:image/png;base64,")
