# Technical Design Specification: PDF Translation Web Application

**Date**: 2026-09-02  
**Status**: Approved  
**Stack**: Python 3.11, FastAPI, PyMuPDF (fitz), python-docx, ReportLab, HTML5/TailwindCSS/JS  

---

## 1. Mục tiêu & Tổng quan
Hệ thống là một ứng dụng Web trực quan, gọn nhẹ và bảo mật cho phép người dùng tải lên tài liệu PDF, chọn ngôn ngữ đích, kết nối với API Key AI của mình (OpenAI, DeepSeek, Google Gemini, Anthropic Claude, hoặc Custom OpenAI-compatible endpoint), theo dõi tiến độ dịch thời gian thực theo từng trang, xem trước bản dịch song ngữ trực tiếp và tải về các định dạng (PDF dịch, Word `.docx`, Markdown).

---

## 2. Kiến trúc Hệ thống (Architecture)

```
+-------------------------------------------------------------+
|                      Browser (Frontend)                     |
|  - Modern Single Page App (HTML5 + TailwindCSS + Vanilla JS) |
|  - LocalStorage (API Keys & Preferences - Client-side Only)  |
|  - SSE EventSource (Real-time translation progress)         |
|  - Split View Viewer (Original PDF Preview vs Translated)   |
+------------------------------+------------------------------+
                               | HTTP Multipart / SSE Stream
                               v
+-------------------------------------------------------------+
|                   FastAPI Backend (Python 3.11)             |
|  - Router & Endpoints (/api/upload, /api/translate/stream)   |
|  - PDF Processing Engine (PyMuPDF / fitz)                   |
|  - Document Generator (python-docx, ReportLab)              |
|  - Multi-Provider AI Translation Client (httpx Async)       |
+------------------------------+------------------------------+
                               |
            +------------------+------------------+
            |                  |                  |
            v                  v                  v
     [OpenAI / DeepSeek]   [Gemini API]    [Anthropic API]
     (or Custom Endpoint)
```

---

## 3. Chi tiết các Module & Thành phần

### 3.1. Frontend UI/UX
- **Settings Modal**:
  - Chọn Provider: `OpenAI`, `DeepSeek`, `Google Gemini`, `Anthropic Claude`, `Custom (OpenAI-compatible)`.
  - Nhập API Key, Base URL (cho Custom), Custom Model Name, Temperature.
  - Test Connection Button (gọi request test nhanh để xác thực API key).
  - Toàn bộ key được lưu trong `localStorage` trình duyệt của người dùng, không bao giờ lưu trữ vĩnh viễn trên server.
- **Upload & Configuration Area**:
  - Drag & Drop vùng tải file PDF.
  - Hiển thị metadata file: Tên file, dung lượng, tổng số trang.
  - Lựa chọn ngôn ngữ nguồn (Auto-detect) và ngôn ngữ đích (Tiếng Việt, Tiếng Anh, Tiếng Trung, Tiếng Nhật, Tiếng Hàn, Tiếng Pháp, Tiếng Đức, v.v.).
  - Tùy chọn phạm vi trang (Page Range): Toàn bộ (All) hoặc chỉ định (VD: `1-5, 8, 10-12`).
  - Lựa chọn văn phong / Custom Prompt (Học thuật, Kinh doanh, Tự nhiên, Chuyên ngành Kỹ thuật/Y khoa).
- **Live Progress & Streaming View**:
  - Thanh tiến trình tổng % và hiển thị trạng thái `Đang xử lý trang X / Y...`.
  - Tự động hiển thị nội dung dịch của từng trang ngay khi nhận được qua SSE (Server-Sent Events).
- **Dual-Pane Interactive Viewer**:
  - Cột trái: Xem trước hình ảnh của trang PDF gốc (được render bằng PyMuPDF dưới dạng webp/png base64).
  - Cột phải: Bản dịch tương ứng của trang, hỗ trợ hiển thị Markdown hoặc định dạng văn bản gốc.
  - Nút chuyển chế độ: *Song ngữ song song* hoặc *Chỉ xem bản dịch*.
- **Export Toolbar**:
  - Nút tải **Word (.docx)** (giữ phân đoạn, tiêu đề, bảng biểu nếu có).
  - Nút tải **PDF Đã Dịch** (tạo file PDF mới chứa nội dung dịch tương ứng từng trang).
  - Nút tải **Markdown (.md)** hoặc **Text (.txt)**.

### 3.2. Backend Core Services

#### A. PDF Processing Engine (`app/services/pdf_service.py`)
- Sử dụng `PyMuPDF` (`fitz`) để:
  - Trích xuất text từng trang theo khối đoạn văn (blocks) bảo toàn thứ tự đọc.
  - Render ảnh preview độ phân giải tối ưu cho từng trang để hiển thị lên UI song ngữ.
  - Phân tích và lọc khoảng trang theo cấu hình người dùng (ví dụ parse chuỗi `1-3, 5` thành danh sách index `[0, 1, 2, 4]`).

#### B. AI Translation Service (`app/services/ai_service.py`)
- Thiết kế dạng Provider Adapter pattern thống nhất một giao diện gọi `translate_text(text, target_lang, prompt_style, api_config)`:
  - **OpenAI / DeepSeek / Custom**: Sử dụng `httpx.AsyncClient` gọi endpoint `v1/chat/completions`.
  - **Google Gemini**: Gọi REST API Gemini `generateContent`.
  - **Anthropic Claude**: Gọi REST API Claude `v1/messages`.
- Tối ưu hóa Prompt dịch chuyên dụng cho tài liệu:
  - Bảo toàn thuật ngữ chuyên ngành, định dạng markdown, công thức toán học/code block nếu có.
  - Trả về bản dịch chuẩn xác, không thêm câu giao tiếp thừa (như "Here is the translation:").

#### C. Document Generation Service (`app/services/doc_service.py`)
- **Word (.docx)**: Dùng `python-docx` để tạo tài liệu đẹp mắt, có phân trang, giữ phân đoạn tương ứng từng trang gốc.
- **PDF Export**: Sử dụng `ReportLab` kết hợp font Unicode (DejaVuSans / Roboto) để xuất PDF không bị lỗi font tiếng Việt, trình bày bố cục sạch sẽ theo từng trang tương ứng.

#### D. Translation Orchestrator & SSE (`app/api/endpoints.py`)
- Khi client gửi yêu cầu dịch (chứa `file_id`, dải trang, target_lang, api_config):
- Endpoint trả về stream SSE (`text/event-stream`):
  - Sự kiện `page_start`: `{ "page": 1, "total": 10 }`
  - Sự kiện `page_translated`: `{ "page": 1, "original_text": "...", "translated_text": "...", "preview_img": "data:image/png;base64,..." }`
  - Sự kiện `page_error`: `{ "page": 1, "error": "..." }`
  - Sự kiện `completed`: `{ "job_id": "...", "download_docx_url": "...", "download_pdf_url": "..." }`

---

## 4. Cấu trúc Thư mục Dự án

```
dich-pdf/
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI entry point & static mounts
│   ├── config.py              # Application settings
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py          # API endpoints (upload, translate SSE, download, test-api)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pdf_service.py     # PDF extraction, parsing & page image rendering
│   │   ├── ai_service.py      # Multi-provider AI translation adapters (OpenAI, Gemini, DeepSeek, Claude)
│   │   └── doc_service.py     # Export generators (DOCX & translated PDF)
│   ├── static/
│   │   ├── css/
│   │   │   └── styles.css
│   │   └── js/
│   │       ├── api.js         # API helpers & LocalStorage key management
│   │       └── app.js         # UI logic, SSE handler, Side-by-side rendering
│   └── templates/
│       └── index.html         # Modern SPA Web UI
├── storage/                   # Temporary upload & exported files directory
│   ├── uploads/
│   └── exports/
├── requirements.txt           # Python dependencies
├── run.py                     # Convenience starter script
└── README.md
```

---

## 5. Kế hoạch Kiểm thử & Xác thực (Verification)
1. **Kiểm tra trích xuất PDF**: Test với file PDF mẫu tiếng Anh nhiều trang, xác nhận bóc tách text và render preview ảnh chính xác.
2. **Kiểm tra Adapter AI**: Test kết nối API key với các provider (DeepSeek, OpenAI, Gemini, Claude) và xử lý lỗi rate limit / timeout.
3. **Kiểm tra luồng SSE**: Xác nhận luồng stream trả dữ liệu từng trang lên giao diện thời gian thực mượt mà.
4. **Kiểm tra chất lượng xuất file**: Tạo thử file `.docx` và `.pdf`, kiểm tra hiển thị tiếng Việt có dấu chuẩn, không bị vỡ font hay mất nội dung.
