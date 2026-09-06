# Đại tu AI PDF Translator: Beamer slide → PDF compile sẵn — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Biến app dịch PDF hiện tại thành công cụ xuất được **PDF slide Beamer đã compile**, dịch song song có retry, và cache bản dịch để chạy lại không tốn thêm tiền API.

**Architecture:** Tách luồng monolith trong một generator SSE thành các stage test được riêng: trích xuất → dịch (song song, có cache) → sanitize/validate/repair LaTeX → assemble → compile bằng Tectonic → xuất artifact. Stage dịch có cache địa chỉ theo nội dung, nên vòng repair–compile chạy lại được mà không gọi lại API.

**Tech Stack:** Python 3.11, FastAPI, PyMuPDF, httpx, Tectonic 0.17.0 (XeTeX), vanilla JS + DOMPurify, pytest + respx, `node --test` cho JS.

**Spec:** `docs/superpowers/specs/2026-09-03-pdf-translation-overhaul-design.md`

## Global Constraints

- **Python thực thi**: `C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe`. `python` trên PATH trỏ vào venv hermes-agent và **không có pytest**. Mọi lệnh test trong plan này dùng đường dẫn đầy đủ.
- **Preamble Beamer — dùng đúng nguyên văn, không thêm bớt** (đã compile thành công trong spike):
  ```latex
  \documentclass[aspectratio=169]{beamer}
  \usepackage{fontspec}
  \usepackage[vietnamese]{babel}
  \usepackage{amsmath,amssymb}
  \usetheme{Madrid}
  ```
  Không `\setsansfont`, không `inputenc`, không `polyglossia`.
- **Tectonic**: `%LOCALAPPDATA%\Tectonic\tectonic.exe` (0.17.0, đã cài). Luôn compile với `--untrusted` vì LaTeX do LLM sinh.
- **Bộ phân tích log compile PHẢI bỏ qua** `Missing character ... in font nullfont` và `accessing absolute path`. Cả hai vô hại; coi chúng là lỗi sẽ làm mọi lần compile thất bại.
- **Không bao giờ trích text từ PDF đã compile** để sinh `.docx`/`.md`. XeTeX phát kern thay glyph space sau ký tự có dấu dưới → `xử lý` bị dán thành `xửlý`. Luôn đi từ LaTeX source.
- **Khi so sánh text tiếng Việt trích từ PDF trong test**: chuẩn hoá NFC và **bỏ sạch khoảng trắng** trước khi so, vì lý do trên.
- **Không hardcode tên model trong Python.** Model mặc định nằm ở `.env.example`.
- **Không thêm** Docker, CI, TypeScript, build step frontend, OCR. Ngoài phạm vi theo spec mục 1.
- **Ngôn ngữ**: comment và message hướng tới người dùng viết tiếng Việt; tên biến/hàm tiếng Anh.
- **Phiên bản pin** (đúng bản đang cài, không nâng trong plan này): `fastapi==0.141.1`, `uvicorn==0.52.1`, `python-multipart==0.0.32`, `pymupdf==1.28.2`, `python-docx==1.2.0`, `httpx==0.28.1`, `pydantic==2.12.5`, `pydantic-settings==2.15.0`, `pytest==9.1.1`, `pytest-asyncio==1.4.0`.
- **`reportlab` bị xoá** khỏi dependency.
- Commit sau mỗi task. Prefix: `feat:`, `fix:`, `refactor:`, `test:`, `chore:`, `docs:`.

---

## Cấu trúc file

| File | Trách nhiệm | Task |
|---|---|---|
| `pyproject.toml` | Cấu hình ruff / mypy / pytest | 1 |
| `requirements.txt`, `requirements-dev.txt` | Dependency đã pin | 1 |
| `.env.example` | Mọi setting + model mặc định | 1 |
| `app/config.py` | Settings; **không** mkdir lúc import | 1 |
| `app/logging_setup.py` | Cấu hình log + redact secret | 1 |
| `app/api/schemas.py` | Enum provider, `AIConfig`, request/response | 2 |
| `app/services/netguard.py` | Chặn base_url trỏ vào IP nội bộ / metadata | 2 |
| `app/jobs.py` | Validate UUID, manifest JSON, dọn TTL | 3 |
| `app/services/pdf_service.py` | Mở PDF một lần, text theo block, preview PNG | 4 |
| `app/services/translate/prompts.py` | System prompt + `PROMPT_VERSION` | 5 |
| `app/services/translate/cache.py` | Cache địa chỉ theo nội dung | 6 |
| `app/services/translate/chunker.py` | Ước lượng token, cắt an toàn | 7 |
| `app/services/translate/providers.py` | Adapter 3 họ API + phân loại lỗi | 8 |
| `app/services/translate/engine.py` | Semaphore + retry + tra cache | 9 |
| `app/services/latex/sanitizer.py` | Gỡ fence/preamble, chặn lệnh cấm | 10 |
| `app/services/latex/validator.py` | Cân bằng env/ngoặc/math, phát hiện cắt cụt, repair cơ học | 11 |
| `app/services/latex/assembler.py` | Preamble + marker + frame hạ cấp | 12 |
| `app/services/latex/compiler.py` | Gọi tectonic, phân tích log, định vị frame lỗi | 13 |
| `app/services/export_service.py` | LaTeX → plain text → `.docx`/`.md` | 14 |
| `app/api/routes.py` | Điều phối, SSE, preview, download | 15 |
| `app/static/js/beamer_renderer.js` | Parser Beamer thật (stack-based) | 16 |
| `app/static/js/app.js` | SSE, chèn theo trang, progress, hủy, sanitize | 17 |
| `app/templates/index.html` | A11y, SRI, DOMPurify, nút Hủy | 18 |
| `run.py`, `README.md` | Đọc config, tài liệu | 19 |

**Xoá**: `app/services/ai_service.py`, `app/services/doc_service.py` (thay bằng module mới); `tests/test_ai_service.py`, `tests/test_doc_service.py`.

---

## Task 1: Nền móng — cấu hình, logging, dependency

**Files:**
- Create: `pyproject.toml`, `.env.example`, `requirements-dev.txt`, `app/logging_setup.py`
- Modify: `app/config.py` (thay toàn bộ), `requirements.txt` (thay toàn bộ), `.gitignore`
- Test: `tests/test_config.py`, `tests/test_logging_setup.py`

**Interfaces:**
- Consumes: không
- Produces:
  - `app.config.Settings` với các thuộc tính: `app_name: str`, `storage_dir: Path`, `max_upload_size_mb: int`, `concurrency: int`, `max_retries: int`, `max_tokens: int`, `request_timeout_s: float`, `chunk_token_threshold: int`, `compile_timeout_s: float`, `tectonic_path: Path | None`, `artifact_ttl_hours: int`, `cache_ttl_days: int`, `host: str`, `port: int`, `reload: bool`, `log_level: str`
  - properties: `upload_dir`, `export_dir`, `preview_dir`, `cache_dir`, `job_dir` (đều `Path`)
  - `app.config.settings` — instance singleton
  - `app.config.ensure_dirs() -> None`
  - `app.logging_setup.setup_logging(level: str) -> None`
  - `app.logging_setup.redact(text: str) -> str`

- [ ] **Step 1: Viết test thất bại cho config**

Tạo `tests/test_config.py`:

```python
import importlib
from pathlib import Path


def test_import_does_not_create_directories(tmp_path, monkeypatch):
    """Import không được có side effect tạo thư mục."""
    monkeypatch.setenv("DICHPDF_STORAGE_DIR", str(tmp_path / "kho"))
    import app.config

    importlib.reload(app.config)
    assert not (tmp_path / "kho").exists()


def test_ensure_dirs_creates_all_subdirs(tmp_path, monkeypatch):
    monkeypatch.setenv("DICHPDF_STORAGE_DIR", str(tmp_path / "kho"))
    import app.config

    importlib.reload(app.config)
    app.config.ensure_dirs()

    s = app.config.settings
    for d in (s.upload_dir, s.export_dir, s.preview_dir, s.cache_dir, s.job_dir):
        assert d.is_dir(), f"thiếu {d}"


def test_settings_read_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DICHPDF_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("DICHPDF_CONCURRENCY", "9")
    monkeypatch.setenv("DICHPDF_MAX_TOKENS", "1234")
    import app.config

    importlib.reload(app.config)
    assert app.config.settings.concurrency == 9
    assert app.config.settings.max_tokens == 1234


def test_defaults_match_spec():
    import app.config

    importlib.reload(app.config)
    s = app.config.Settings(storage_dir=Path("/tmp/x"))
    assert s.concurrency == 4
    assert s.max_retries == 3
    assert s.max_tokens == 8192
    assert s.chunk_token_threshold == 3000
    assert s.artifact_ttl_hours == 24
    assert s.cache_ttl_days == 30
```

- [ ] **Step 2: Chạy test để xác nhận nó fail**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_config.py -v`

Expected: FAIL — `ensure_dirs` chưa tồn tại, và `test_import_does_not_create_directories` fail vì `config.py` hiện gọi `mkdir` lúc import.

- [ ] **Step 3: Thay toàn bộ `app/config.py`**

```python
"""Cấu hình ứng dụng.

Nguyên tắc: import module này KHÔNG được tạo thư mục hay chạm đĩa. Việc tạo
thư mục do ensure_dirs() làm, gọi lúc app khởi động.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="DICHPDF_", extra="ignore"
    )

    app_name: str = "AI PDF Translator"
    storage_dir: Path = BASE_DIR / "storage"
    max_upload_size_mb: int = 50

    # Dịch thuật
    concurrency: int = 4
    max_retries: int = 3
    max_tokens: int = 8192
    request_timeout_s: float = 120.0
    chunk_token_threshold: int = 3000

    # LaTeX
    tectonic_path: Path | None = None
    compile_timeout_s: float = 120.0

    # Dọn rác
    artifact_ttl_hours: int = 24
    cache_ttl_days: int = 30

    # Chạy server
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"

    @property
    def upload_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def export_dir(self) -> Path:
        return self.storage_dir / "exports"

    @property
    def preview_dir(self) -> Path:
        return self.storage_dir / "previews"

    @property
    def cache_dir(self) -> Path:
        return self.storage_dir / "cache"

    @property
    def job_dir(self) -> Path:
        return self.storage_dir / "jobs"


settings = Settings()


def ensure_dirs() -> None:
    """Tạo mọi thư mục lưu trữ. Gọi lúc app khởi động, không gọi lúc import."""
    for directory in (
        settings.upload_dir,
        settings.export_dir,
        settings.preview_dir,
        settings.cache_dir,
        settings.job_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Chạy lại test config**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_config.py -v`

Expected: PASS cả 4 ca.

- [ ] **Step 5: Viết test thất bại cho redact**

Tạo `tests/test_logging_setup.py`:

```python
from app.logging_setup import redact


def test_redact_bearer_token():
    out = redact("Authorization: Bearer sk-proj-abcdef1234567890")
    assert "sk-proj-abcdef1234567890" not in out
    assert "***" in out


def test_redact_openai_style_key():
    out = redact('{"api_key": "sk-abcdefghijklmnopqrstuvwxyz012345"}')
    assert "abcdefghijklmnopqrstuvwxyz" not in out


def test_redact_query_param_key():
    out = redact("https://example.com/v1/models?key=AIzaSyD-verySecretValue123")
    assert "AIzaSyD-verySecretValue123" not in out


def test_redact_leaves_plain_text_alone():
    msg = "Đang dịch trang 5 / 12"
    assert redact(msg) == msg
```

- [ ] **Step 6: Chạy test để xác nhận fail**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_logging_setup.py -v`

Expected: FAIL — `ModuleNotFoundError: app.logging_setup`.

- [ ] **Step 7: Tạo `app/logging_setup.py`**

```python
"""Cấu hình logging kèm việc che secret.

App nhận API key qua body request. Key không được rơi vào file log, kể cả khi
một exception mang theo cả payload.
"""

import logging
import re
import sys

# Các dạng secret thường gặp: Bearer token, sk-..., AIza..., ?key=...
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]{8,}", re.I), r"\1***"),
    (re.compile(r"\b(sk-[A-Za-z0-9\-_]{4})[A-Za-z0-9\-_]{8,}"), r"\1***"),
    (re.compile(r"\b(AIza[A-Za-z0-9\-_]{4})[A-Za-z0-9\-_]{8,}"), r"\1***"),
    (re.compile(r"([?&](?:key|api_key|access_token)=)[^&\s\"']{8,}", re.I), r"\1***"),
    (
        re.compile(
            r'("(?:api_key|apiKey|x-api-key)"\s*:\s*")[^"]{8,}(")',
            re.I,
        ),
        r"\1***\2",
    ),
)


def redact(text: str) -> str:
    """Trả về text đã che mọi secret nhận dạng được."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO") -> None:
    """Gắn một handler ra stderr có che secret lên root logger."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
```

- [ ] **Step 8: Chạy lại test logging**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_logging_setup.py -v`

Expected: PASS cả 4 ca.

- [ ] **Step 9: Tạo `pyproject.toml`**

```toml
[project]
name = "dich-pdf"
version = "2.0.0"
description = "Dịch slide PDF bằng AI, xuất Beamer PDF đã compile"
requires-python = ">=3.11"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-m 'not slow'"
markers = [
    "slow: ca chạy chậm hoặc cần mạng (compile LaTeX thật). Chạy bằng -m slow",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "PTH"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.11"
warn_unused_ignores = true
warn_redundant_casts = true
disallow_untyped_defs = true
ignore_missing_imports = true
files = ["app"]
```

- [ ] **Step 10: Thay `requirements.txt` và tạo `requirements-dev.txt`**

`requirements.txt`:

```
fastapi==0.141.1
uvicorn[standard]==0.52.1
python-multipart==0.0.32
pymupdf==1.28.2
python-docx==1.2.0
httpx==0.28.1
pydantic==2.12.5
pydantic-settings==2.15.0
```

`requirements-dev.txt`:

```
-r requirements.txt
pytest==9.1.1
pytest-asyncio==1.4.0
respx==0.22.0
ruff==0.14.2
mypy==1.18.2
```

- [ ] **Step 11: Tạo `.env.example`**

```
# Sao chép thành .env rồi sửa. Mọi biến đều có tiền tố DICHPDF_.

# --- Lưu trữ ---
DICHPDF_STORAGE_DIR=storage
DICHPDF_MAX_UPLOAD_SIZE_MB=50

# --- Dịch thuật ---
# Số trang dịch song song. Tăng thì nhanh hơn nhưng dễ đụng rate limit.
DICHPDF_CONCURRENCY=4
DICHPDF_MAX_RETRIES=3
DICHPDF_MAX_TOKENS=8192
DICHPDF_REQUEST_TIMEOUT_S=120
DICHPDF_CHUNK_TOKEN_THRESHOLD=3000

# --- LaTeX ---
# Để trống thì tự tìm trong PATH rồi %LOCALAPPDATA%\Tectonic
DICHPDF_TECTONIC_PATH=
DICHPDF_COMPILE_TIMEOUT_S=120

# --- Dọn rác ---
DICHPDF_ARTIFACT_TTL_HOURS=24
DICHPDF_CACHE_TTL_DAYS=30

# --- Server ---
DICHPDF_HOST=127.0.0.1
DICHPDF_PORT=8000
DICHPDF_RELOAD=false
DICHPDF_LOG_LEVEL=INFO

# --- Model mặc định gợi ý cho UI ---
# Tên model thay đổi theo thời gian. Sửa ở đây, KHÔNG sửa trong code Python.
# Kiểm tra tên model hiện hành trên trang tài liệu của từng nhà cung cấp.
DICHPDF_DEFAULT_MODEL_OPENAI=gpt-4o-mini
DICHPDF_DEFAULT_MODEL_DEEPSEEK=deepseek-chat
DICHPDF_DEFAULT_MODEL_GEMINI=gemini-1.5-flash
DICHPDF_DEFAULT_MODEL_CLAUDE=claude-3-5-sonnet-20241022
```

- [ ] **Step 12: Bổ sung `.gitignore`**

Thêm vào cuối file:

```
storage/previews/*
storage/jobs/*
storage/cache/*
!storage/previews/.gitkeep
!storage/jobs/.gitkeep
!storage/cache/.gitkeep
.venv/
.ruff_cache/
.mypy_cache/
```

- [ ] **Step 13: Cài dev dependency và chạy toàn bộ test**

Run:
```
& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pip install -r requirements-dev.txt
& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest -v
```

Expected: test cũ vẫn pass (12 ca) cộng 8 ca mới. `test_api_routes.py` và `test_frontend_assets.py` có thể fail nếu `main.py` phụ thuộc `mkdir` lúc import — nếu vậy, thêm `ensure_dirs()` vào `app/main.py` ngay sau import và chạy lại.

- [ ] **Step 14: Commit**

```bash
git add pyproject.toml requirements.txt requirements-dev.txt .env.example .gitignore app/config.py app/logging_setup.py app/main.py tests/test_config.py tests/test_logging_setup.py
git commit -m "feat: add pinned deps, env-driven config without import side effects, and secret-redacting logging"
```

---

## Task 2: Schema API và chặn SSRF

**Files:**
- Create: `app/api/schemas.py`, `app/services/netguard.py`
- Test: `tests/test_schemas.py`, `tests/test_netguard.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 1)
- Produces:
  - `app.api.schemas.Provider` — `str, Enum`: `OPENAI="openai"`, `DEEPSEEK="deepseek"`, `GEMINI="gemini"`, `CLAUDE="claude"`, `CUSTOM="custom"`
  - `app.api.schemas.AIConfig` — pydantic model: `provider: Provider`, `api_key: SecretStr`, `model: str`, `base_url: str | None`, `custom_prompt: str | None`, `temperature: float` (0.0–2.0), `max_tokens: int`
  - `app.api.schemas.TranslateRequest` — `file_id: str`, `target_lang: str`, `style: str`, `page_range: str`, `ai_config: AIConfig`
  - `app.api.schemas.EstimateRequest` — `file_id: str`, `page_range: str`
  - `app.services.netguard.UnsafeUrlError(ValueError)`
  - `app.services.netguard.assert_safe_url(url: str) -> None`

- [ ] **Step 1: Viết test thất bại cho schemas**

Tạo `tests/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.api.schemas import AIConfig, Provider, TranslateRequest


def _cfg(**over):
    base = {
        "provider": "openai",
        "api_key": "sk-test-key-value",
        "model": "some-model",
        "temperature": 0.3,
    }
    base.update(over)
    return AIConfig(**base)


def test_api_key_is_not_leaked_by_repr():
    cfg = _cfg()
    assert "sk-test-key-value" not in repr(cfg)
    assert "sk-test-key-value" not in str(cfg)
    assert cfg.api_key.get_secret_value() == "sk-test-key-value"


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError):
        _cfg(provider="khong-ton-tai")


def test_temperature_bounds_enforced():
    with pytest.raises(ValidationError):
        _cfg(temperature=-0.1)
    with pytest.raises(ValidationError):
        _cfg(temperature=2.1)
    assert _cfg(temperature=2.0).temperature == 2.0


def test_custom_provider_requires_base_url():
    with pytest.raises(ValidationError, match="base_url"):
        _cfg(provider="custom", base_url=None)
    assert _cfg(provider="custom", base_url="http://localhost:11434/v1")


def test_empty_api_key_rejected():
    with pytest.raises(ValidationError):
        _cfg(api_key="")


def test_max_tokens_defaults_from_settings():
    assert _cfg().max_tokens == 8192


def test_translate_request_defaults():
    req = TranslateRequest(file_id="abc", ai_config=_cfg())
    assert req.target_lang == "Vietnamese"
    assert req.style == "LaTeX_Beamer"
    assert req.page_range == "all"
    assert req.ai_config.provider is Provider.OPENAI
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_schemas.py -v`

Expected: FAIL — `ModuleNotFoundError: app.api.schemas`.

- [ ] **Step 3: Tạo `app/api/schemas.py`**

```python
"""Model request/response của API.

api_key dùng SecretStr để nó không lọt vào repr, log hay traceback.
"""

from enum import Enum

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.config import settings


class Provider(str, Enum):
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    CLAUDE = "claude"
    CUSTOM = "custom"


class AIConfig(BaseModel):
    provider: Provider
    api_key: SecretStr
    model: str = Field(min_length=1)
    base_url: str | None = None
    custom_prompt: str | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default_factory=lambda: settings.max_tokens, gt=0)

    @field_validator("api_key")
    @classmethod
    def _key_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key không được để trống")
        return value

    @model_validator(mode="after")
    def _custom_needs_base_url(self) -> "AIConfig":
        if self.provider is Provider.CUSTOM and not (self.base_url or "").strip():
            raise ValueError("provider 'custom' bắt buộc phải có base_url")
        return self


class TranslateRequest(BaseModel):
    file_id: str
    target_lang: str = "Vietnamese"
    style: str = "LaTeX_Beamer"
    page_range: str = "all"
    ai_config: AIConfig


class EstimateRequest(BaseModel):
    file_id: str
    page_range: str = "all"
```

- [ ] **Step 4: Chạy lại test schemas**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_schemas.py -v`

Expected: PASS cả 7 ca.

- [ ] **Step 5: Viết test thất bại cho netguard**

Tạo `tests/test_netguard.py`:

```python
import pytest

from app.services.netguard import UnsafeUrlError, assert_safe_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadata AWS
        "http://metadata.google.internal/",           # metadata GCP
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.10/v1",
        "http://172.16.0.1/v1",
        "http://[::1]:8000/v1",
        "http://0.0.0.0/v1",
    ],
)
def test_internal_targets_rejected(url):
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "https://api.deepseek.com/v1"],
)
def test_public_targets_allowed(url):
    assert_safe_url(url)


def test_non_http_scheme_rejected():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("file:///etc/passwd")
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("gopher://example.com/")


def test_allow_local_flag_permits_loopback():
    # Local LLM (Ollama/LMStudio) là trường hợp dùng hợp lệ, nhưng phải chủ động bật.
    assert_safe_url("http://localhost:11434/v1", allow_local=True)
```

- [ ] **Step 6: Chạy test để xác nhận fail**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_netguard.py -v`

Expected: FAIL — `ModuleNotFoundError: app.services.netguard`.

- [ ] **Step 7: Tạo `app/services/netguard.py`**

```python
"""Chặn base_url do người dùng nhập trỏ vào hạ tầng nội bộ.

App cho phép nhập base_url tuỳ ý, nghĩa là server sẽ gửi request tới đó. Nếu
không kiểm tra, endpoint test kết nối thành một proxy SSRF: nó còn phản chiếu
body phản hồi về cho client.

Local LLM (Ollama, LMStudio, vLLM) là trường hợp dùng hợp lệ, nên loopback
được phép khi allow_local=True — người dùng phải chủ động chọn.
"""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "instance-data"}


class UnsafeUrlError(ValueError):
    """base_url trỏ vào đích không được phép."""


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str, *, allow_local: bool = False) -> None:
    """Raise UnsafeUrlError nếu url không an toàn để server gọi tới."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"Chỉ hỗ trợ http/https, nhận được '{parsed.scheme}'")

    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeUrlError("URL thiếu hostname")

    if host in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"Hostname '{host}' bị chặn (endpoint metadata)")

    try:
        candidates = [
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(host, None)
        ]
    except (socket.gaierror, ValueError) as exc:
        raise UnsafeUrlError(f"Không phân giải được hostname '{host}'") from exc

    for ip in candidates:
        if _is_private(ip):
            if allow_local and ip.is_loopback:
                continue
            raise UnsafeUrlError(
                f"Hostname '{host}' phân giải ra địa chỉ nội bộ {ip}. "
                "Nếu đây là local LLM, hãy bật tuỳ chọn cho phép địa chỉ nội bộ."
            )
```

- [ ] **Step 8: Chạy lại test netguard**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_netguard.py -v`

Expected: PASS cả 13 ca.

- [ ] **Step 9: Commit**

```bash
git add app/api/schemas.py app/services/netguard.py tests/test_schemas.py tests/test_netguard.py
git commit -m "feat: add typed API schemas with SecretStr keys and SSRF guard for user base_url"
```

---

## Task 3: Job store và validate ID

**Files:**
- Create: `app/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 1)
- Produces:
  - `app.jobs.InvalidIdError(ValueError)`
  - `app.jobs.validate_id(value: str) -> str` — trả về value nếu là UUID v4 dạng chuẩn, ngược lại raise
  - `app.jobs.JobManifest` — dataclass: `job_id: str`, `filename: str`, `target_lang: str`, `style: str`, `page_numbers: list[int]`, `status: str`, `created_at: float`, `failed_pages: list[int]`, `fallback_frames: list[int]`, `artifacts: dict[str, bool]`
  - `app.jobs.JobStore` — `__init__(job_dir: Path)`, `save(manifest) -> None`, `load(job_id: str) -> JobManifest`, `cleanup(ttl_hours: float) -> int`
  - `app.jobs.cleanup_dir(directory: Path, ttl_hours: float) -> int`

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobs.py`:

```python
import time
import uuid

import pytest

from app.jobs import (
    InvalidIdError,
    JobManifest,
    JobStore,
    cleanup_dir,
    validate_id,
)


def test_validate_id_accepts_uuid4():
    value = str(uuid.uuid4())
    assert validate_id(value) == value


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "abc",
        "",
        "a" * 40,
        str(uuid.uuid4()) + "/../x",
        str(uuid.uuid4()) + ".pdf",
        "%2e%2e%2f",
    ],
)
def test_validate_id_rejects_traversal_and_junk(bad):
    with pytest.raises(InvalidIdError):
        validate_id(bad)


def test_manifest_round_trip(tmp_path):
    store = JobStore(tmp_path)
    manifest = JobManifest(
        job_id=str(uuid.uuid4()),
        filename="slide.pdf",
        target_lang="Vietnamese",
        style="LaTeX_Beamer",
        page_numbers=[1, 2, 3],
    )
    manifest.failed_pages.append(2)
    manifest.fallback_frames.append(3)
    manifest.artifacts["pdf"] = True
    store.save(manifest)

    loaded = store.load(manifest.job_id)
    assert loaded.job_id == manifest.job_id
    assert loaded.filename == "slide.pdf"
    assert loaded.page_numbers == [1, 2, 3]
    assert loaded.failed_pages == [2]
    assert loaded.fallback_frames == [3]
    assert loaded.artifacts["pdf"] is True


def test_load_rejects_bad_id_before_touching_disk(tmp_path):
    store = JobStore(tmp_path)
    with pytest.raises(InvalidIdError):
        store.load("../../secret")


def test_load_missing_job_raises_keyerror(tmp_path):
    store = JobStore(tmp_path)
    with pytest.raises(KeyError):
        store.load(str(uuid.uuid4()))


def test_cleanup_dir_removes_only_expired(tmp_path):
    old = tmp_path / "cu.txt"
    new = tmp_path / "moi.txt"
    old.write_text("x", encoding="utf-8")
    new.write_text("y", encoding="utf-8")

    two_days_ago = time.time() - 2 * 24 * 3600
    import os

    os.utime(old, (two_days_ago, two_days_ago))

    removed = cleanup_dir(tmp_path, ttl_hours=24)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_cleanup_dir_handles_missing_directory(tmp_path):
    assert cleanup_dir(tmp_path / "khong-ton-tai", ttl_hours=1) == 0


def test_store_cleanup_removes_expired_manifests(tmp_path):
    import os

    store = JobStore(tmp_path)
    manifest = JobManifest(
        job_id=str(uuid.uuid4()),
        filename="a.pdf",
        target_lang="Vietnamese",
        style="LaTeX_Beamer",
        page_numbers=[1],
    )
    store.save(manifest)
    path = tmp_path / f"{manifest.job_id}.json"
    stale = time.time() - 100 * 3600
    os.utime(path, (stale, stale))

    assert store.cleanup(ttl_hours=24) == 1
    assert not path.exists()
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_jobs.py -v`

Expected: FAIL — `ModuleNotFoundError: app.jobs`.

- [ ] **Step 3: Tạo `app/jobs.py`**

```python
"""Trạng thái job trên đĩa và validate ID.

Bản cũ giữ job trong một dict toàn cục, kèm cả ảnh base64 của mọi trang, và
không bao giờ giải phóng. Ở đây manifest nằm trên đĩa, ảnh nằm thành file
riêng, và cả hai được dọn theo TTL.

validate_id là chốt bảo mật: mọi ID từ client phải qua nó TRƯỚC khi được ghép
vào đường dẫn.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class InvalidIdError(ValueError):
    """ID không phải UUID hợp lệ, không được dùng để ghép đường dẫn."""


def validate_id(value: str) -> str:
    """Trả về value nếu là UUID dạng chuẩn; ngược lại raise InvalidIdError."""
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise InvalidIdError(f"ID không hợp lệ: {value!r}")
    return value


@dataclass
class JobManifest:
    job_id: str
    filename: str
    target_lang: str
    style: str
    page_numbers: list[int]
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    failed_pages: list[int] = field(default_factory=list)
    fallback_frames: list[int] = field(default_factory=list)
    artifacts: dict[str, bool] = field(default_factory=dict)


class JobStore:
    def __init__(self, job_dir: Path) -> None:
        self._dir = job_dir

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{validate_id(job_id)}.json"

    def save(self, manifest: JobManifest) -> None:
        path = self._path(manifest.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, job_id: str) -> JobManifest:
        path = self._path(job_id)
        if not path.exists():
            raise KeyError(f"Không tìm thấy job {job_id}")
        return JobManifest(**json.loads(path.read_text(encoding="utf-8")))

    def cleanup(self, ttl_hours: float) -> int:
        return cleanup_dir(self._dir, ttl_hours)


def cleanup_dir(directory: Path, ttl_hours: float) -> int:
    """Xoá file/thư mục cũ hơn ttl_hours. Trả về số mục đã xoá."""
    if not directory.is_dir():
        return 0

    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for entry in directory.iterdir():
        if entry.name == ".gitkeep":
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                for child in sorted(entry.rglob("*"), reverse=True):
                    child.unlink() if child.is_file() else child.rmdir()
                entry.rmdir()
            else:
                entry.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Không xoá được %s: %s", entry, exc)
    return removed
```

- [ ] **Step 4: Chạy lại test jobs**

Run: `& "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_jobs.py -v`

Expected: PASS cả 15 ca.

- [ ] **Step 5: Commit**

```bash
git add app/jobs.py tests/test_jobs.py
git commit -m "feat: add on-disk job manifests, UUID id validation, and TTL cleanup"
```

---
