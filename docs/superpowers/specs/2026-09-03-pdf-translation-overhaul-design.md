# Đại tu AI PDF Translator: Beamer slide → PDF compile sẵn

**Ngày**: 2026-09-03
**Trạng thái**: Chờ review
**Thay thế**: `2026-09-02-pdf-translation-design.md` (bản gốc, vẫn giữ để đối chiếu)
**Stack**: Python 3.11, FastAPI, PyMuPDF, Tectonic (XeTeX), vanilla JS

---

## 1. Bối cảnh & mục tiêu

Bản hiện tại chạy được nhưng không dùng nổi cho đúng việc nó nhắm tới. Đã chốt với người dùng:

- **Môi trường**: localhost, một người dùng, không deploy ra ngoài.
- **Tài liệu**: slide bài giảng / thuyết trình, nhiều công thức toán, block định nghĩa–định lý, đôi khi chia cột.
- **Đầu ra cần**: **PDF slide đã compile sẵn**, tải về là dùng được. Không phải file `.tex` để tự compile.

Ba mục tiêu, theo thứ tự ưu tiên:

1. **PDF slide compile được thật.** Đây là thứ hiện chưa tồn tại. Nút "PDF dịch" hôm nay xuất ra một file chứa LaTeX source dạng chữ.
2. **Không đốt tiền API hai lần.** Vòng validate → repair → compile có thể phải chạy lại nhiều lượt; bản dịch phải được cache để lượt sau miễn phí.
3. **Nhanh và không mất trang.** Dịch song song, retry lỗi tạm thời, và trang lỗi không được âm thầm biến mất khỏi kết quả.

### Không thuộc phạm vi (cố ý)

Docker, CI/CD, TypeScript/Vite, OCR cho PDF scan, đa người dùng, xác thực đăng nhập. Với một tool cá nhân chạy bằng `python run.py`, những thứ này là nghi thức chứ không sửa được triệu chứng nào. Đã xác nhận với người dùng.

---

## 2. Ràng buộc đã xác thực bằng thực nghiệm

Đã cài Tectonic 0.17.0 (`%LOCALAPPDATA%\Tectonic\tectonic.exe`) và compile thử 5 biến thể preamble với slide chứa 28 ký tự tiếng Việt dấu phức hợp, công thức toán, block và cột. Kết quả đo được, không phải suy đoán:

| Kết luận | Bằng chứng |
|---|---|
| Preamble chốt: `fontspec` + `babel[vietnamese]`, **không khai báo font** | Latin Modern Sans nhúng dạng `Identity-H`, đủ 28 ký tự dấu, PDF 35 KB — nhỏ nhất trong 3 biến thể pass |
| Không phụ thuộc font hệ thống | Tectonic cảnh báo `accessing absolute path C:/Windows/fonts... build may not be reproducible`. Biến thể Arial/Segoe UI chạy được nhưng buộc output vào Windows |
| `DejaVu Sans` không dùng được trên máy này | 2 biến thể dùng nó fail với `Package fontspec Error: The font "DejaVu Sans" cannot be found` |
| babel xuất ngày tiếng Việt đúng | PDF chứa "Ngày 3 tháng 9 năm 2026" |
| **Cảnh báo `Missing character ... in font nullfont` là vô hại** | 3 cảnh báo là chữ số ASCII `6`,`1`,`3` sinh từ lượt beamer ghi file điều hướng (`No file test.nav.`), không phải ký tự Việt |
| **Không được trích text từ PDF đã compile để sinh .docx/.md** | XeTeX phát kern thay vì glyph space sau ký tự có dấu dưới → trình trích dán chữ: `xử lý`→`xửlý`, `phố Hồ Chí`→`phốHồChí`, `trị riêng`→`trịriêng` |
| Compile LaTeX do LLM sinh phải dùng `--untrusted` | Flag có sẵn trong `tectonic -X compile`, tắt `\write18` và các tính năng shell escape |

Hai ràng buộc in đậm là **yêu cầu cứng** với `compiler.py` và `export_service.py`: bộ phân tích log phải bỏ qua `nullfont`, nếu không mọi lần compile đều bị coi là lỗi; và đường sinh .docx/.md phải đi từ LaTeX source, không đi qua PDF.

**Chưa xác thực**: chưa có ai nhìn bằng mắt bản PDF (model đang dùng không nhận input ảnh). Bằng chứng gián tiếp đủ chắc — glyph nhúng đúng, codepoint trích ra đúng, không cảnh báo missing character nào cho ký tự Việt — nhưng bước đầu tiên khi implement vẫn là để người dùng xác nhận bằng mắt.

---

## 3. Kiến trúc module

Giữ FastAPI + vanilla JS, không thêm build step. Tách theo trách nhiệm; mỗi module trả lời được "nó làm gì, dùng thế nào, phụ thuộc gì".

```
app/
├── main.py                    # app factory, middleware, khởi tạo logging
├── config.py                  # pydantic-settings + .env; BỎ mkdir lúc import
├── logging_setup.py           # cấu hình log + redact secret
├── jobs.py                    # job manifest JSON trên đĩa + dọn theo TTL
├── api/
│   ├── routes.py              # chỉ điều phối, không chứa business logic
│   └── schemas.py             # request/response models; provider là Enum
└── services/
    ├── pdf_service.py         # mở PDF một lần; text theo block; render preview
    ├── translate/
    │   ├── providers.py       # adapter OpenAI-compatible / Gemini / Claude
    │   ├── engine.py          # semaphore + retry/backoff + tra cache
    │   ├── chunker.py         # cắt theo token ở ranh giới frame/block
    │   ├── prompts.py         # dựng system prompt theo style
    │   └── cache.py           # cache địa chỉ theo nội dung
    ├── latex/
    │   ├── sanitizer.py       # gỡ fence, gỡ preamble AI tự thêm, chặn lệnh cấm
    │   ├── validator.py       # cân bằng env/ngoặc/math bằng stack; phát hiện cắt cụt
    │   ├── assembler.py       # preamble đã xác thực + ghép frame + marker
    │   └── compiler.py        # gọi tectonic, phân tích log, định vị frame lỗi
    └── export_service.py      # .docx/.md sinh từ plain text đã gỡ LaTeX
```

Không tách thành 30 file kiểu enterprise. 12 module là đủ để mỗi file vừa một lần đọc.

---

## 4. Luồng end-to-end

```
upload PDF
   → pdf_service: mở MỘT lần, lấy text theo block cho các trang được chọn
   → engine: song song N trang (semaphore), mỗi trang:
        cache hit? → trả ngay, không gọi API
        cache miss → chunker (nếu quá dài) → provider → retry nếu lỗi tạm → ghi cache
        ↳ phát SSE page_completed ngay khi có (client chèn theo số trang)
   → sanitizer: gỡ ```latex fence, gỡ \documentclass AI tự thêm, chặn \write18/\input
   → validator: cân bằng \begin/\end, ngoặc, $; phát hiện cắt cụt
        fail → repair cơ học → vẫn fail → nhờ AI sửa một lượt
   → assembler: preamble đã xác thực + các frame + \typeout marker mỗi frame
   → compiler: tectonic --untrusted, timeout
        fail → đọc marker cuối trong log để định vị frame lỗi
             → thay frame đó bằng bản text thuần trong frame[fragile] → compile lại
   → artifacts: .pdf (chính), .tex, .docx, .md
```

Điểm khác biệt cốt lõi so với bản cũ: bản cũ là một vòng `for` tuần tự vừa trích vừa dịch vừa xuất file trong lòng một generator SSE. Bản mới tách thành các stage có thể test riêng, và stage dịch có cache nên các stage sau chạy lại được mà không tốn tiền.

---

## 5. Chi tiết các thành phần

### 5.1 Trích xuất PDF

- Mở document **một lần** cho cả job, không mở lại từng trang (hiện tại `extract_page_content` mở/đóng file cho mỗi trang).
- Lấy text theo **block** (`page.get_text("blocks")`) rồi sắp theo thứ tự đọc, thay cho `get_text("text")` — slide chia cột hiện đang bị trộn lẫn hai cột thành một dòng.
- Ảnh preview **không nhúng base64 vào SSE**. Ghi ra `storage/previews/<job_id>/<page>.png`, SSE trả `image_url`. Lý do: base64 làm phình stream 33%, và bản cũ giữ toàn bộ ảnh trong `JOB_STORE` vĩnh viễn.

  Dùng **PNG, không dùng WebP**: đã kiểm tra trên PyMuPDF 1.28.2, `Pixmap.tobytes` chỉ nhận `('png','pnm','pgm','ppm','pbm','pam','tga','tpic','psd','ps','jpg','jpeg')` — WebP raise `ValueError`. Với slide chữ, PNG còn nhỏ hơn JPEG (5,0 KB so với 9,6 KB ở cùng mức zoom).
- `parse_page_ranges` với input không hợp lệ phải **raise lỗi**, không được trả về toàn bộ trang. Hành vi hiện tại khiến gõ nhầm `999` trên file 40 trang thành dịch cả 40 trang.

### 5.2 Translation engine

**Song song**: `asyncio.Semaphore(N)`, `N` cấu hình được, mặc định 4. `asyncio.gather` giữ tương ứng trang.

**Hệ quả bắt buộc xử lý**: kết quả về không theo thứ tự, nên frontend phải **chèn card theo `page_number`** thay vì `appendChild`.

**Retry**: backoff luỹ thừa + jitter, tôn trọng header `Retry-After`.

| Tình huống | Xử lý |
|---|---|
| 429, 500, 502, 503, 504, timeout, lỗi kết nối | retry, tối đa `max_retries` (mặc định 3) |
| 400, 401, 403, 404 | **không retry** — lỗi cấu hình, retry chỉ tốn thời gian |
| Hết lượt retry | phát `page_error` với `retriable`, trang vẫn có chỗ trong kết quả |

**`max_tokens`**: thành setting cấu hình được (mặc định 8192), không hardcode 4096. Giá trị hiện tại đang cắt cụt bản dịch của slide dày ngay giữa `\begin{frame}` khiến .tex không compile nổi.

**Model mặc định**: chuyển hết ra `.env.example` kèm chú thích, **không hardcode trong Python**. Tên model thay đổi theo thời gian; đây phải là chỗ sửa config, không phải sửa code. Trường model trên UI là bắt buộc, có gợi ý.

**Cache** (`cache.py`): khoá là `sha256` của JSON chuẩn hoá gồm `text`, `target_lang`, `style`, `provider`, `model`, `custom_prompt`, `temperature`, và `prompt_version`. Lưu tại `storage/cache/<khoá>.json`.

`prompt_version` là bắt buộc: khi sửa system prompt, cache cũ phải tự thành vô hiệu.

**Chunker**: trang vượt ngưỡng token (mặc định 3000 token đầu vào, cấu hình được) thì cắt ở ranh giới block/đoạn — **không bao giờ cắt giữa một môi trường LaTeX** — dịch từng phần rồi ghép. Slide thường không chạm ngưỡng này; nó tồn tại cho slide dày chữ.

**Ước lượng trước khi chạy**: trả về số trang và số token dự kiến để người dùng xác nhận trước khi tiêu tiền.

### 5.3 LaTeX pipeline

Đây là phần rủi ro nhất, vì **LaTeX do LLM sinh thường không compile được ngay**.

**`sanitizer.py`** — gỡ ` ```latex ` fence; gỡ `\documentclass`, `\begin{document}`, `\end{document}` nếu AI tự thêm; chuẩn hoá whitespace; **loại bỏ** `\write18`, `\input`, `\include`, `\openout`.

**`validator.py`** — dùng stack, không dùng regex:

- `\begin{X}` push, `\end{X}` pop; lệch → báo lỗi kèm vị trí.
- Cân bằng `{}` ngoài vùng verbatim.
- Số `$` chẵn; `$$`, `\[`/`\]` có cặp.
- Dấu hiệu cắt cụt: kết thúc giữa một command, còn môi trường chưa đóng, ngoặc lệch tại EOF.

**Repair 3 tầng**:

1. **Cơ học** — đóng các môi trường còn mở theo thứ tự ngược, đóng ngoặc, bỏ command dở ở cuối.
2. **Nhờ AI** — một lượt duy nhất, gửi kèm thông báo lỗi của validator hoặc của compiler, yêu cầu chỉ trả code.
3. **Hạ cấp** — thay bằng `\begin{frame}[fragile]{Trang N}` + `\begin{verbatim}` chứa text thuần + ghi chú. **Cả deck vẫn build được.**

**`assembler.py`** — preamble đã xác thực ở mục 2, cộng `\typeout{DSH-FRAME-<n>}` chèn trước mỗi frame để định vị lỗi từ log.

Preamble chốt, dùng đúng nguyên văn (đây là bản đã compile thành công ở mục 2):

```latex
\documentclass[aspectratio=169]{beamer}
\usepackage{fontspec}
\usepackage[vietnamese]{babel}
\usepackage{amsmath,amssymb}
\usetheme{Madrid}
```

Không thêm `\setsansfont`, không thêm `inputenc`, không thêm `polyglossia`. Mọi thay đổi preamble phải chạy lại được bài kiểm tra glyph ở mục 6 trước khi nhận.

**`compiler.py`**:

- Lệnh: `tectonic -X compile --untrusted --outdir <tmp> --keep-logs <file>`, có timeout (mặc định 120s).
- Tìm tectonic theo thứ tự: setting `tectonic_path` → `PATH` → `%LOCALAPPDATA%\Tectonic`.
- **Không có tectonic không phải lỗi chí tử**: vẫn xuất .tex/.docx/.md, báo rõ `pdf_ready: false` kèm hướng dẫn cài.
- Phân tích log: bắt các dòng `!` và `l.<n>`. **Bỏ qua** `Missing character ... nullfont` và `accessing absolute path` — cả hai đều vô hại (mục 2).
- Định vị frame lỗi: lấy marker `DSH-FRAME-<n>` cuối cùng xuất hiện trước lỗi. Nếu không kết luận được thì compile từng frame để khoanh vùng.

### 5.4 Xuất file

| Định dạng | Nguồn | Ghi chú |
|---|---|---|
| `.pdf` | tectonic compile từ .tex | **sản phẩm chính** |
| `.tex` | assembler | để người dùng tự sửa/compile lại |
| `.docx` | LaTeX source → plain text | **không** trích từ PDF (mục 2) |
| `.md` | LaTeX source → plain text | như trên |

Bộ chuyển LaTeX → plain text gỡ command, giữ nội dung, giữ công thức ở dạng `$...$` để đọc được. Hành vi hiện tại — nhồi nguyên LaTeX source vào .docx và .pdf — bị loại bỏ hoàn toàn.

Font tiếng Việt cho `.docx`: python-docx ghi text Unicode, việc chọn font để render là của Word — không cần đăng ký font phía server.

**Bỏ hẳn `reportlab`** khỏi `requirements.txt`. PDF giờ do tectonic sinh, nên toàn bộ nhánh ReportLab và hàm `_get_unicode_font_name` — hàm chỉ tìm font trong `C:/Windows/Fonts`, fallback về `Helvetica` làm mất dấu tiếng Việt, và hỏng hoàn toàn trên Linux — được xoá.

### 5.5 Job store & dọn rác

`jobs.py` giữ manifest JSON mỗi job tại `storage/jobs/<job_id>.json`: trạng thái, danh sách trang, trang lỗi, frame bị hạ cấp, đường dẫn artifact.

Thay cho `JOB_STORE: dict` trong bộ nhớ đang giữ base64 ảnh mọi trang vĩnh viễn.

Dọn theo TTL (mặc định 24h) cho `uploads/`, `exports/`, `previews/`; cache có TTL riêng dài hơn (mặc định 30 ngày). Chạy lúc khởi động và định kỳ.

### 5.6 Hợp đồng API & SSE

```
POST /api/upload            → {file_id, filename, total_pages, file_size}
POST /api/estimate          → {pages, estimated_tokens}
POST /api/test-connection   → {status, sample_translation}
POST /api/translate/stream  → SSE
GET  /api/preview/{job_id}/{page}   → image/png
GET  /api/download/{job_id}/{fmt}   → fmt ∈ {pdf, tex, docx, md}
```

Sự kiện SSE:

```
event: start
data: {"job_id":"…","total_pages":12,"pages":[1,2,…]}

event: page_progress
data: {"page_number":5,"completed":2,"total_pages":12}

event: page_completed
data: {"page_number":5,"original_text":"…","translated_text":"…",
       "image_url":"/api/preview/…/5","cached":false}

event: page_error
data: {"page_number":5,"error":"…","retriable":false}

event: build_progress
data: {"stage":"validate|repair|compile|fallback","detail":"…"}

event: completed
data: {"job_id":"…","artifacts":{"pdf":true,"tex":true,"docx":true,"md":true},
       "failed_pages":[7],"fallback_frames":[3]}

event: failed
data: {"job_id":"…","stage":"compile","error":"…"}
```

Ba thay đổi so với bản cũ:

1. **Heartbeat** `: ping` mỗi 15s — giữ kết nối khi AI xử lý lâu.
2. **Khung `data:` nhiều dòng đúng chuẩn** — mỗi dòng một tiền tố `data: `. Bản cũ parse bằng `line.replace("data:","")` nên chỉ giữ được dòng cuối và sẽ hỏng nếu JSON có newline.
3. **Tách `failed` khỏi `completed`** — bản cũ báo `completed` cả khi xuất file lỗi, khiến client bật hết nút tải rồi nhận 404.

`total_pages` được gửi trong `start` để client tính phần trăm thật.

### 5.7 Frontend

Vanilla JS, không build step.

- **DOMPurify** (CDN + SRI) bọc mọi điểm nhồi HTML. Thêm SRI cho KaTeX và marked.
- **Viết lại `beamer_renderer.js` thành parser thật**:
  - Stack-based → block lồng nhau hoạt động (regex non-greedy hiện tại dừng ở `\end{block}` đầu tiên).
  - Hiểu cả `\column{...}` lẫn `\begin{column}{...}...\end{column}` (hiện chỉ hiểu dạng đầu).
  - **Tách math ra placeholder trước mọi biến đổi text, trả lại sau** → hết chuyện `\textbf` bên trong `$...$` bị thay thành `<strong>` trước khi KaTeX kịp thấy.
  - Bỏ `grid-cols-${n}` động, dùng bảng tra class cố định (class động chỉ sống được nhờ Tailwind CDN JIT).
  - Không tự bọc card riêng nữa — hiện tại card của renderer nằm trong card của trang, thành khung đôi.
- **Chèn card theo `page_number`**, không `appendChild` (hệ quả của dịch song song).
- Progress bar dùng biến JS thật thay cho `progressBar.dataset.total` — thuộc tính này chưa từng được ghi, chỉ được đọc, nên phần trăm luôn kẹt ở 90%.
- **Nút Hủy** bằng `AbortController`.
- `escapeHTML` chuyển thành hàm dùng chung; hiện nó nằm trong closure `DOMContentLoaded` nên `beamer_renderer.js` gọi tới là `ReferenceError`, không phải fallback.
- Accessibility: `for`/`id` cho mọi label; drop zone có `role="button"` + `tabindex="0"` + Enter/Space; modal có Esc + focus trap + `aria-modal="true"` + click backdrop; toast có `role="status"`.

### 5.8 Bảo mật

Localhost nên hạ ưu tiên, nhưng những mục sau vẫn làm vì rẻ và PDF tải về có thể độc hại:

- **XSS**: output của AI đi qua DOMPurify. Đây là rủi ro thật — nội dung bắt nguồn từ PDF do người khác cung cấp.
- **Validate `job_id`/`file_id` là UUID** trước khi ghép vào đường dẫn. Hiện `job_id` được nội suy thẳng vào path.
- **Upload theo stream có giới hạn**, dừng sớm khi vượt ngưỡng. Hiện `await file.read()` nạp toàn bộ vào RAM *rồi mới* kiểm tra dung lượng.
- **Sanitize filename** trong header `Content-Disposition`.
- **API key**: dùng `SecretStr`, redact trong log. Key vẫn đi qua body request (chấp nhận được ở localhost) nhưng không được rơi vào file log.
- **SSRF**: chặn IP nội bộ và địa chỉ metadata trong `base_url`. Khoảng 20 dòng. Hiện `/api/test-connection` phản chiếu cả `resp.text` của đích về client.

### 5.9 Config & logging

- `config.py` dùng `SettingsConfigDict(env_file=".env")`. **Bỏ `mkdir` lúc import** — side effect khi import làm hỏng test isolation và filesystem chỉ đọc.
- `.env.example` liệt kê đủ: concurrency, max_tokens, timeout, TTL, `tectonic_path`, model mặc định từng provider.
- Logging có cấu trúc, redact secret. Hiện toàn app **không có một dòng log nào**.

---

## 6. Kế hoạch kiểm thử

Bộ test hiện tại có 12 ca đều là smoke test: không mock provider nào (`test_translate_text_empty` chỉ kiểm tra nhánh chuỗi rỗng), không test SSE, không test đường lỗi.

| Vùng | Ca kiểm thử |
|---|---|
| Provider | `respx` mock từng adapter; map lỗi; retry đúng mã lỗi; **không** retry 401/403; tôn trọng `Retry-After` |
| Cache | hit/miss; khoá ổn định qua các lần chạy; `prompt_version` đổi thì cache vô hiệu |
| Chunker | trang dài cắt ở ranh giới block, không cắt giữa môi trường |
| Validator | input lệch `\begin`/`\end`, ngoặc lệch, `$` lẻ, bị cắt cụt giữa command |
| Sanitizer | gỡ fence; gỡ preamble AI tự thêm; loại `\write18`/`\input` |
| Assembler | preamble đúng bản đã xác thực; marker `DSH-FRAME-<n>` đủ và đúng thứ tự |
| Compiler | **compile thật một file Beamer tiếng Việt**, khẳng định glyph đủ (so sánh bỏ khoảng trắng); bỏ qua cảnh báo `nullfont`; hạ cấp frame khi compile fail; thiếu tectonic thì suy giảm mềm |
| Export | .docx/.md **không chứa** `\begin{frame}`; tiếng Việt có dấu đúng |
| Page range | input sai → 400, **không** trả về toàn bộ trang |
| Path | `job_id` dạng `../../…` bị từ chối |
| SSE | chạy hết luồng với provider mock; khung `data:` nhiều dòng; có heartbeat; export lỗi phát `failed` chứ không `completed` |
| Frontend | parser Beamer: block lồng nhau, cả hai dạng column, math không bị biến đổi trước KaTeX |

Test compile thật sẽ chậm và cần mạng ở lần đầu (tectonic tải package) — đánh dấu `@pytest.mark.slow`, tách khỏi lượt chạy mặc định.

Hạ tầng: `pyproject.toml` với ruff + mypy + pytest (`asyncio_mode = "auto"`); pin dependency bằng `==`; tách `requirements-dev.txt`.

**Lưu ý môi trường**: `python` trên PATH của máy này trỏ vào venv của hermes-agent và không có pytest. Phải dùng `C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe`. Ghi vào README.

---

## 7. Truy vết: lỗi hiện tại → chỗ được sửa

| Lỗi | Vị trí hiện tại | Sửa ở |
|---|---|---|
| `max_tokens` 4096 hardcode làm cắt cụt bản dịch | `ai_service.py:102` | 5.2 |
| .docx/.pdf chứa LaTeX source thô | `doc_service.py:47-52,108-112` | 5.4 |
| Preamble `inputenc`+babel dễ vỡ với tiếng Việt | `doc_service.py:135-136` | mục 2, 5.3 |
| Trang lỗi bị loại khỏi kết quả xuất | `routes.py:113-119` | 5.2, 5.6 |
| Parser Beamer vỡ với block lồng nhau | `beamer_renderer.js:71-89` | 5.7 |
| Chỉ hiểu `\column{}`, không hiểu `\begin{column}` | `beamer_renderer.js:92-97` | 5.7 |
| `\textbf` trong math bị phá trước KaTeX | `beamer_renderer.js:123-126` | 5.7 |
| Dịch tuần tự hoàn toàn | `routes.py:84` | 5.2 |
| Không có retry | `routes.py:113-119` | 5.2 |
| Page range sai → dịch cả tài liệu | `pdf_service.py:28` | 5.1 |
| Mở lại file PDF cho từng trang | `pdf_service.py:49` | 5.1 |
| Progress bar kẹt 90% (`dataset.total` chỉ đọc, chưa từng ghi) | `app.js:301` | 5.6, 5.7 |
| Export lỗi vẫn báo `completed` | `routes.py:136` | 5.6 |
| `JOB_STORE` giữ base64 ảnh vĩnh viễn | `routes.py:18,110` | 5.1, 5.5 |
| Upload/export không bao giờ được dọn | — | 5.5 |
| Output AI vào `innerHTML` không sanitize | `app.js:327,352`, cả `beamer_renderer.js` | 5.7, 5.8 |
| `escapeHTML` trong closure → `ReferenceError` khi gọi từ file khác | `app.js:448` vs `beamer_renderer.js:18` | 5.7 |
| Nạp toàn bộ upload vào RAM rồi mới kiểm tra size | `routes.py:35-37` | 5.8 |
| `job_id` nội suy thẳng vào đường dẫn | `routes.py:141-148` | 5.8 |
| `/api/test-connection` phản chiếu `resp.text` của đích | `ai_service.py:73`, `routes.py:61` | 5.8 |
| `mkdir` lúc import | `config.py:9-10` | 5.9 |
| Không có log | toàn bộ app | 5.9 |
| Font PDF chỉ tìm ở `C:/Windows/Fonts`, fallback mất dấu | `doc_service.py:22-35` | 5.4 |
| SSE parse `data:` chỉ giữ dòng cuối | `app.js:277-279` | 5.6 |
| Card lồng card | `app.js:352` + `beamer_renderer.js:134` | 5.7 |
| `grid-cols-${n}` động, chết nếu build | `beamer_renderer.js:96` | 5.7 |

---

## 8. Rủi ro & phương án dự phòng

| Rủi ro | Mức | Dự phòng |
|---|---|---|
| LaTeX do LLM sinh vẫn không compile sau repair | **Cao** | Hạ cấp từng frame về text thuần trong `frame[fragile]`; cả deck vẫn build. Đây là cơ chế bắt buộc, không phải tuỳ chọn |
| XeTeX vỡ với tiếng Việt trong tình huống chưa lường | Thấp | Đã đo trên slide có 28 ký tự dấu phức hợp. Nếu vẫn vỡ: MiKTeX + `pdflatex` + `vntex` |
| Lần compile đầu cần mạng để tectonic tải package | Trung bình | Warm-up cache lúc setup; `--only-cached` cho lần chạy offline |
| Dịch song song đụng rate limit | Trung bình | Concurrency cấu hình được, mặc định thấp (4); tôn trọng `Retry-After` |
| Cache phình đĩa | Thấp | TTL 30 ngày + dọn định kỳ |
| Tên model mặc định lỗi thời | Trung bình | Không hardcode trong code; đặt ở `.env.example` để sửa không cần đổi code |

---

## 9. Tiêu chí hoàn thành

1. Upload một PDF slide, chọn Beamer, dịch → tải về `.pdf` **mở được, đúng slide, tiếng Việt đủ dấu, công thức đúng**.
2. Một trang lỗi API không làm mất trang đó khỏi kết quả, và không làm sập cả job.
3. Chạy lại cùng tài liệu với cùng cấu hình → **không phát sinh lời gọi API nào** (cache hit toàn bộ).
4. `.docx` và `.md` không chứa bất kỳ chuỗi `\begin{frame}` nào.
5. Progress bar đi từ 0 đến 100 theo tiến độ thật.
6. Toàn bộ test pass, gồm ca compile thật đối chiếu glyph tiếng Việt.
7. Không còn điểm nào nhồi HTML chưa qua sanitize.
