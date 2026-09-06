import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

if os.environ.get("VERCEL"):
    STORAGE_DIR = Path("/tmp/storage")
else:
    STORAGE_DIR = BASE_DIR / "storage"

UPLOAD_DIR = STORAGE_DIR / "uploads"
EXPORT_DIR = STORAGE_DIR / "exports"
FONTS_DIR = STORAGE_DIR / "fonts"
MODELS_DIR = STORAGE_DIR / "models"
PREVIEWS_DIR = STORAGE_DIR / "previews"

for directory in (UPLOAD_DIR, EXPORT_DIR, FONTS_DIR, MODELS_DIR, PREVIEWS_DIR):
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

class Settings(BaseSettings):
    app_name: str = "PDF Translation AI"
    upload_dir: Path = UPLOAD_DIR
    export_dir: Path = EXPORT_DIR
    fonts_dir: Path = FONTS_DIR
    models_dir: Path = MODELS_DIR
    previews_dir: Path = PREVIEWS_DIR
    max_upload_size_mb: int = 50

settings = Settings()
