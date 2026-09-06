# 🌐 AI PDF Translator - Dịch Tài Liệu PDF Chuyên Nghiệp (Dual-Engine)

Ứng dụng web hiện đại giúp bạn dịch tài liệu PDF và slide thuyết trình đa ngôn ngữ dễ dàng, nhanh chóng với **cơ chế kép (Dual-Engine)** linh hoạt, tích hợp sức mạnh từ động cơ giữ nguyên layout của **PDFMathTranslate (`pdf2zh`)** cùng các AI LLM hàng đầu (**OpenAI, DeepSeek, Google Gemini, Anthropic Claude, Custom API**) và dịch tự động miễn phí (**Google Translate, Bing Translate**).

---

## ✨ Tính Năng Nổi Bật

- 🚀 **Hệ Thống Dịch Kép (Dual-Engine)**:
  - 📑 **Chế độ 1: Giữ nguyên Layout gốc (PDF2ZH Engine)**: Dành cho sách, bài báo khoa học, luận văn, tài liệu nghiên cứu. Dùng AI Computer Vision (`DocLayout-YOLO`) nhận diện ảnh, bảng biểu và đóng băng công thức toán học (`{v*}`), vá luồng lệnh vẽ PDF trực tiếp để giữ nguyên 100% layout gốc chuẩn pixel.
  - 📊 **Chế độ 2: Tái cấu trúc Slide (LaTeX Beamer)**: Dành cho slide bài giảng, thuyết trình. Tái cấu trúc nội dung sang cú pháp LaTeX Beamer (khung block, alertblock, cột, công thức toán KaTeX).
- 🌐 **Hỗ Trợ Cả Dịch Miễn Phí & Dịch Bằng AI**:
  - Dùng **Google Dịch** hoặc **Bing Dịch** hoàn toàn miễn phí mà **không cần API Key**.
  - Dùng API Key AI của chính bạn: **OpenAI**, **DeepSeek**, **Google Gemini**, **Anthropic Claude**, hoặc **Custom OpenAI-Compatible API** (Ollama, vLLM, LMStudio...).
- 🔑 **Bảo Mật Tuyệt Đối (BYOK)**: API Key được lưu trực tiếp trên trình duyệt của bạn (`LocalStorage`), không lưu trữ vĩnh viễn trên server.
- ⚡ **Xem Đối Chiếu Song Ngữ Trực Tiếp**: Giao diện chia đôi màn hình (Side-by-Side) hiển thị ảnh trang PDF gốc đối chiếu với ảnh trang PDF dịch thật (ở chế độ PDF2ZH) hoặc bản xem trước Beamer slide (ở chế độ LaTeX).
- 📡 **Tiến Trình Thời Gian Thực (SSE)**: Cập nhật bản dịch và tiến trình từng trang ngay lập tức mà không cần chờ toàn bộ file.
- 📥 **Xuất Đa Định Dạng & PDF Song Ngữ**:
  - **PDF Dịch Đơn Ngữ (Mono PDF)**: Giữ nguyên bố cục gốc chuẩn pixel.
  - **PDF Song Ngữ Xen Kẽ (Dual PDF)**: Tự động ghép 1 trang gốc xen kẽ 1 trang dịch.
  - **Word (.docx)**, **Markdown (.md)**, và **LaTeX (.tex)**.

---

## 🛠️ Cài Đặt & Khởi Chạy

### 1. Yêu Cầu Môi Trường
- **Python 3.10+** (Khuyên dùng Python 3.11)

### 2. Cài Đặt Thư Viện
Mở terminal / PowerShell tại thư mục dự án và chạy:

```bash
pip install -r requirements.txt
```

Nếu muốn chạy kiểm thử tự động trình duyệt bằng Playwright, cài thêm browser driver:
```bash
playwright install chromium
```

### 3. Khởi Chạy Ứng Dụng
Chạy lệnh:

```bash
python run.py
```

Mở trình duyệt và truy cập:  
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📖 Hướng Dẫn Sử Dụng

1. **Cấu Hình Dịch Vụ**:
   - Nhấn vào nút **"Cài đặt API Key"** ở góc trên bên phải màn hình.
   - Chọn nhà cung cấp:
     - **Google Dịch (Miễn phí)** hoặc **Bing Dịch (Miễn phí)**: Không cần nhập API Key, có thể dùng ngay lập tức!
     - **OpenAI / DeepSeek / Gemini / Claude / Custom**: Nhập API key tương ứng.
   - Nhấn **"Kiểm tra kết nối"** rồi nhấn **"Lưu cấu hình"**.

2. **Tải Lên & Chọn Chế Độ Dịch**:
   - Kéo & thả file PDF vào ô tải lên.
   - Chọn chế độ dịch:
     - **📑 Giữ nguyên Layout (PDF2ZH)**: Nếu muốn xuất ra bản PDF dịch đè giữ nguyên tranh ảnh, bảng biểu và công thức toán.
     - **📊 Slide thuyết trình (LaTeX Beamer)**: Nếu dịch slide bài giảng muốn xem khung slide Beamer.
   - Chọn ngôn ngữ đích (Tiếng Việt, Tiếng Anh, Tiếng Trung, Tiếng Nhật...) và phạm vi trang (VD: `all` hoặc `1-3, 5`).
   - Nhấn **"Bắt đầu Dịch Ngay"**.

3. **Theo Dõi & Tải Kết Quả**:
   - Xem kết quả đối chiếu song ngữ hiển thị trực tiếp theo từng trang.
   - Sau khi hoàn tất, tải về:
     - **PDF Dịch (Mono)**
     - **PDF Song ngữ (Dual)**
     - **Word (.docx)**, **Markdown (.md)** hoặc **LaTeX (.tex)**.

---

## 🧪 Chạy Kiểm Thử (Tests)

Để chạy toàn bộ bài kiểm tra tự động (58 unit, integration và Playwright E2E tests):

```bash
pytest -v
```

Để chỉ chạy riêng bài kiểm thử E2E giao diện người dùng bằng Playwright:

```bash
pytest tests/e2e/test_web_playwright.py -v
```
