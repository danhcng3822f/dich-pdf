import base64
import json
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, expect

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.main import app
from app.api.routes import JOB_STORE
from app.config import settings

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((SERVER_HOST, port)) == 0


@pytest.fixture(scope="session", autouse=True)
def server():
    """Spawns uvicorn running app.main:app in a background daemon thread and waits until responsive."""
    if not is_port_in_use(SERVER_PORT):
        config = uvicorn.Config(app, host=SERVER_HOST, port=SERVER_PORT, log_level="error")
        uv_server = uvicorn.Server(config=config)
        thread = threading.Thread(target=uv_server.run, daemon=True)
        thread.start()

        start_time = time.time()
        timeout = 15.0
        connected = False
        while time.time() - start_time < timeout:
            try:
                r = httpx.get(f"{BASE_URL}/", timeout=1.0)
                if r.status_code == 200:
                    connected = True
                    break
            except Exception:
                time.sleep(0.1)

        if not connected:
            raise RuntimeError(f"Server at {BASE_URL} failed to start within {timeout}s")

        yield BASE_URL

        uv_server.should_exit = True
        thread.join(timeout=3.0)
    else:
        # Port already running, verify it responds
        r = httpx.get(f"{BASE_URL}/", timeout=2.0)
        yield BASE_URL


def test_homepage_render_and_engine_toggle(page: Page):
    """
    Test 1:
    - Navigates to http://127.0.0.1:8765/.
    - Asserts title contains 'AI PDF Translator'.
    - Asserts #engine-pdf2zh and #engine-beamer exist.
    - Clicks #engine-beamer, verifies active style changes and beamer description appears.
    - Clicks #engine-pdf2zh, verifies active style returns.
    """
    page.goto(f"{BASE_URL}/")

    # Asserts title contains "AI PDF Translator"
    expect(page).to_have_title(re.compile("AI PDF Translator"))

    # Asserts #engine-pdf2zh and #engine-beamer exist
    pdf2zh_btn = page.locator("#engine-pdf2zh")
    beamer_btn = page.locator("#engine-beamer")
    expect(pdf2zh_btn).to_be_visible()
    expect(beamer_btn).to_be_visible()

    # Clicks #engine-beamer, verifies active style changes and beamer description appears
    beamer_btn.click()
    expect(beamer_btn).to_have_class(re.compile(r"text-blue-600"))
    expect(page.locator("#engine-desc")).to_contain_text("LaTeX Beamer")

    # Clicks #engine-pdf2zh, verifies active style returns
    pdf2zh_btn.click()
    expect(pdf2zh_btn).to_have_class(re.compile(r"text-blue-600"))
    expect(beamer_btn).not_to_have_class(re.compile(r"text-blue-600"))
    expect(page.locator("#engine-desc")).to_contain_text("Giữ nguyên 100% định dạng")


def test_settings_modal_free_providers(page: Page):
    """
    Test 2:
    - Clicks #settings-btn.
    - Verifies modal #settings-modal is visible.
    - Selects provider google.
    - Verifies API key input is marked optional or has hint '(Không cần API Key)'.
    - Clicks #test-settings-btn, verifies connection test passes or shows success message.
    - Clicks #save-settings-btn, modal closes.
    """
    # UI behavior is deterministic; endpoint availability is covered by opt-in live tests.
    page.route(
        "**/api/test-connection",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"success","sample_translation":"Xin chào"}',
        ),
    )
    page.goto(f"{BASE_URL}/")

    # Clicks #settings-btn
    page.locator("#settings-btn").click()

    # Verifies modal #settings-modal is visible
    expect(page.locator("#settings-modal")).to_be_visible()

    # Selects provider google
    page.locator("#provider-select").select_option("google")

    # Verifies API key input is marked optional or has hint "(Không cần API Key)"
    placeholder = page.locator("#api-key-input").get_attribute("placeholder") or ""
    hint_text = page.locator("#api-key-hint").text_content() or ""
    badge_visible = page.locator("#api-key-free-badge").is_visible()
    assert (
        "Không cần API Key" in placeholder
        or "Không cần API Key" in hint_text
        or badge_visible
    ), "Expected API key input to be marked optional or show free badge/hint"

    # Clicks #test-settings-btn, verifies connection test passes or shows success message
    page.locator("#test-settings-btn").click()
    expect(page.locator("#test-status")).to_contain_text("Kết nối thành công", timeout=15000)

    # Clicks #save-settings-btn, modal closes
    page.locator("#save-settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_hidden()


def test_upload_and_translate_pdf2zh_e2e(page: Page, tmp_path: Path):
    """
    Test 3:
    - Creates a simple 1-page PDF using PyMuPDF.
    - Uses page.set_input_files("#file-input", str(test_pdf)).
    - Asserts #file-info-card is visible and shows test_sample.pdf.
    - Clicks #start-translate-btn.
    - Waits for progress section #progress-section to appear and #progress-percent to reach '100%'.
    - Waits for #pages-container to have at least one .bg-white.rounded-xl card.
    - Verifies that inside the page card, both the original PDF image and translated PDF image
      (img[src^="data:image/png;base64,"]) are rendered.
    - Verifies #download-mono-btn and #download-dual-btn are enabled (not disabled).
    """
    # Creates a simple 1-page PDF using PyMuPDF
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Hello world from AI PDF Translator test.")
    test_pdf = tmp_path / "test_sample.pdf"
    doc.save(str(test_pdf))
    doc.close()

    page.goto(f"{BASE_URL}/")

    # Uses page.set_input_files("#file-input", str(test_pdf))
    page.set_input_files("#file-input", str(test_pdf))

    # Asserts #file-info-card is visible and shows test_sample.pdf
    expect(page.locator("#file-info-card")).to_be_visible()
    expect(page.locator("#file-name")).to_contain_text("test_sample.pdf")

    # Clicks #start-translate-btn
    page.locator("#start-translate-btn").click()

    # Waits for progress section #progress-section to appear and #progress-percent to reach "100%"
    expect(page.locator("#progress-section")).to_be_visible()
    expect(page.locator("#progress-percent")).to_have_text("100%", timeout=60000)

    # Waits for #pages-container to have at least one .bg-white.rounded-xl card
    page_card = page.locator("#pages-container .bg-white.rounded-xl").first
    expect(page_card).to_be_visible(timeout=10000)

    # Verifies that both original and translated page previews are rendered.
    # The app converts inline data URLs to lighter browser Blob URLs.
    images = page_card.locator("img")
    expect(images).to_have_count(2)

    # Verifies #download-mono-btn and #download-dual-btn are enabled (not disabled)
    mono_btn = page.locator("#download-mono-btn, #download-pdf-btn")
    dual_btn = page.locator("#download-dual-btn")
    expect(mono_btn).to_be_enabled()
    expect(dual_btn).to_be_enabled()


def test_three_page_preview_accumulates_and_downloads_openable_pdfs(
    page: Page,
    tmp_path: Path,
):
    """Verify that one SSE response renders every page and both PDF buttons download valid files."""
    source = fitz.open()
    for page_number in range(1, 4):
        pdf_page = source.new_page()
        pdf_page.insert_text((50, 72), f"Original page {page_number}")
    source_path = tmp_path / "three-page-browser.pdf"
    source.save(str(source_path))
    source.close()

    job_id = "browser-three-page-delivery"
    mono_path = settings.export_dir / f"{job_id}_mono.pdf"
    dual_path = settings.export_dir / f"{job_id}_dual.pdf"
    settings.export_dir.mkdir(parents=True, exist_ok=True)

    mono_doc = fitz.open()
    for page_number in range(1, 4):
        pdf_page = mono_doc.new_page()
        pdf_page.insert_text((50, 72), f"Translated page {page_number}")
    mono_doc.save(str(mono_path))
    mono_doc.close()

    dual_doc = fitz.open()
    for page_number in range(1, 7):
        pdf_page = dual_doc.new_page()
        pdf_page.insert_text((50, 72), f"Bilingual page {page_number}")
    dual_doc.save(str(dual_path))
    dual_doc.close()

    JOB_STORE[job_id] = {
        "filename": source_path.name,
        "pages": [],
        "engine_mode": "pdf2zh_layout",
    }

    # Valid transparent 1x1 PNG; enough to exercise image-card accumulation.
    image_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
        "BAEAHnOcQAAAAABJRU5ErkJggg=="
    )

    sse_blocks = [
        (
            "start",
            {
                "job_id": job_id,
                "total_pages": 3,
                "pages": [1, 2, 3],
                "engine_mode": "pdf2zh_layout",
            },
        )
    ]
    for page_number in range(1, 4):
        sse_blocks.extend(
            [
                (
                    "page_progress",
                    {
                        "current_index": page_number,
                        "total_pages": 3,
                        "page_number": page_number,
                    },
                ),
                (
                    "page_completed",
                    {
                        "page_number": page_number,
                        "current_index": page_number,
                        "total_pages": 3,
                        "original_image": image_url,
                        "translated_image": image_url,
                        "translated_text": f"Trang da dich {page_number}",
                        "engine_mode": "pdf2zh_layout",
                    },
                ),
            ]
        )
    sse_blocks.append(
        (
            "export_start",
            {
                "job_id": job_id,
                "format": "mono_pdf",
                "filename": "three-page-browser_translated.pdf",
                "mime_type": "application/pdf",
                "size": mono_path.stat().st_size,
                "total_chunks": 1,
            },
        )
    )
    sse_blocks.append(
        (
            "export_chunk",
            {
                "job_id": job_id,
                "format": "mono_pdf",
                "index": 0,
                "content": base64.b64encode(mono_path.read_bytes()).decode("ascii"),
            },
        )
    )
    sse_blocks.append(
        (
            "export_ready",
            {
                "job_id": job_id,
                "format": "mono_pdf",
                "filename": "three-page-browser_translated.pdf",
                "mime_type": "application/pdf",
                "size": mono_path.stat().st_size,
                "total_chunks": 1,
            },
        )
    )
    sse_blocks.append(
        (
            "export_start",
            {
                "job_id": job_id,
                "format": "dual_pdf",
                "filename": "three-page-browser_bilingual.pdf",
                "mime_type": "application/pdf",
                "size": dual_path.stat().st_size,
                "total_chunks": 1,
            },
        )
    )
    sse_blocks.append(
        (
            "export_chunk",
            {
                "job_id": job_id,
                "format": "dual_pdf",
                "index": 0,
                "content": base64.b64encode(dual_path.read_bytes()).decode("ascii"),
            },
        )
    )
    sse_blocks.append(
        (
            "export_ready",
            {
                "job_id": job_id,
                "format": "dual_pdf",
                "filename": "three-page-browser_bilingual.pdf",
                "mime_type": "application/pdf",
                "size": dual_path.stat().st_size,
                "total_chunks": 1,
            },
        )
    )
    sse_blocks.append(
        (
            "completed",
            {
                "job_id": job_id,
                "mono_ready": True,
                "dual_ready": True,
                "engine_mode": "pdf2zh_layout",
            },
        )
    )
    sse_body = "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for event, data in sse_blocks
    )

    uploaded_file_id = None
    try:
        page.route(
            "**/api/translate/file-stream",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream; charset=utf-8",
                body=sse_body,
            ),
        )
        page.goto(f"{BASE_URL}/")

        with page.expect_response("**/api/upload") as upload_info:
            page.set_input_files("#file-input", str(source_path))
        uploaded_file_id = upload_info.value.json()["file_id"]

        page.locator("#start-translate-btn").click()
        expect(page.locator("#progress-percent")).to_have_text("100%")

        cards = page.locator("#pages-container > [id^='page-card-']")
        expect(cards).to_have_count(3)
        assert cards.evaluate_all("els => els.map(el => el.id)") == [
            "page-card-1",
            "page-card-2",
            "page-card-3",
        ]
        expect(cards.locator("img")).to_have_count(6)
        expect(page.locator("#job-history-select option")).to_have_count(1)

        # Prove that downloads come from the same SSE response, not from the
        # server's temporary filesystem.
        mono_path.unlink(missing_ok=True)
        dual_path.unlink(missing_ok=True)

        with page.expect_download() as mono_download_info:
            page.locator("#download-pdf-btn").click()
        mono_bytes = Path(mono_download_info.value.path()).read_bytes()
        assert mono_bytes.startswith(b"%PDF-")
        mono_download = fitz.open(stream=mono_bytes, filetype="pdf")
        assert len(mono_download) == 3
        mono_download.close()

        with page.expect_download() as dual_download_info:
            page.locator("#download-dual-btn").click()
        dual_bytes = Path(dual_download_info.value.path()).read_bytes()
        assert dual_bytes.startswith(b"%PDF-")
        dual_download = fitz.open(stream=dual_bytes, filetype="pdf")
        assert len(dual_download) == 6
        dual_download.close()

        second_job_id = "browser-second-history-result"
        second_sse = "".join(
            f"event: {event}\ndata: {json.dumps(data)}\n\n"
            for event, data in [
                (
                    "start",
                    {
                        "job_id": second_job_id,
                        "total_pages": 1,
                        "pages": [2],
                        "engine_mode": "beamer_slide",
                    },
                ),
                (
                    "page_completed",
                    {
                        "page_number": 2,
                        "image_base64": image_url,
                        "translated_text": "Trang da dich lan hai",
                        "engine_mode": "beamer_slide",
                    },
                ),
                (
                    "completed",
                    {
                        "job_id": second_job_id,
                        "pdf_ready": False,
                        "tex_ready": False,
                        "engine_mode": "beamer_slide",
                    },
                ),
            ]
        )
        page.unroute("**/api/translate/file-stream")
        page.route(
            "**/api/translate/file-stream",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream; charset=utf-8",
                body=second_sse,
            ),
        )
        page.locator("#page-range-input").fill("2")
        page.locator("#start-translate-btn").click()
        expect(page.locator("#progress-percent")).to_have_text("100%")
        expect(page.locator("#job-history-select option")).to_have_count(2)
        expect(page.locator("#pages-container > [id^='page-card-']")).to_have_count(1)
        expect(page.locator("#download-dual-btn")).to_be_hidden()
        expect(page.locator("#download-tex-btn")).to_be_visible()

        page.locator("#job-history-select").select_option(job_id)
        expect(page.locator("#pages-container > [id^='page-card-']")).to_have_count(3)
        expect(page.locator("#download-dual-btn")).to_be_visible()
        expect(page.locator("#download-tex-btn")).to_be_hidden()
    finally:
        if uploaded_file_id:
            (settings.upload_dir / f"{uploaded_file_id}.pdf").unlink(missing_ok=True)
        mono_path.unlink(missing_ok=True)
        dual_path.unlink(missing_ok=True)
        JOB_STORE.pop(job_id, None)


def test_truncated_translation_stream_is_reported_as_failure(page: Page, tmp_path: Path):
    source = fitz.open()
    source.new_page().insert_text((50, 72), "A stream that will be interrupted")
    source_path = tmp_path / "interrupted.pdf"
    source.save(str(source_path))
    source.close()

    image_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
        "BAEAHnOcQAAAAABJRU5ErkJggg=="
    )
    truncated_sse = "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for event, data in [
            (
                "start",
                {
                    "job_id": "interrupted-browser-job",
                    "total_pages": 1,
                    "pages": [1],
                    "engine_mode": "pdf2zh_layout",
                },
            ),
            (
                "page_completed",
                {
                    "page_number": 1,
                    "original_image": image_url,
                    "translated_image": image_url,
                    "translated_text": "Bản dịch chưa đóng gói xong",
                },
            ),
        ]
    )

    uploaded_file_id = None
    try:
        page.route(
            "**/api/translate/file-stream",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream; charset=utf-8",
                body=truncated_sse,
            ),
        )
        page.goto(f"{BASE_URL}/")
        with page.expect_response("**/api/upload") as upload_info:
            page.set_input_files("#file-input", str(source_path))
        uploaded_file_id = upload_info.value.json()["file_id"]

        page.locator("#start-translate-btn").click()
        expect(page.locator("#progress-text")).to_contain_text(
            "Kết nối kết thúc trước khi file được tạo xong"
        )
        expect(page.locator("#job-history-select option")).to_have_count(1)
        assert (page.locator("#job-history-select option").text_content() or "").startswith("✕")
        expect(page.locator("#download-pdf-btn")).to_be_disabled()
        expect(page.locator("#download-dual-btn")).to_be_disabled()
    finally:
        if uploaded_file_id:
            (settings.upload_dir / f"{uploaded_file_id}.pdf").unlink(missing_ok=True)
