# Tích hợp PDFMathTranslate (pdf2zh) Dual-Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tích hợp toàn diện động cơ dịch tài liệu giữ nguyên layout của PDFMathTranslate (`pdf2zh`) vào AI PDF Translator, tạo thành hệ thống Dual-Engine: Giữ nguyên Layout gốc (PDF2ZH) và Tái cấu trúc Slide (LaTeX Beamer), hiển thị đối chiếu song ngữ thời gian thực trên giao diện web FastAPI và kiểm thử Playwright.

**Architecture:** Tạo module độc lập `app/services/pdf2zh_engine/` kế thừa các thuật toán cốt lõi của PDFMathTranslate (nhận diện layout ONNX, đóng băng công thức `{v*}` và vá luồng toán tử PDF nhị phân). Xây dựng lớp `UnifiedTranslatorAdapter` kết nối linh hoạt với cả API Key cá nhân (`OpenAI`, `DeepSeek`, `Gemini`, `Claude`, `Custom`) lẫn dịch tự động miễn phí (`Google`, `Bing`). Mở rộng API SSE và cập nhật giao diện người dùng để xem ảnh đối chiếu PDF gốc vs PDF dịch thật theo từng trang.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, PyMuPDF (fitz), pdfminer.six, onnxruntime, opencv-python-headless, babeldoc, vanilla JS, Tailwind CSS, KaTeX, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-pdf2zh-dual-engine-integration-design.md`

## Global Constraints

- **Python thực thi**: `C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe`. Mọi lệnh `pip`, `pytest`, `python` phải dùng đúng đường dẫn này.
- **Thư mục lưu trữ**: Font Unicode lưu trong `storage/fonts/`, model ONNX lưu trong `storage/models/`, file xuất lưu trong `storage/exports/`.
- **Tuyệt đối không để xảy ra `ImportError`**: Toàn bộ các thư viện bên thứ ba tùy chọn của `pdf2zh` (`deepl`, `tencentcloud`, `xinference`...) phải được loại bỏ hoặc lazy import. Chỉ dùng các thư viện đã khai báo trong `requirements.txt`.
- **Bảo toàn token công thức**: Token `{v0}`, `{v1}`, `{v2}`... phải được bảo vệ nguyên vẹn khi dịch qua LLM hoặc Google/Bing.
- **Khả năng tương thích ngược**: Chế độ Beamer Slide (`LaTeX_Beamer`) và toàn bộ 12 unit tests hiện tại phải tiếp tục PASS 100%.

---

## Cấu trúc File Dự kiến

| File | Trách nhiệm | Task |
|---|---|---|
| `requirements.txt` | Khai báo các dependency mới (`babeldoc`, `opencv-python-headless`) | 1 |
| `app/config.py` | Bổ sung đường dẫn `fonts_dir`, `models_dir`, `previews_dir` | 1 |
| `app/services/pdf2zh_engine/font_manager.py` | Tải và quản lý font Unicode (`Noto`, `SourceHanSerif`) | 2 |
| `app/services/pdf2zh_engine/doclayout.py` | Quản lý model ONNX DocLayout-YOLO | 2 |
| `app/services/pdf2zh_engine/adapter.py` | Unified Translator Adapter (AI LLMs + Google/Bing Free) | 3 |
| `app/services/pdf2zh_engine/pdfinterp.py` | Bộ chặn và thực thi toán tử PDF (`PDFPageInterpreterEx`) | 4 |
| `app/services/pdf2zh_engine/converter.py` | Dàn trang, gom công thức `{v*}` và vá PDF stream | 4 |
| `app/services/pdf2zh_engine/pipeline.py` | Điều phối dịch từng trang, sinh Mono PDF & Dual PDF | 5 |
| `app/api/schemas.py` | Pydantic model cho Engine Mode và tùy chọn dịch | 6 |
| `app/api/routes.py` | REST API `/translate/stream`, `/download`, `/test-connection` | 6 |
| `app/templates/index.html` | UI: Tab chọn Engine, Provider Google/Bing, nút tải Mono/Dual | 7 |
| `app/static/js/api.js` | Hỗ trợ cấu hình provider miễn phí không cần API Key | 7 |
| `app/static/js/app.js` | Điều khiển stream và hiển thị ảnh đối chiếu song ngữ PDF2ZH | 7 |
| `tests/test_pdf2zh_adapter.py` | Unit test cho Translator Adapter | 3 |
| `tests/test_pdf2zh_pipeline.py` | Integration test cho pipeline xuất Mono/Dual PDF | 5 |
| `tests/e2e/test_web_playwright.py` | E2E Browser test với Playwright | 8 |

---

## Task 1: Cài đặt Dependencies & Cấu hình Hệ thống

**Files:**
- Modify: `requirements.txt`
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces: `settings.fonts_dir`, `settings.models_dir`, `settings.previews_dir`

- [ ] **Step 1: Cập nhật `requirements.txt`**
Thêm `opencv-python-headless>=4.9.0` và `babeldoc>=0.6.0` vào `requirements.txt`.

- [ ] **Step 2: Cài đặt packages vào Python 3.11**
Chạy lệnh cài đặt:
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pip install babeldoc opencv-python-headless`

- [ ] **Step 3: Viết test cho thư mục cấu hình mới**
Tạo `tests/test_config.py`:
```python
from app.config import settings

def test_settings_directories():
    assert settings.upload_dir.exists()
    assert settings.export_dir.exists()
    assert settings.fonts_dir.exists()
    assert settings.models_dir.exists()
    assert settings.previews_dir.exists()
```

- [ ] **Step 4: Cập nhật `app/config.py`**
Bổ sung `FONTS_DIR`, `MODELS_DIR`, `PREVIEWS_DIR` và tự động tạo thư mục:
```python
FONTS_DIR = STORAGE_DIR / "fonts"
MODELS_DIR = STORAGE_DIR / "models"
PREVIEWS_DIR = STORAGE_DIR / "previews"

FONTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
```
Gán vào class `Settings`.

- [ ] **Step 5: Chạy test config**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_config.py -v`
Xác nhận PASS.

- [ ] **Step 6: Commit**
`git add requirements.txt app/config.py tests/test_config.py`
`git commit -m "feat: add pdf2zh dependencies and storage directory settings"`

---

## Task 2: Quản lý Font Unicode & Model ONNX Layout

**Files:**
- Create: `app/services/pdf2zh_engine/font_manager.py`
- Create: `app/services/pdf2zh_engine/doclayout.py`
- Create: `app/services/pdf2zh_engine/__init__.py`
- Test: `tests/test_font_and_layout.py`

**Interfaces:**
- Produces:
  - `get_font_path(lang: str) -> str`
  - `load_layout_model() -> OnnxModel`
  - `OnnxModel.predict(image, imgsz) -> list`

- [ ] **Step 1: Viết test cho Font Manager & DocLayout loader**
Tạo `tests/test_font_and_layout.py`:
```python
from app.services.pdf2zh_engine.font_manager import get_font_path
from app.services.pdf2zh_engine.doclayout import load_layout_model
from pathlib import Path

def test_get_font_path():
    path = get_font_path("vi")
    assert path is not None
    assert Path(path).exists()

def test_load_layout_model():
    model = load_layout_model()
    assert model is not None
    assert hasattr(model, "predict")
```

- [ ] **Step 2: Viết `font_manager.py`**
Tận dụng `babeldoc.assets.assets.get_font_and_metadata` để tự động tải font `GoNotoKurrent-Regular.ttf` hoặc font CJK về `storage/fonts/`.

- [ ] **Step 3: Viết `doclayout.py`**
Sử dụng `DocLayout-YOLO` thông qua `OnnxModel` từ `babeldoc.assets.assets.get_doclayout_onnx_model_path`, khởi tạo `onnxruntime.InferenceSession` với backend CPU/DirectML.

- [ ] **Step 4: Chạy test và xác nhận PASS**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_font_and_layout.py -v`

- [ ] **Step 5: Commit**
`git add app/services/pdf2zh_engine/ tests/test_font_and_layout.py`
`git commit -m "feat: implement font manager and onnx doclayout model loader"`

---

## Task 3: Unified Translator Adapter (AI LLMs + Google/Bing Free)

**Files:**
- Create: `app/services/pdf2zh_engine/adapter.py`
- Test: `tests/test_pdf2zh_adapter.py`

**Interfaces:**
- Produces:
  - `class PDF2ZHAdapter`:
    - `translate(text: str) -> str`
    - `translate_batch(texts: list[str]) -> list[str]`
  - `create_adapter(provider: str, api_key: str, model: str, base_url: str, lang_in: str, lang_out: str) -> PDF2ZHAdapter`

- [ ] **Step 1: Viết test thất bại cho Adapter**
Tạo `tests/test_pdf2zh_adapter.py`:
```python
import pytest
from app.services.pdf2zh_engine.adapter import PDF2ZHAdapter

def test_adapter_preserves_tokens():
    # Giả lập phản hồi dịch
    class MockEngine:
        def translate(self, text):
            return text.replace("Hello", "Xin chào")

    adapter = PDF2ZHAdapter(engine=MockEngine())
    result = adapter.translate("Hello {v0}, this is {v1}.")
    assert "{v0}" in result
    assert "{v1}" in result
    assert "Xin chào" in result

@pytest.mark.asyncio
async def test_google_free_translate():
    from app.services.pdf2zh_engine.adapter import GoogleFreeTranslator
    translator = GoogleFreeTranslator(lang_in="en", lang_out="vi")
    translated = translator.translate("Deep learning with {v0} is effective.")
    assert "{v0}" in translated
    assert len(translated) > 5
```

- [ ] **Step 2: Viết `GoogleFreeTranslator` & `BingFreeTranslator`**
Tích hợp thuật toán gọi endpoint Google Translate miễn phí của `pdf2zh`, tự động encode và giữ nguyên chuỗi `{v\d+}`.

- [ ] **Step 3: Viết `LLMTranslator` cho AIConfig**
Kết nối với `httpx` gọi OpenAI, DeepSeek, Gemini, Claude với System Prompt yêu cầu bắt buộc giữ nguyên các token `{v0}`, `{v1}`...

- [ ] **Step 4: Chạy test xác nhận PASS**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_pdf2zh_adapter.py -v`

- [ ] **Step 5: Commit**
`git add app/services/pdf2zh_engine/adapter.py tests/test_pdf2zh_adapter.py`
`git commit -m "feat: implement unified translator adapter with token preservation"`

---

## Task 4: Converter & Bộ Vá Luồng Toán Tử PDF

**Files:**
- Create: `app/services/pdf2zh_engine/pdfinterp.py`
- Create: `app/services/pdf2zh_engine/converter.py`
- Test: `tests/test_pdf2zh_converter.py`

**Interfaces:**
- Produces:
  - `class PDFPageInterpreterEx(PDFPageInterpreter)`
  - `class TranslateConverter(PDFConverterEx)`
  - `patch_page(page, model, converter) -> str`

- [ ] **Step 1: Viết `pdfinterp.py`**
Chuyển `PDFPageInterpreterEx` từ `PDFMathTranslate-main/pdf2zh/pdfinterp.py` sang, tối ưu hóa bộ nhớ và loại bỏ các biến không dùng.

- [ ] **Step 2: Viết `converter.py`**
Port `TranslateConverter` từ `PDFMathTranslate-main/pdf2zh/converter.py`:
- Dùng `vflag()` quét font LaTeX toán học (`CM`, `TeX-`, `Math`...) và ký tự Hy Lạp.
- Gom cụm công thức thành `{v0}`, `{v1}`...
- Gọi `adapter.translate()` để dịch đoạn văn.
- Dàn trang mới bằng font Noto Unicode và vá luồng opcodes PDF (`/font size Tf 1 0 0 1 x y Tm [<hex>] TJ`).
- Xóa bỏ hoàn toàn các import cứng (`deepl`, `tencentcloud`, `xinference`...).

- [ ] **Step 3: Viết test vá PDF**
Tạo `tests/test_pdf2zh_converter.py`:
Kiểm tra mở một file PDF đơn giản, chạy qua `TranslateConverter` và thu được luồng stream opcodes đã sửa đổi.

- [ ] **Step 4: Chạy test xác nhận PASS**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_pdf2zh_converter.py -v`

- [ ] **Step 5: Commit**
`git add app/services/pdf2zh_engine/pdfinterp.py app/services/pdf2zh_engine/converter.py tests/test_pdf2zh_converter.py`
`git commit -m "feat: port and clean translate converter and pdf interpreter"`

---

## Task 5: Pipeline Điều Phối & Xuất File Mono/Dual PDF

**Files:**
- Create: `app/services/pdf2zh_engine/pipeline.py`
- Test: `tests/test_pdf2zh_pipeline.py`

**Interfaces:**
- Produces:
  - `async def process_pdf2zh_stream(file_path, page_indices, target_lang, adapter) -> AsyncGenerator[dict, None]`
  - `create_dual_pdf(original_pdf_path, translated_pdf_path, output_path) -> Path`

- [ ] **Step 1: Viết test cho Pipeline**
Tạo `tests/test_pdf2zh_pipeline.py`:
Tạo một file PDF test 2 trang, chạy `pipeline`, kiểm tra:
1. Mỗi trang trả về `original_image_base64` và `translated_image_base64`.
2. Tạo thành công file `mono.pdf` (2 trang) và file `dual.pdf` (4 trang xen kẽ).

- [ ] **Step 2: Viết `app/services/pdf2zh_engine/pipeline.py`**
- Mở PDF nguồn bằng PyMuPDF `fitz.Document`.
- Chèn font Unicode (`Noto`).
- Duyệt từng trang được chọn:
  - Render ảnh trang gốc thành base64 PNG.
  - Phân tích layout và vá luồng đối tượng PDF.
  - Render ảnh trang dịch mới thành base64 PNG.
  - Yield event trạng thái về cho SSE generator.
- Lưu file `mono.pdf` và tạo file `dual.pdf` bằng phương pháp xen kẽ (`move_page`).

- [ ] **Step 3: Chạy test xác nhận PASS**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_pdf2zh_pipeline.py -v`

- [ ] **Step 4: Commit**
`git add app/services/pdf2zh_engine/pipeline.py tests/test_pdf2zh_pipeline.py`
`git commit -m "feat: implement pdf2zh stream pipeline and mono/dual pdf generation"`

---

## Task 6: Mở Rộng API Routes & Streaming

**Files:**
- Modify: `app/api/schemas.py` (hoặc tạo mới)
- Modify: `app/api/routes.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- API Endpoints:
  - `POST /api/translate/stream`: Thêm `engine_mode: "pdf2zh_layout" | "beamer_slide"`.
  - `GET /api/download/{job_id}/{fmt}`: Thêm `fmt="dual_pdf"`, `fmt="mono_pdf"`.
  - `POST /api/test-connection`: Hỗ trợ kiểm tra Google/Bing không cần API key.

- [ ] **Step 1: Cập nhật schema trong `app/api/routes.py`**
```python
class TranslationStreamRequest(BaseModel):
    file_id: str
    target_lang: str = "Vietnamese"
    style: str = "Default"
    page_range: str = "all"
    engine_mode: str = "pdf2zh_layout"  # "pdf2zh_layout" | "beamer_slide"
    ai_config: AIConfig
```

- [ ] **Step 2: Cập nhật luồng SSE trong `/api/translate/stream`**
Nếu `req.engine_mode == "pdf2zh_layout"`, kích hoạt `process_pdf2zh_stream` và phát sự kiện:
```json
{
  "page_number": 1,
  "original_image": "data:image/png;base64,...",
  "translated_image": "data:image/png;base64,...",
  "engine_mode": "pdf2zh_layout"
}
```
Nếu `req.engine_mode == "beamer_slide"`, giữ nguyên luồng Beamer LaTeX hiện tại.

- [ ] **Step 3: Cập nhật endpoint download `/api/download/{job_id}/{fmt}`**
Cho phép tải `mono_pdf` (hoặc `pdf`), `dual_pdf`, `docx`, `md`, `tex`.

- [ ] **Step 4: Cập nhật test `tests/test_api_routes.py`**
Chạy test API hiện tại và bổ sung test cho endpoint mới.

- [ ] **Step 5: Chạy test xác nhận PASS**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_api_routes.py -v`

- [ ] **Step 6: Commit**
`git add app/api/routes.py tests/test_api_routes.py`
`git commit -m "feat: add engine_mode to translation api and support dual pdf download"`

---

## Task 7: Cập Nhật Giao Diện Web & Đối Chiếu Song Ngữ Trực Quan

**Files:**
- Modify: `app/templates/index.html`
- Modify: `app/static/js/api.js`
- Modify: `app/static/js/app.js`
- Modify: `app/static/css/styles.css`

- [ ] **Step 1: Cập nhật `app/templates/index.html`**
- Thêm Tab chuyển đổi Chế độ Dịch:
  - 📑 **Giữ nguyên Layout gốc (PDF2ZH Engine)** (Mặc định)
  - 📊 **Tái cấu trúc Slide (LaTeX Beamer)**
- Bổ sung Provider Google (Miễn phí) & Bing (Miễn phí) trong Modal Cài đặt.
- Thêm nút tải về: **"PDF Dịch (Mono)"** và **"PDF Song ngữ (Dual)"**.

- [ ] **Step 2: Cập nhật `app/static/js/api.js`**
Hỗ trợ provider `google` và `bing`, tự động bỏ qua kiểm tra API key rỗng đối với 2 provider miễn phí này.

- [ ] **Step 3: Cập nhật `app/static/js/app.js`**
- Khi nhận sự kiện `page_completed`:
  - Nếu ở chế độ `pdf2zh_layout`: Render cột trái là ảnh trang gốc, cột phải là ảnh trang PDF dịch thật (`translated_image`).
  - Nếu ở chế độ `beamer_slide`: Render Beamer preview như cũ.
- Bật/tắt các nút tải về `mono_pdf`, `dual_pdf`, `docx`, `md`, `tex` tương ứng với engine đã chọn.

- [ ] **Step 4: Chạy lại toàn bộ test suite cơ bản**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest -v`

- [ ] **Step 5: Commit**
`git add app/templates/index.html app/static/js/ app/static/css/`
`git commit -m "feat: add engine switcher, dual-pdf preview and google/bing free providers to web ui"`

---

## Task 8: Kiểm Thử E2E Tự Động Bằng Playwright

**Files:**
- Create: `tests/e2e/test_web_playwright.py`

- [ ] **Step 1: Cài đặt Playwright trong Python 3.11**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pip install pytest-playwright playwright`
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m playwright install chromium`

- [ ] **Step 2: Viết test Playwright E2E**
Tạo file `tests/e2e/test_web_playwright.py`:
- Mở server FastAPI chạy nền trên cổng ngẫu nhiên hoặc `http://127.0.0.1:8000`.
- Truy cập trang chủ bằng browser chromium.
- Kiểm tra render giao diện: Tiêu đề, Tab chọn Engine, Khung kéo thả file.
- Chọn provider Google miễn phí, chuyển đổi giữa Tab PDF2ZH và Tab Beamer.
- Upload 1 file PDF mẫu trong `storage/uploads/`.
- Nhấn "Bắt đầu Dịch Ngay", kiểm tra nhận SSE stream và thẻ kết quả xuất hiện 2 ảnh đối chiếu song ngữ.
- Kiểm tra các nút tải về kích hoạt thành công.

- [ ] **Step 3: Chạy test Playwright**
`& 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/e2e/test_web_playwright.py -v`

- [ ] **Step 4: Kiểm tra trực tiếp bằng MCP Playwright trên browser**
Dùng công cụ MCP Playwright để mở trang web kiểm tra trực quan.

- [ ] **Step 5: Commit**
`git add tests/e2e/test_web_playwright.py`
`git commit -m "test: add playwright e2e tests for dual-engine translation web app"`
