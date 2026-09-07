import base64
import io
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF
    import fitz

from app.api.routes import JOB_STORE, UPLOAD_STORE
from app.config import settings
from app.main import app
from app.services.pdf2zh_engine.adapter import BaseTranslator


client = TestClient(app)


class _DeterministicTranslator(BaseTranslator):
    def __init__(self) -> None:
        super().__init__(name="deterministic", lang_in="en", lang_out="vi")

    def do_translate(self, text: str) -> str:
        return text.replace("Page", "Trang").replace("Hello", "Xin chao")


def _three_page_pdf() -> bytes:
    doc = fitz.open()
    try:
        for page_number in range(1, 4):
            page = doc.new_page()
            page.insert_text(
                (72, 100),
                f"Hello from Page {page_number}",
                fontsize=16,
            )
        return doc.tobytes()
    finally:
        doc.close()


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    normalized = body.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        event_type = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if data_lines:
            events.append((event_type, json.loads("\n".join(data_lines))))
    return events


def _assert_openable_pdf(content: bytes, expected_pages: int) -> None:
    assert content.startswith(b"%PDF-")
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        assert len(doc) == expected_pages
        for page in doc:
            page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
    finally:
        doc.close()


def test_file_stream_all_pages_and_deliver_openable_mono_dual_pdfs_inline():
    """Exercise self-contained upload, SSE previews, and inline PDF delivery."""
    pdf_bytes = _three_page_pdf()
    upload = client.post(
        "/api/upload",
        files={"file": ("three-pages.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert upload.status_code == 200
    initial_file_id = upload.json()["file_id"]
    job_id = None
    try:
        with (
            patch(
                "app.api.routes.create_translator",
                return_value=_DeterministicTranslator(),
            ),
            patch(
                "app.services.pdf2zh_engine.pipeline.load_layout_model",
                return_value=None,
            ),
        ):
            response = client.post(
                "/api/translate/file-stream",
                data={
                    "request_json": json.dumps(
                        {
                            "file_id": initial_file_id,
                            "source_lang": "English",
                            "target_lang": "Vietnamese",
                            "page_range": "all",
                            "engine_mode": "pdf2zh_layout",
                            "ai_config": {"provider": "google"},
                        }
                    )
                },
                files={
                    "file": (
                        "three-pages.pdf",
                        io.BytesIO(pdf_bytes),
                        "application/pdf",
                    )
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert not (settings.upload_dir / f"{initial_file_id}.pdf").exists()
        assert initial_file_id not in UPLOAD_STORE
        events = _sse_events(response.text)

        start = next(data for event, data in events if event == "start")
        job_id = start["job_id"]
        assert start["pages"] == [1, 2, 3]

        page_events = [data for event, data in events if event == "page_completed"]
        assert [event["page_number"] for event in page_events] == [1, 2, 3]
        for event in page_events:
            for image_key in ("original_image", "translated_image"):
                image_url = event[image_key]
                assert image_url.startswith("data:image/png;base64,")
                image_bytes = base64.b64decode(image_url.partition(",")[2])
                assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")

        completed = [data for event, data in events if event == "completed"][-1]
        assert completed["job_id"] == job_id
        assert completed["mono_ready"] is True
        assert completed["dual_ready"] is True

        inline_exports: dict[str, bytes] = {}
        starts = {
            data["format"]: data
            for event, data in events
            if event == "export_start"
        }
        for fmt in ("mono_pdf", "dual_pdf"):
            chunks = sorted(
                (
                    data for event, data in events
                    if event == "export_chunk" and data["format"] == fmt
                ),
                key=lambda item: item["index"],
            )
            inline_exports[fmt] = b"".join(
                base64.b64decode(chunk["content"])
                for chunk in chunks
            )
            assert len(inline_exports[fmt]) == starts[fmt]["size"]

        _assert_openable_pdf(inline_exports["mono_pdf"], expected_pages=3)
        _assert_openable_pdf(inline_exports["dual_pdf"], expected_pages=6)

        mono = client.get(f"/api/download/{job_id}/mono_pdf")
        assert mono.status_code == 200
        assert mono.headers["content-type"] == "application/pdf"
        _assert_openable_pdf(mono.content, expected_pages=3)

        dual = client.get(f"/api/download/{job_id}/dual_pdf")
        assert dual.status_code == 200
        assert dual.headers["content-type"] == "application/pdf"
        _assert_openable_pdf(dual.content, expected_pages=6)
    finally:
        (settings.upload_dir / f"{initial_file_id}.pdf").unlink(missing_ok=True)
        UPLOAD_STORE.pop(initial_file_id, None)
        if job_id:
            for suffix in ("_mono.pdf", "_dual.pdf", ".docx", ".md"):
                (settings.export_dir / f"{job_id}{suffix}").unlink(missing_ok=True)
            JOB_STORE.pop(job_id, None)
