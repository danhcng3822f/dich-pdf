from pathlib import Path
import shutil
import logging
from babeldoc.assets.assets import get_font_and_metadata
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


def get_font_path(lang: str) -> str:
    """
    Get the absolute file path to a suitable Unicode font for the given language.
    Downloads the font via babeldoc if not present in settings.fonts_dir,
    and caches it locally in settings.fonts_dir.
    """
    font_name = get_font_name_for_lang(lang)
    fonts_dir = Path(settings.fonts_dir)
    fonts_dir.mkdir(parents=True, exist_ok=True)
    target_path = fonts_dir / font_name

    if not target_path.exists():
        logger.info("Font %s not found in %s, retrieving via babeldoc...", font_name, fonts_dir)
        cached_path, _ = get_font_and_metadata(font_name)
        cached_path = Path(cached_path)
        if cached_path.resolve() != target_path.resolve():
            shutil.copy2(cached_path, target_path)

    if not target_path.exists():
        raise FileNotFoundError(f"Font file could not be located or downloaded: {font_name}")

    return str(target_path)
