import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_index_page_returns_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "AI PDF Translator" in response.text
    assert "Cài đặt API Key" in response.text

def test_static_files_accessible():
    response = client.get("/static/css/styles.css")
    assert response.status_code == 200
    assert "glassmorphism" in response.text
