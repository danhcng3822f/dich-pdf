import pytest
from fastapi.testclient import TestClient
import io
import pymupdf as fitz
from app.main import app

client = TestClient(app)

def test_upload_pdf():
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 72), "Test File", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    
    response = client.post(
        "/api/upload",
        files={"file": ("test.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    )
    assert response.status_code == 200
    data = response.json()
    assert "file_id" in data
    assert data["total_pages"] == 1
    assert data["filename"] == "test.pdf"

def test_download_not_found():
    response = client.get("/api/download/nonexistent_id/docx")
    assert response.status_code == 404
