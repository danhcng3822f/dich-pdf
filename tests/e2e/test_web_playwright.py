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

    # Verifies that inside the page card, both the original PDF image and translated PDF image
    # (img[src^="data:image/png;base64,"]) are rendered
    images = page_card.locator('img[src^="data:image/png;base64,"]')
    expect(images).to_have_count(2)

    # Verifies #download-mono-btn and #download-dual-btn are enabled (not disabled)
    mono_btn = page.locator("#download-mono-btn, #download-pdf-btn")
    dual_btn = page.locator("#download-dual-btn")
    expect(mono_btn).to_be_enabled()
    expect(dual_btn).to_be_enabled()
