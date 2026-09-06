from pathlib import Path
import shutil
import logging
from typing import List
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Map language codes and names to their corresponding font files
CJK_LANG_MAP = {
    # Simplified Chinese
    "zh": "SourceHanSerifCN-Regular.ttf",
    "zh-cn": "SourceHanSerifCN-Regular.ttf",
    "zh-hans": "SourceHanSerifCN-Regular.ttf",
    "zh_cn": "SourceHanSerifCN-Regular.ttf",
    "zh_hans": "SourceHanSerifCN-Regular.ttf",
    "chinese": "SourceHanSerifCN-Regular.ttf",
    "chinese (simplified)": "SourceHanSerifCN-Regular.ttf",
    "chinese-simplified": "SourceHanSerifCN-Regular.ttf",

    # Traditional Chinese
    "zh-tw": "SourceHanSerifTW-Regular.ttf",
    "zh-hant": "SourceHanSerifTW-Regular.ttf",
    "zh_tw": "SourceHanSerifTW-Regular.ttf",
    "zh_hant": "SourceHanSerifTW-Regular.ttf",
    "chinese (traditional)": "SourceHanSerifTW-Regular.ttf",
    "chinese-traditional": "SourceHanSerifTW-Regular.ttf",

    # Japanese
    "ja": "SourceHanSerifJP-Regular.ttf",
    "jp": "SourceHanSerifJP-Regular.ttf",
    "japanese": "SourceHanSerifJP-Regular.ttf",
    "jpn": "SourceHanSerifJP-Regular.ttf",

    # Korean
    "ko": "SourceHanSerifKR-Regular.ttf",
    "kr": "SourceHanSerifKR-Regular.ttf",
    "korean": "SourceHanSerifKR-Regular.ttf",
    "kor": "SourceHanSerifKR-Regular.ttf",
}

DEFAULT_FONT_NAME = "GoNotoKurrent-Regular.ttf"


def get_font_name_for_lang(lang: str) -> str:
    """Map language string or font name to standard font file name."""
    clean = (lang or "").strip().lower()
    if clean.endswith(".ttf") or clean.endswith(".otf"):
        return lang.strip()
    return CJK_LANG_MAP.get(clean, DEFAULT_FONT_NAME)


def _download_font_file(font_name: str, target_path: Path) -> Path:
    """Download font file directly from public mirrors with fallback."""
    urls = [
        f"https://huggingface.co/datasets/awwaawwa/BabelDOC-Assets/resolve/main/fonts/{font_name}?download=true",
        f"https://hf-mirror.com/datasets/awwaawwa/BabelDOC-Assets/resolve/main/fonts/{font_name}?download=true",
    ]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_path.with_suffix(".tmp")
    for url in urls:
        try:
            logger.info("Downloading font %s from %s...", font_name, url)
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(temp_target, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)
            temp_target.replace(target_path)
            logger.info("Successfully saved font %s to %s", font_name, target_path)
            return target_path
        except Exception as e:
            logger.warning("Failed downloading font from %s: %s", url, e)
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except OSError:
                    pass

    raise RuntimeError(f"Could not download font {font_name} from available sources.")


def get_font_path(lang: str) -> str:
    """
    Get the absolute file path to a suitable Unicode font for the given language.
    Downloads the font if not present in settings.fonts_dir,
    and caches it locally in settings.fonts_dir.
    """
    font_name = get_font_name_for_lang(lang)
    fonts_dir = Path(settings.fonts_dir)
    fonts_dir.mkdir(parents=True, exist_ok=True)
    target_path = fonts_dir / font_name

    if not target_path.exists():
        logger.info("Font %s not found in %s, retrieving...", font_name, fonts_dir)
        _download_font_file(font_name, target_path)

    if not target_path.exists():
        raise FileNotFoundError(f"Font file could not be located or downloaded: {font_name}")

    return str(target_path)
