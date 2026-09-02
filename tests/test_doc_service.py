import pytest
from pathlib import Path
from docx import Document
from app.services.doc_service import generate_docx, generate_pdf, generate_markdown

@pytest.fixture
def sample_pages():
    return [
        {"page_number": 1, "original_text": "Hello World", "translated_text": "Xin chào Thế Giới"},
        {"page_number": 2, "original_text": "This is page 2", "translated_text": "Đây là trang 2 có dấu tiếng Việt"}
    ]

def test_generate_docx(tmp_path, sample_pages):
    out = tmp_path / "test.docx"
    generate_docx(sample_pages, out, "Test Doc")
    assert out.exists()
    doc = Document(str(out))
    full_text = "\n".join([p.text for p in doc.paragraphs])
    assert "Xin chào Thế Giới" in full_text
    assert "Đây là trang 2" in full_text

def test_generate_pdf(tmp_path, sample_pages):
    out = tmp_path / "test.pdf"
    generate_pdf(sample_pages, out, "Test Doc")
    assert out.exists()
    assert out.stat().st_size > 0

def test_generate_markdown(tmp_path, sample_pages):
    out = tmp_path / "test.md"
    generate_markdown(sample_pages, out, "Test Doc")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "# Test Doc" in content
    assert "Xin chào Thế Giới" in content
