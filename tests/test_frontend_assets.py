import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_index_page_returns_html():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "AI PDF Translator" in html
    assert "Cài đặt API Key" in html

    # Verify Engine Switcher tabs
    assert 'id="engine-pdf2zh"' in html
    assert 'id="engine-beamer"' in html
    assert "Giữ nguyên Layout (PDF2ZH)" in html
    assert "Slide thuyết trình (LaTeX Beamer)" in html

    # Verify Provider options
    assert 'value="google"' in html
    assert 'value="bing"' in html
    assert "Google Dịch (Miễn phí, không cần API Key)" in html
    assert "Bing Dịch (Miễn phí, không cần API Key)" in html
    assert "Không cần API Key" in html

    # Free translators can auto-detect or use an explicit source language.
    assert 'id="source-lang-select"' in html
    assert 'value="auto"' in html
    assert "Tự động nhận diện" in html

    # Verify Export buttons
    assert 'id="download-docx-btn"' in html
    assert 'id="download-pdf-btn"' in html
    assert 'id="download-dual-btn"' in html
    assert 'id="download-md-btn"' in html
    assert 'id="download-tex-btn"' in html
    assert "PDF Song ngữ (Dual)" in html
    assert "PDF Dịch (Mono)" in html


def test_static_css_accessible():
    response = client.get("/static/css/styles.css")
    assert response.status_code == 200
    css = response.text
    assert "glassmorphism" in css
    assert "engine-tab-btn" in css
    assert "dual-preview-container" in css


def test_static_api_js_accessible():
    response = client.get("/static/js/api.js")
    assert response.status_code == 200
    js = response.text
    assert 'google: ""' in js
    assert 'bing: ""' in js
    assert "DEFAULT_MODELS" in js
    assert "isFreeProvider" in js


def test_static_app_js_accessible():
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    js = response.text
    assert "currentEngineMode" in js
    assert "pdf2zh_layout" in js
    assert "beamer_slide" in js
    assert "download-dual-btn" in js
    assert "dual_ready" in js
    assert "source_lang" in js
