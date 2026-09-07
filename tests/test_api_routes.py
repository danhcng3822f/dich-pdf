import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.config import settings
from app.main import app
from app.api.routes import JOB_STORE, UPLOAD_STORE

client = TestClient(app)


def _create_sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 72), "Test File", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _write_valid_pdf(path: Path, text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


def test_runtime_config_reports_upload_limit():
    response = client.get("/api/runtime-config")
    assert response.status_code == 200
    assert response.json()["max_upload_size_mb"] == settings.max_upload_size_mb


def test_upload_pdf():
    pdf_bytes = _create_sample_pdf_bytes()
    response = client.post(
        "/api/upload",
        files={"file": ("test.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    )
    assert response.status_code == 200
    data = response.json()
    assert "file_id" in data
    assert data["total_pages"] == 1
    assert data["filename"] == "test.pdf"

    file_id = data["file_id"]
    assert (settings.upload_dir / f"{file_id}.pdf").exists()
    delete_response = client.delete(f"/api/upload/{file_id}")
    assert delete_response.status_code == 204
    assert not (settings.upload_dir / f"{file_id}.pdf").exists()
    assert file_id not in UPLOAD_STORE


def test_download_not_found():
    response = client.get("/api/download/nonexistent_id/docx")
    assert response.status_code == 404


def test_test_connection_google_free():
    with patch("app.api.routes.create_translator") as mock_create:
        mock_translator = MagicMock()
        mock_translator.translate.return_value = "Xin chào"
        mock_create.return_value = mock_translator

        response = client.post(
            "/api/test-connection",
            json={"provider": "google_free"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["sample_translation"] == "Xin chào"
        mock_create.assert_called_once_with("google_free", target_lang="vi")


def test_test_connection_google_free_error():
    with patch("app.api.routes.create_translator") as mock_create:
        mock_translator = MagicMock()
        mock_translator.translate.side_effect = RuntimeError("Connection timeout")
        mock_create.return_value = mock_translator

        response = client.post(
            "/api/test-connection",
            json={"provider": "google_free"}
        )
        assert response.status_code == 400
        assert "Connection timeout" in response.json()["detail"]


def test_test_connection_paid_provider():
    # Missing API key should return 400
    response = client.post(
        "/api/test-connection",
        json={"provider": "openai", "api_key": ""}
    )
    assert response.status_code == 400
    assert "API key là bắt buộc" in response.json()["detail"]

    # Valid API key calls test_api_connection
    with patch("app.api.routes.test_api_connection", new_callable=AsyncMock) as mock_test:
        mock_test.return_value = {"status": "success", "sample_translation": "Xin chào"}
        response = client.post(
            "/api/test-connection",
            json={"provider": "openai", "api_key": "sk-mock-key"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"


def test_translate_stream_pdf2zh_layout():
    pdf_bytes = _create_sample_pdf_bytes()
    upload_res = client.post(
        "/api/upload",
        files={"file": ("sample_paper.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    )
    assert upload_res.status_code == 200
    file_id = upload_res.json()["file_id"]

    async def fake_pdf2zh_stream(file_path, page_indices, target_lang, translator, mono_out_path, dual_out_path, **kwargs):
        _write_valid_pdf(Path(mono_out_path), "Mono translated page")
        _write_valid_pdf(Path(dual_out_path), "Dual translated page")
        for idx, pno in enumerate(page_indices):
            yield {
                "event": "page_progress",
                "current_index": idx + 1,
                "total_pages": len(page_indices),
                "page_number": pno + 1,
            }
            yield {
                "event": "page_completed",
                "page_number": pno + 1,
                "current_index": idx + 1,
                "total_pages": len(page_indices),
                "original_image": "data:image/png;base64,orig",
                "translated_image": "data:image/png;base64,trans",
                "translated_text": "Sample translated page content.",
            }
        yield {
            "event": "completed",
            "mono_pdf": str(mono_out_path),
            "dual_pdf": str(dual_out_path),
            "total_pages": len(page_indices),
        }

    with patch("app.api.routes.process_pdf2zh_stream", side_effect=fake_pdf2zh_stream), \
         patch("app.api.routes.create_translator") as mock_create_translator:
        payload = {
            "file_id": file_id,
            "source_lang": "Japanese",
            "target_lang": "Vietnamese",
            "page_range": "all",
            "engine_mode": "pdf2zh_layout",
            "ai_config": {
                "provider": "google_free"
            }
        }
        response = client.post("/api/translate/stream", json=payload)
        assert response.status_code == 200
        body = response.text
        assert "event: start" in body
        assert "pdf2zh_layout" in body
        assert "event: page_progress" in body
        assert "event: page_completed" in body
        assert "event: export_start" in body
        assert "event: export_chunk" in body
        assert "event: export_ready" in body
        assert "event: completed" in body
        assert '"mono_ready": true' in body.lower()
        assert '"dual_ready": true' in body.lower()
        assert mock_create_translator.call_args.kwargs["source_lang"] == "Japanese"


def test_translate_stream_file_not_found():
    payload = {
        "file_id": "nonexistent-file-id",
        "target_lang": "Vietnamese",
        "engine_mode": "pdf2zh_layout",
        "ai_config": {
            "provider": "google_free"
        }
    }
    response = client.post("/api/translate/stream", json=payload)
    assert response.status_code == 404


def test_translate_stream_beamer_mode():
    pdf_bytes = _create_sample_pdf_bytes()
    upload_res = client.post(
        "/api/upload",
        files={"file": ("slides.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    )
    assert upload_res.status_code == 200
    file_id = upload_res.json()["file_id"]

    with patch("app.api.routes.translate_text", new_callable=AsyncMock) as mock_trans:
        mock_trans.return_value = "\\begin{frame}{Slide 1}\\end{frame}"
        payload = {
            "file_id": file_id,
            "target_lang": "Vietnamese",
            "style": "LaTeX_Beamer",
            "page_range": "1",
            "engine_mode": "beamer_slide",
            "ai_config": {
                "provider": "openai",
                "api_key": "sk-dummy"
            }
        }
        response = client.post("/api/translate/stream", json=payload)
        assert response.status_code == 200
        body = response.text
        assert "event: start" in body
        assert "event: page_completed" in body
        assert "event: completed" in body
        assert mock_trans.await_args.kwargs["source_lang"] == "auto"


def test_download_mono_and_dual_pdf():
    job_id = "test-job-download-pdf2zh"
    JOB_STORE[job_id] = {
        "filename": "academic_paper.pdf",
        "pages": [],
        "engine_mode": "pdf2zh_layout",
    }

    settings.export_dir.mkdir(parents=True, exist_ok=True)
    mono_path = settings.export_dir / f"{job_id}_mono.pdf"
    dual_path = settings.export_dir / f"{job_id}_dual.pdf"
    docx_path = settings.export_dir / f"{job_id}.docx"

    mono_bytes = _write_valid_pdf(mono_path, "MONO DATA")
    dual_bytes = _write_valid_pdf(dual_path, "DUAL DATA")
    docx_path.write_bytes(b"DOCX DATA")

    try:
        # Test mono_pdf download
        res_mono = client.get(f"/api/download/{job_id}/mono_pdf")
        assert res_mono.status_code == 200
        assert res_mono.content == mono_bytes
        assert res_mono.headers["content-type"] == "application/pdf"
        assert 'academic_paper_translated.pdf' in res_mono.headers.get("content-disposition", "")
        assert res_mono.headers["cache-control"] == "private, no-store"

        # Test mono alias download
        res_mono_alias = client.get(f"/api/download/{job_id}/mono")
        assert res_mono_alias.status_code == 200
        assert res_mono_alias.content == mono_bytes

        # Test dual_pdf download
        res_dual = client.get(f"/api/download/{job_id}/dual_pdf")
        assert res_dual.status_code == 200
        assert res_dual.content == dual_bytes
        assert res_dual.headers["content-type"] == "application/pdf"
        assert 'academic_paper_bilingual.pdf' in res_dual.headers.get("content-disposition", "")

        # Test dual alias download
        res_dual_alias = client.get(f"/api/download/{job_id}/dual")
        assert res_dual_alias.status_code == 200
        assert res_dual_alias.content == dual_bytes

        # Test standard pdf download when mono exists
        res_pdf = client.get(f"/api/download/{job_id}/pdf")
        assert res_pdf.status_code == 200
        assert res_pdf.content == mono_bytes
        assert 'academic_paper_translated.pdf' in res_pdf.headers.get("content-disposition", "")

        # Test docx download
        res_docx = client.get(f"/api/download/{job_id}/docx")
        assert res_docx.status_code == 200
        assert res_docx.content == b"DOCX DATA"

        # Test unsupported format returns 400
        res_bad = client.get(f"/api/download/{job_id}/unknown_format")
        assert res_bad.status_code == 400

    finally:
        mono_path.unlink(missing_ok=True)
        dual_path.unlink(missing_ok=True)
        docx_path.unlink(missing_ok=True)
        JOB_STORE.pop(job_id, None)
