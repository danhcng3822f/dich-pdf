# PDF Translation Web Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng ứng dụng web hoàn chỉnh cho phép tải lên file PDF, phân tích và xem trước từng trang, dịch thuật đa ngôn ngữ với các AI Providers (OpenAI, DeepSeek, Google Gemini, Anthropic Claude, Custom OpenAI-compatible) qua luồng SSE thời gian thực, xem song ngữ trực tiếp và xuất file dịch ra PDF/Word (.docx)/Markdown.

**Architecture:** 
- Backend FastAPI (Python 3.11) cấu trúc module hóa: `pdf_service` (xử lý & render preview PDF bằng PyMuPDF), `ai_service` (gọi AI API với httpx async), `doc_service` (xuất DOCX & PDF có font Unicode tiếng Việt), `routes` (upload, test api, stream SSE, download).
- Frontend Single Page App (HTML5 + TailwindCSS + Vanilla JS) giao diện song ngữ side-by-side, quản lý API key an toàn trên LocalStorage.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, PyMuPDF (`fitz`), python-docx, reportlab, httpx, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-pdf-translation-design.md`

## Global Constraints
- Python 3.11 compatibility.
- Không lưu API Key của người dùng lên server database hay file log; chỉ truyền qua request header/body phiên làm việc hoặc lưu client-side LocalStorage.
- Hỗ trợ đầy đủ tiếng Việt có dấu (UTF-8, font Unicode khi xuất PDF/Word).
- Kiểm thử đầy đủ với pytest cho toàn bộ core services và endpoints.

---

### Task 1: Environment & Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `storage/uploads/.gitkeep`
- Create: `storage/exports/.gitkeep`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `app.config.Settings` (paths for storage, default app config)

- [ ] **Step 1: Write requirements.txt**
```text
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
python-multipart>=0.0.9
pymupdf>=1.24.0
python-docx>=1.1.0
reportlab>=4.1.0
httpx>=0.27.0
pydantic>=2.6.0
pydantic-settings>=2.2.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create directory structure and app/config.py**
```python
import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
EXPORT_DIR = STORAGE_DIR / "exports"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    app_name: str = "PDF Translation AI"
    upload_dir: Path = UPLOAD_DIR
    export_dir: Path = EXPORT_DIR
    max_upload_size_mb: int = 50

settings = Settings()
```

- [ ] **Step 3: Install dependencies and test import**
Run: `pip install -r requirements.txt`
Expected: Dependencies installed successfully.

- [ ] **Step 4: Commit**
```bash
git add requirements.txt app/ storage/ tests/
git commit -m "chore: setup project structure, config, and dependencies"
```

---

### Task 2: PDF Processing Service (`app/services/pdf_service.py`)

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/pdf_service.py`
- Test: `tests/test_pdf_service.py`

**Interfaces:**
- Produces:
  - `parse_page_ranges(range_str: str, total_pages: int) -> list[int]`
  - `get_pdf_metadata(file_path: Path) -> dict`
  - `extract_page_content(file_path: Path, page_num: int) -> dict` (returns text, blocks, preview_base64_image)
  - `render_page_image(doc: fitz.Document, page_num: int) -> str` (base64 PNG)

- [ ] **Step 1: Write failing test in `tests/test_pdf_service.py`**
```python
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
```

- [ ] **Step 2: Run test to verify failure**
Run: `pytest tests/test_pdf_service.py`
Expected: FAIL (ModuleNotFoundError or Import error)

- [ ] **Step 3: Implement `app/services/pdf_service.py`**
```python
import base64
from pathlib import Path
import fitz  # PyMuPDF

def parse_page_ranges(range_str: str, total_pages: int) -> list[int]:
    if not range_str or range_str.strip().lower() in ["all", ""]:
        return list(range(total_pages))
    
    pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start, end = int(sub[0]), int(sub[1])
                for p in range(start, end + 1):
                    if 1 <= p <= total_pages:
                        pages.add(p - 1)
        elif part.isdigit():
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
                
    result = sorted(list(pages))
    return result if result else list(range(total_pages))

def get_pdf_metadata(file_path: Path) -> dict:
    doc = fitz.open(str(file_path))
    total_pages = len(doc)
    doc.close()
    file_size = file_path.stat().st_size
    return {
        "filename": file_path.name,
        "total_pages": total_pages,
        "file_size": file_size,
    }

def render_page_image(page: fitz.Page, zoom: float = 1.5) -> str:
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"

def extract_page_content(file_path: Path, page_index: int) -> dict:
    doc = fitz.open(str(file_path))
    if page_index < 0 or page_index >= len(doc):
        doc.close()
        raise IndexError(f"Page index {page_index} out of range (0-{len(doc)-1})")
    
    page = doc[page_index]
    text = page.get_text("text").strip()
    image_b64 = render_page_image(page)
    doc.close()
    
    return {
        "page_number": page_index + 1,
        "page_index": page_index,
        "text": text,
        "image_base64": image_b64,
    }
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_pdf_service.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**
```bash
git add app/services/ tests/test_pdf_service.py
git commit -m "feat: implement PDF parsing, metadata extraction, and page image rendering"
```

---

### Task 3: Multi-Provider AI Translation Client (`app/services/ai_service.py`)

**Files:**
- Create: `app/services/ai_service.py`
- Test: `tests/test_ai_service.py`

**Interfaces:**
- Produces:
  - `async translate_text(text: str, target_lang: str, style: str, config: AIConfig) -> str`
  - `async test_api_connection(config: AIConfig) -> dict`
  - `AIConfig` (Pydantic model: `provider`, `api_key`, `model`, `base_url`, `custom_prompt`)

- [ ] **Step 1: Write failing test in `tests/test_ai_service.py`**
```python
import pytest
from app.services.ai_service import AIConfig, build_system_prompt, translate_text

def test_build_system_prompt():
    prompt = build_system_prompt("Vietnamese", "Academic")
    assert "Vietnamese" in prompt
    assert "academic" in prompt.lower()
    assert "Do not add any conversational prelude" in prompt

@pytest.mark.asyncio
async def test_translate_text_empty():
    config = AIConfig(provider="openai", api_key="dummy", model="gpt-4o-mini")
    res = await translate_text("", "Vietnamese", "Default", config)
    assert res == ""
```

- [ ] **Step 2: Run test to verify failure**
Run: `pytest tests/test_ai_service.py`
Expected: FAIL

- [ ] **Step 3: Implement `app/services/ai_service.py`**
```python
import httpx
from pydantic import BaseModel, Field
from typing import Optional

class AIConfig(BaseModel):
    provider: str = Field(..., description="openai | deepseek | gemini | claude | custom")
    api_key: str
    model: Optional[str] = None
    base_url: Optional[str] = None
    custom_prompt: Optional[str] = None
    temperature: float = 0.3

STYLE_DESCRIPTIONS = {
    "Default": "Accurate, natural and context-aware.",
    "Academic": "Formal, academic terminology, rigorous tone.",
    "Business": "Professional, business and executive friendly tone.",
    "Technical": "Precise technical vocabulary, preserving formulas and code terms.",
    "Casual": "Friendly, easy to understand and conversational tone."
}

def build_system_prompt(target_lang: str, style: str, custom_instruction: Optional[str] = None) -> str:
    style_desc = STYLE_DESCRIPTIONS.get(style, STYLE_DESCRIPTIONS["Default"])
    prompt = (
        f"You are a professional document translator. Translate the given text accurately into {target_lang}.\n"
        f"Translation Style: {style_desc}\n"
        "Guidelines:\n"
        "1. Preserve the original paragraph layout, markdown formats, math equations, code snippets, and variable names.\n"
        "2. Do not add any conversational prelude, explanation, or notes. Output ONLY the translated text.\n"
        "3. Maintain high fluency and correct domain terminology."
    )
    if custom_instruction and custom_instruction.strip():
        prompt += f"\nAdditional User Instructions: {custom_instruction.strip()}"
    return prompt

async def translate_openai_compatible(text: str, system_prompt: str, config: AIConfig, default_base_url: str, default_model: str) -> str:
    base_url = (config.base_url.rstrip("/") if config.base_url else default_base_url)
    model = config.model if config.model else default_model
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": config.temperature,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"AI Provider error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

async def translate_gemini(text: str, system_prompt: str, config: AIConfig) -> str:
    model = config.model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"temperature": config.temperature}
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()

async def translate_claude(text: str, system_prompt: str, config: AIConfig) -> str:
    model = config.model or "claude-3-5-sonnet-20241022"
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": text}],
        "temperature": config.temperature
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["content"][0]["text"].strip()

async def translate_text(text: str, target_lang: str, style: str, config: AIConfig) -> str:
    if not text or not text.strip():
        return ""
    
    system_prompt = build_system_prompt(target_lang, style, config.custom_prompt)
    provider = config.provider.lower()
    
    if provider == "openai":
        return await translate_openai_compatible(text, system_prompt, config, "https://api.openai.com/v1", "gpt-4o-mini")
    elif provider == "deepseek":
        return await translate_openai_compatible(text, system_prompt, config, "https://api.deepseek.com/v1", "deepseek-chat")
    elif provider == "gemini":
        return await translate_gemini(text, system_prompt, config)
    elif provider == "claude":
        return await translate_claude(text, system_prompt, config)
    elif provider == "custom":
        if not config.base_url:
            raise ValueError("Custom provider requires Base URL")
        return await translate_openai_compatible(text, system_prompt, config, config.base_url, config.model or "default")
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")

async def test_api_connection(config: AIConfig) -> dict:
    test_result = await translate_text("Hello", "Vietnamese", "Default", config)
    return {"status": "success", "sample_translation": test_result}
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_ai_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**
```bash
git add app/services/ai_service.py tests/test_ai_service.py
git commit -m "feat: implement multi-provider AI translation client and prompt builder"
```

---

### Task 4: Document Generation Service (`app/services/doc_service.py`)

**Files:**
- Create: `app/services/doc_service.py`
- Test: `tests/test_doc_service.py`

**Interfaces:**
- Produces:
  - `generate_docx(translated_pages: list[dict], output_path: Path, doc_title: str) -> Path`
  - `generate_pdf(translated_pages: list[dict], output_path: Path, doc_title: str) -> Path`
  - `generate_markdown(translated_pages: list[dict], output_path: Path, doc_title: str) -> Path`

- [ ] **Step 1: Write failing test in `tests/test_doc_service.py`**
```python
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
```

- [ ] **Step 2: Run test to verify failure**
Run: `pytest tests/test_doc_service.py`
Expected: FAIL

- [ ] **Step 3: Implement `app/services/doc_service.py`**
```python
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab.rl_config
import sys

def generate_docx(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    doc = Document()
    
    # Title
    title = doc.add_heading(doc_title, level=0)
    title.alignment = 1 # Center
    
    for i, page in enumerate(translated_pages):
        page_num = page.get("page_number", i + 1)
        
        # Section header
        heading = doc.add_heading(f"Trang {page_num}", level=2)
        
        # Content paragraphs
        text = page.get("translated_text", "")
        for line in text.split("\n"):
            line = line.strip()
            if line:
                p = doc.add_paragraph(line)
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.line_spacing = 1.15
        
        if i < len(translated_pages) - 1:
            doc.add_page_break()
            
    doc.save(str(output_path))
    return output_path

def generate_pdf(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    # Set up ReportLab with standard Helvetica or fallback unicode font
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        alignment=1, # Center
        spaceAfter=15
    )
    page_header_style = ParagraphStyle(
        'PageHeader',
        parent=styles['Heading2'],
        fontSize=12,
        leading=16,
        textColor=RGBColor(59, 130, 246),
        spaceBefore=10,
        spaceAfter=8
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        spaceAfter=6
    )
    
    story = [Paragraph(doc_title, title_style), Spacer(1, 10)]
    
    for i, page in enumerate(translated_pages):
        page_num = page.get("page_number", i + 1)
        story.append(Paragraph(f"--- Trang {page_num} ---", page_header_style))
        
        text = page.get("translated_text", "")
        # Escape xml special chars
        for line in text.split("\n"):
            clean_line = line.strip().replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            if clean_line:
                story.append(Paragraph(clean_line, body_style))
        
        if i < len(translated_pages) - 1:
            story.append(PageBreak())
            
    doc.build(story)
    return output_path

def generate_markdown(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    lines = [f"# {doc_title}\n\n"]
    for page in translated_pages:
        page_num = page.get("page_number", 1)
        lines.append(f"## Trang {page_num}\n\n")
        lines.append(f"{page.get('translated_text', '')}\n\n")
        lines.append("---\n\n")
        
    output_path.write_text("".join(lines), encoding="utf-8")
    return output_path
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_doc_service.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**
```bash
git add app/services/doc_service.py tests/test_doc_service.py
git commit -m "feat: implement document export generators (DOCX, PDF, Markdown)"
```

---

### Task 5: API Endpoints & SSE Streaming (`app/api/routes.py` & `app/main.py`)

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/routes.py`
- Create: `app/main.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Produces:
  - `POST /api/upload`: Nhận file PDF, lưu vào `storage/uploads`, trả về metadata (id, pages, filename, size)
  - `POST /api/test-connection`: Test API key với AI provider
  - `POST /api/translate/stream`: SSE Endpoint truyền dữ liệu tiến độ và bản dịch từng trang thời gian thực
  - `GET /api/download/{job_id}/{format}`: Tải file kết quả (`docx`, `pdf`, `md`)

- [ ] **Step 1: Write test for API routes in `tests/test_api_routes.py`**
```python
import pytest
from fastapi.testclient import TestClient
import io
import fitz
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
```

- [ ] **Step 2: Implement `app/api/routes.py` and `app/main.py`**
- [ ] **Step 3: Run test to verify it passes**
Run: `pytest tests/test_api_routes.py -v`
Expected: PASS

- [ ] **Step 4: Commit**
```bash
git add app/api/ app/main.py tests/test_api_routes.py
git commit -m "feat: implement REST and SSE streaming endpoints for PDF translation"
```

---

### Task 6: Modern Single Page Web UI (HTML, Tailwind CSS, JS)

**Files:**
- Create: `app/templates/index.html`
- Create: `app/static/css/styles.css`
- Create: `app/static/js/api.js`
- Create: `app/static/js/app.js`

**Features:**
- Settings Modal: Lưu cấu hình API Key (OpenAI, DeepSeek, Gemini, Claude, Custom) vào `localStorage`, Test Connection indicator.
- Upload Area: Kéo thả file PDF, hiển thị preview thông tin file, chọn ngôn ngữ đích & phạm vi trang & văn phong.
- Live Dual-Pane Workspace:
  - Cột trái: Ảnh preview trang gốc của PDF.
  - Cột phải: Bản dịch tương ứng của trang, hiển thị tiến độ thời gian thực qua SSE.
  - Chuyển đổi chế độ: Song ngữ (Split View) hoặc Full Bản dịch.
- Export Bar: Tải nhanh file Word (.docx), PDF dịch, Markdown (.md).

- [ ] **Step 1: Create `app/static/js/api.js` with client storage & fetch helpers**
- [ ] **Step 2: Create `app/static/js/app.js` with UI state management, SSE handling, and rendering**
- [ ] **Step 3: Create `app/templates/index.html` with responsive Tailwind UI, icons, modals, and dual-pane reader**
- [ ] **Step 4: Verify static assets mounting and UI loading**
- [ ] **Step 5: Commit**
```bash
git add app/templates/ app/static/
git commit -m "feat: build responsive modern SPA frontend for PDF translation"
```

---

### Task 7: Entrypoint Script, Verification & Documentation

**Files:**
- Create: `run.py`
- Create: `README.md`

- [ ] **Step 1: Create `run.py` for easy local launch**
```python
import uvicorn

if __name__ == "__main__":
    print("Starting PDF Translation Web Server at http://127.0.0.1:8000 ...")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
```

- [ ] **Step 2: Write complete README.md with instructions (cách lấy API key, cách chạy web)**
- [ ] **Step 3: Run complete test suite**
Run: `pytest`
Expected: All tests pass.

- [ ] **Step 4: Commit**
```bash
git add run.py README.md
git commit -m "docs: add starter script and comprehensive documentation"
```

---
