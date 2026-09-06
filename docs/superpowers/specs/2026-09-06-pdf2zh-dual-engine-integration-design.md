# Tích hợp PDFMathTranslate (pdf2zh) & Chế độ Kép (Dual-Engine) cho AI PDF Translator

**Ngày**: 2026-09-06  
**Trạng thái**: Đã phê duyệt  
**Mục tiêu**: Tích hợp toàn diện động cơ dịch tài liệu giữ nguyên layout của PDFMathTranslate (pdf2zh) vào AI PDF Translator, tạo thành hệ thống Dual-Engine: Giữ nguyên Layout gốc (PDF2ZH) và Tái cấu trúc Slide (LaTeX Beamer), phục vụ trên giao diện web FastAPI hiện đại với kiểm thử Playwright.

---

## 1. Bối cảnh & Mục tiêu

Dự án `dich-pdf` hiện tại có giao diện web hiện đại (FastAPI + Tailwind CSS + KaTeX), dịch tài liệu học thuật/slide theo từng trang và tái cấu trúc sang định dạng LaTeX Beamer. Tuy nhiên, với các tài liệu nghiên cứu, sách hoặc bài báo khoa học có cấu trúc trang phức tạp (nhiều cột, hình ảnh, bảng biểu, công thức toán vector dày đặc), người dùng cần một bản dịch **giữ nguyên 100% layout gốc, vị trí hình vẽ và công thức toán học**, không làm biến dạng bố cục tài liệu ban đầu.

Mã nguồn mở `PDFMathTranslate` (`pdf2zh`) giải quyết xuất sắc bài toán này bằng cách can thiệp vào luồng đối tượng PDF nhị phân (PDF Stream Operators), nhận diện layout qua model ONNX, đóng băng công thức toán dưới dạng token `{v0}`, `{v1}`, và vá trực tiếp văn bản dịch bằng font Unicode vào file PDF gốc.

### Mục tiêu cốt lõi:
1. **Kiến trúc Dual-Engine linh hoạt:**
   - **Engine 1 (PDF2ZH Layout Engine):** Dịch đè trực tiếp lên PDF, giữ nguyên từng pixel, vị trí ảnh, bảng, công thức toán vector; hỗ trợ xuất **PDF Dịch Đơn Ngữ (Mono)** và **PDF Dịch Song Ngữ Xen Kẽ (Dual)**.
   - **Engine 2 (Beamer Slide Engine):** Tái cấu trúc slide thành mã nguồn LaTeX Beamer để trình chiếu và compile qua Tectonic.
2. **Bộ chuyển đổi Adapter thống nhất (Unified Translator Adapter):**
   - Hỗ trợ các AI Provider qua BYOK API Key (`OpenAI`, `DeepSeek`, `Gemini`, `Claude`, `Custom API`) được lưu trên trình duyệt `LocalStorage`.
   - Bổ sung các dịch vụ dịch tự động miễn phí (`Google Free Web`, `Bing Free Web`) từ `pdf2zh` để người dùng có thể dịch nhanh mà không bắt buộc phải có API Key trả phí.
3. **Tiến trình thời gian thực (Realtime SSE) & Đối chiếu song ngữ trực quan:**
   - Khi dịch ở chế độ PDF2ZH: Mỗi trang dịch xong sẽ được render ngay lập tức thành ảnh trang PDF mới để hiển thị đối chiếu song ngữ (cột trái: trang gốc; cột phải: trang PDF dịch thật).
4. **Kiểm thử tự động toàn diện:**
   - Unit tests và Integration tests cho Adapter và Pipeline.
   - Kiểm thử E2E giao diện web tự động bằng Playwright.

---

## 2. Kiến trúc Hệ thống & Cấu trúc Thư mục

Tất cả logic cốt lõi của PDFMathTranslate được tinh giản, gỡ bỏ phụ thuộc rườm rà và đóng gói vào `app/services/pdf2zh_engine/`:

```text
dich-pdf/
├── requirements.txt                       # fastapi, pymupdf, pdfminer.six, onnxruntime, opencv-python-headless, babeldoc...
├── run.py                                 # Điểm khởi chạy server Uvicorn (port 8000)
├── app/
│   ├── config.py                          # Đường dẫn storage, font cache, model cache, max_upload_size
│   ├── main.py                            # Khởi tạo FastAPI app, mount static & templates
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py                      # REST endpoints: /upload, /test-connection, /translate/stream, /download
│   │   └── schemas.py                     # Pydantic schemas cho request/response
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pdf_service.py                 # PyMuPDF utils: metadata, extract blocks, render image preview
│   │   ├── ai_service.py                  # Dịch vụ gọi LLM cho chế độ Beamer & kiểm tra kết nối API
│   │   ├── doc_service.py                 # Xuất Docx, ReportLab PDF, Markdown, LaTeX
│   │   │
│   │   └── pdf2zh_engine/                 # 🌟 Lõi PDFMathTranslate tích hợp
│   │       ├── __init__.py
│   │       ├── converter.py               # TranslateConverter (gom công thức {v*}, tính toán tọa độ, sinh opcodes)
│   │       ├── pdfinterp.py               # PDFPageInterpreterEx (chặn và sửa luồng toán tử vẽ PDF)
│   │       ├── doclayout.py               # DocLayoutModel (nhận diện layout tài liệu bằng DocLayout-YOLO qua ONNX)
│   │       ├── font_manager.py            # Tải và quản lý font Unicode (Noto, SourceHanSerif)
│   │       ├── adapter.py                 # Cầu nối gắn AIConfig & Google/Bing vào Translator của pdf2zh
│   │       └── pipeline.py                # Điều phối dịch theo trang, stream SSE và tạo Mono/Dual PDF
│   │
│   ├── templates/
│   │   └── index.html                     # Giao diện web: Tab chọn Engine, Preview đối chiếu, các nút tải về
│   │
│   └── static/
│       ├── css/styles.css
│       └── js/
│           ├── api.js                     # Quản lý cấu hình API Key & gọi backend API
│           ├── beamer_renderer.js         # Renderer mô phỏng Beamer theme Madrid
│           └── app.js                     # Điều khiển SSE stream, render ảnh đối chiếu kép
│
├── storage/
│   ├── uploads/                           # File PDF tải lên
│   ├── exports/                           # File xuất (mono.pdf, dual.pdf, docx, md, tex)
│   ├── previews/                          # Ảnh preview từng trang (gốc & dịch)
│   ├── fonts/                             # Font Noto / SourceHan được tải về
│   └── models/                            # Model ONNX DocLayout-YOLO
│
└── tests/
    ├── test_pdf_service.py
    ├── test_ai_service.py
    ├── test_doc_service.py
    ├── test_api_routes.py
    ├── test_frontend_assets.py
    ├── test_pdf2zh_adapter.py             # Test adapter bọc token {v*}
    ├── test_pdf2zh_pipeline.py            # Test xuất Mono PDF & Dual PDF
    └── e2e/
        └── test_web_playwright.py         # Test giao diện bằng Playwright
```

---

## 3. Thiết kế Kỹ thuật Chi tiết của PDF2ZH Engine

### 3.1 Phân tích Layout & Đóng băng Công thức Toán
1. **Nhận diện vùng đối tượng (DocLayout-YOLO):**
   - Chạy mô hình ONNX dự đoán các hộp giới hạn (Bounding Boxes): `figure` (hình vẽ), `table` (bảng biểu), `isolate_formula` (công thức đứng riêng), `formula_caption` (chú thích công thức), `abandon` (vùng bỏ qua).
   - Đánh dấu vùng bảo vệ (`cls == 0`) trên ma trận tọa độ để không bao giờ ghi đè hoặc dịch nhầm lên các vùng này.
2. **Quét ký tự & Font Toán học (`vflag`):**
   - Quét từng ký tự trong văn bản qua tên font (`CM`, `TeX-`, `Sym`, `Math`, `Code`...) và dải mã Unicode ký hiệu toán học / ký tự Hy Lạp.
   - Gom các ký tự công thức liền kề thành nhóm và gán mã token duy nhất: `{v0}`, `{v1}`, `{v2}`...
   - Lưu trữ đầy đủ thuộc tính vector gốc của từng công thức: font ID, size, tọa độ (`x0`, `y0`, `x1`, `y1`), đường kẻ phân số (`LTLine`), độ lệch trục (`vfix`).

### 3.2 Lớp Cầu Nối Thống Nhất (`adapter.py`)
* `TranslateConverter` gửi chuỗi văn bản chứa token `{v*}` tới `PDF2ZHAdapter.translate(text)`.
* **Xử lý với AI LLM (OpenAI, DeepSeek, Gemini, Claude, Custom):**
  - Hệ thống tự động thêm chỉ dẫn bảo toàn token vào System Prompt:
    ```text
    CRITICAL CONSTRAINT: The text contains preserved formula markers like {v0}, {v1}, {v2}...
    You MUST preserve all {v\d+} markers EXACTLY as they are in the translation.
    Do NOT translate, alter, remove, or reorder the {v\d+} tokens.
    ```
* **Xử lý với Dịch Tự Động Miễn Phí (Google / Bing):**
  - Sử dụng cơ chế gửi HTTP request dịch web của `pdf2zh`, tự động xử lý và bảo toàn các token `{v*}`.

### 3.3 Dàn Trang & Vá Trực Tiếp Luồng Vẽ PDF (`converter.py` & `pdfinterp.py`)
1. Nhúng font Unicode mở rộng (`Noto`, `SourceHanSerif`) vào từ điển font tài nguyên của PDF (`/Resources /Font`).
2. Với mỗi đoạn văn bản dịch trả về:
   - Tách chuỗi theo các token `{v(\d+)}`.
   - Đối với chữ dịch: tính toán chiều rộng ký tự theo font Unicode mới và chia dòng theo đúng lề (`x0`, `x1`).
   - Đối với công thức `{v*}`: chèn lại các glyph vector và nét kẻ nguyên bản với độ lệch trục `vfix` chính xác.
   - Tạo ra chuỗi mã toán tử PDF mới (`/font size Tf 1 0 0 1 x y Tm [<hex>] TJ`).
3. Cập nhật luồng lệnh mới trực tiếp vào bảng xref của PDF trang tương ứng (`doc_zh.update_stream(page_xref, new_stream)`).

### 3.4 Tạo Sản Phẩm Xuất (Mono & Dual PDF)
- **Mono PDF (`{job_id}_mono.pdf`):** Tài liệu chứa toàn bộ các trang đã được dịch và vá nội dung.
- **Dual PDF (`{job_id}_dual.pdf`):** Tự động ghép tài liệu gốc và tài liệu dịch theo dạng xen kẽ:
  - Trang 1: Trang gốc 1
  - Trang 2: Trang dịch 1
  - Trang 3: Trang gốc 2
  - Trang 4: Trang dịch 2
  - ...

---

## 4. Giao Diện Người Dùng & Luồng Tương Tác

### 4.1 Bộ chọn Chế độ Dịch (Engine Mode)
Tại bước 2 trên màn hình chính:
- **Tab 1: 📑 Giữ nguyên Layout gốc (PDF2ZH Engine)** *(Mặc định)*
  - Phù hợp: Sách, tài liệu khoa học, bài báo, tài liệu nhiều hình ảnh và bảng biểu.
  - Đầu ra: PDF Dịch nguyên bản (Mono), PDF Song ngữ xen kẽ (Dual), Word, Markdown.
- **Tab 2: 📊 Tái cấu trúc Slide (LaTeX Beamer)**
  - Phù hợp: Slide bài giảng, slide thuyết trình.
  - Đầu ra: PDF Beamer slide, mã nguồn `.tex`, Word, Markdown.

### 4.2 Danh sách Nhà cung cấp Dịch vụ (Provider)
Trong modal "Cài đặt API Key":
- **OpenAI** (ChatGPT)
- **DeepSeek AI**
- **Google Gemini**
- **Anthropic Claude**
- **Custom (OpenAI-Compatible / Ollama / Local LLM)**
- **Google Dịch (Miễn phí)** *(Không cần API Key)*
- **Bing Dịch (Miễn phí)** *(Không cần API Key)*

### 4.3 Khung Xem Trước Đối Chiếu Song Ngữ (Side-by-Side)
- Cột trái: Ảnh trang PDF gốc.
- Cột phải:
  - Nếu ở chế độ **PDF2ZH**: Hiển thị ảnh trang PDF đã được vá chữ dịch (chụp trực tiếp từ trang PDF vừa compile xong).
  - Nếu ở chế độ **Beamer**: Hiển thị bản dịch Beamer slide render qua HTML + KaTeX.

---

## 5. Kế Hoạch Kiểm Thử Toàn Diện (Testing)

### 5.1 Unit & Integration Tests (Pytest)
1. `test_pdf2zh_adapter.py`:
   - Xác thực Adapter bảo toàn đầy đủ các token `{v0}`, `{v1}` qua bộ giả lập phản hồi.
   - Xác thực bộ provider miễn phí Google/Bing hoạt động bình thường.
2. `test_pdf2zh_pipeline.py`:
   - Chạy thử nghiệm trên 1 file PDF test nhỏ (1-2 trang).
   - Kiểm tra việc sinh ra file Mono PDF và Dual PDF với số trang chuẩn xác.

### 5.2 Kiểm Thử E2E Giao Diện Trực Quan (Playwright)
File `tests/e2e/test_web_playwright.py`:
1. Mở trang chủ ứng dụng tại `http://127.0.0.1:8000`.
2. Kiểm tra hiển thị đầy đủ Tab Engine Mode, cấu hình Provider (có cả Google/Bing miễn phí).
3. Thao tác tải lên file PDF thử nghiệm.
4. Chọn chế độ PDF2ZH và bấm "Bắt đầu Dịch Ngay".
5. Theo dõi tiến trình SSE stream: kiểm tra thanh tiến trình chạy từ 0% đến 100%.
6. Kiểm tra hai cột đối chiếu hiển thị ảnh trang gốc và ảnh trang dịch.
7. Kiểm tra các nút tải về (PDF Mono, PDF Dual) sáng lên và click tải thành công file với HTTP 200.

---

## 6. Tiêu Chí Nghiệm Thu (Acceptance Criteria)
1. Người dùng có thể dịch tài liệu PDF ở cả 2 chế độ: Giữ layout gốc (PDF2ZH) và Beamer Slide.
2. Ở chế độ PDF2ZH, tải được cả file **PDF dịch đơn ngữ (Mono)** và **PDF song ngữ xen kẽ (Dual)** với layout và công thức toán học được bảo toàn 100%.
3. Hỗ trợ dịch bằng cả API Key cá nhân lẫn Google/Bing miễn phí.
4. Toàn bộ 12 test cũ và các test mới đều PASS 100%.
5. Test E2E bằng Playwright hoàn thành suôn sẻ trên trình duyệt.
