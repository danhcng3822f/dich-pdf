# 🌐 AI PDF Translator - Dịch Tài Liệu PDF Chuyên Nghiệp

Ứng dụng web hiện đại giúp bạn dịch tài liệu PDF đa ngôn ngữ dễ dàng và nhanh chóng bằng API Key AI của chính bạn (**OpenAI, DeepSeek, Google Gemini, Anthropic Claude, hoặc Custom OpenAI-Compatible API**).

---

## ✨ Tính Năng Nổi Bật

- 🔑 **Bảo mật tuyệt đối**: API Key được lưu trực tiếp trên trình duyệt của bạn (`LocalStorage`), không lưu trữ vĩnh viễn trên server.
- ⚡ **Xem song ngữ trực tiếp**: Giao diện chia đôi màn hình (Side-by-Side) hiển thị ảnh trang PDF gốc đối chiếu với bản dịch AI.
- 📡 **Tiến trình thời gian thực (SSE)**: Cập nhật bản dịch và tiến trình từng trang ngay lập tức mà không cần chờ toàn bộ file.
- 🎯 **Tùy chọn linh hoạt**:
  - Dịch toàn bộ hoặc chọn phạm vi trang (VD: `1-5, 8, 10-12`).
  - Hỗ trợ nhiều ngôn ngữ đích: Tiếng Việt, Tiếng Anh, Tiếng Nhật, Tiếng Trung, Tiếng Hàn, Tiếng Pháp, Tiếng Đức, v.v.
  - Tùy chỉnh văn phong dịch thuật: Chuẩn xác, Học thuật, Kinh doanh, Kỹ thuật (CNTT), Thân mật.
  - Thêm prompt chỉ định dịch theo nhu cầu riêng.
- 📥 **Xuất đa định dạng**: Tải về kết quả dưới dạng **Word (.docx)**, **PDF dịch**, hoặc **Markdown (.md)** chỉ với một click.

---

## 🛠️ Cài Đặt & Khởi Chạy

### 1. Yêu Cầu Môi Trường
- **Python 3.10+** (Khuyên dùng Python 3.11)

### 2. Cài Đặt Thư Viện
Mở terminal / PowerShell tại thư mục dự án và chạy:

```bash
pip install -r requirements.txt
```

### 3. Khởi Chạy Website
Chạy lệnh:

```bash
python run.py
```

Mở trình duyệt và truy cập:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📖 Hướng Dẫn Sử Dụng

1. **Cấu hình API Key**:
   - Nhấn vào nút **"Cài đặt API Key"** ở góc trên bên phải màn hình.
   - Chọn nhà cung cấp:
     - **OpenAI**: Nhập `sk-...` (mặc định model `gpt-4o-mini` hoặc `gpt-4o`).
     - **DeepSeek**: Nhập API key của DeepSeek (mặc định model `deepseek-chat`).
     - **Google Gemini**: Nhập Google AI Studio Key (mặc định `gemini-1.5-flash`).
     - **Anthropic Claude**: Nhập Claude Key (mặc định `claude-3-5-sonnet-20241022`).
     - **Custom**: Dành cho các dịch vụ proxy hoặc Local LLM (Ollama, vLLM, LMStudio...).
   - Nhấn **"Kiểm tra kết nối"** để kiểm tra API, sau đó nhấn **"Lưu cấu hình"**.

2. **Tải lên & Dịch**:
   - Kéo & thả file PDF vào ô tải lên.
   - Chọn ngôn ngữ đích, phong cách dịch và dải trang (nếu muốn).
   - Nhấn **"Bắt đầu Dịch Ngay"**.
   - Theo dõi bản dịch hiển thị song ngữ trực tiếp từng trang theo thời gian thực.

3. **Tải Kết Quả**:
   - Sau khi hoàn tất, bạn có thể tải về file **Word (.docx)**, **PDF**, hoặc **Markdown**.

---

## 🧪 Chạy Kiểm Thử (Tests)

Để chạy toàn bộ bài kiểm tra tự động:

```bash
pytest -v
```
