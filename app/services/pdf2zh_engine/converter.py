import concurrent.futures
import io
import logging
import re
import unicodedata
from enum import Enum
from string import Template
from typing import Any, Dict, List, Optional

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.utils import apply_matrix_pt, mult_matrix
from pymupdf import Font

try:
    from tenacity import retry, stop_after_attempt, wait_fixed
except ImportError:
    import time
    from functools import wraps

    def wait_fixed(secs):
        return secs

    def stop_after_attempt(attempts):
        return attempts

    def retry(wait=None, stop=3, reraise=True):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                max_attempts = stop if isinstance(stop, int) else 3
                delay = wait if isinstance(wait, (int, float)) else 1
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception:
                        if attempt == max_attempts and reraise:
                            raise
                        time.sleep(delay)
            return wrapper
        return decorator

from app.services.pdf2zh_engine.adapter import BaseTranslator, create_translator
from app.services.pdf2zh_engine.pdfinterp import PDFPageInterpreterEx

log = logging.getLogger(__name__)

_TOC_PAGE_TOKEN = r"(?:(?:[A-Za-z]+-)?\d+|[ivxlcdm]+)"
_TOC_ENTRY_RE = re.compile(
    rf"^\s*(?P<label>\S.*?)\s*"
    rf"(?P<leader>\.{{3,}}|(?:…\s*){{2,}})\s*"
    rf"(?P<page>{_TOC_PAGE_TOKEN}(?:\s*[-–]\s*{_TOC_PAGE_TOKEN})?)\s*$",
    re.IGNORECASE,
)


def split_toc_entry(text: str) -> Optional[tuple[str, str]]:
    """Split a dotted-leader TOC row into its label and terminal page token."""
    match = _TOC_ENTRY_RE.fullmatch(text)
    if not match:
        return None
    label = match.group("label").strip()
    page = re.sub(r"\s+", " ", match.group("page")).strip()
    return (label, page) if label and page else None


class OpType(Enum):
    TEXT = "text"
    LINE = "line"


class Paragraph:
    def __init__(
        self,
        y: float,
        x: float,
        x0: float,
        x1: float,
        y0: float,
        y1: float,
        size: float,
        brk: bool,
        color: Any = None,
        toc_page_number: Optional[str] = None,
    ) -> None:
        self.y: float = y
        self.x: float = x
        self.x0: float = x0
        self.x1: float = x1
        self.y0: float = y0
        self.y1: float = y1
        self.size: float = size
        self.brk: bool = brk
        self.color: Any = color
        self.toc_page_number: Optional[str] = toc_page_number
        self.colors: List[Any] = []
        if color is not None:
            self.colors.append(color)


def get_char_baseline_y(child: Any) -> float:
    """Get the true text baseline Y coordinate from character's matrix."""
    matrix = getattr(child, "matrix", None)
    if matrix and len(matrix) == 6:
        if abs(matrix[1]) < 1e-4 and abs(matrix[2]) < 1e-4:
            return float(matrix[5])
    return float(getattr(child, "y0", 0.0))


def is_white_or_near_white(color: Any) -> bool:
    """Check if a color is pure white or indistinguishable from white."""
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return color >= 0.98
    if isinstance(color, (tuple, list)):
        if len(color) == 3:  # RGB
            return all(float(c) >= 0.98 for c in color)
        if len(color) == 4:  # CMYK
            return all(float(c) <= 0.02 for c in color)
        if len(color) == 1:
            return float(color[0]) >= 0.98
    return False


def resolve_paragraph_color(paragraph: Paragraph) -> Any:
    """Resolve the effective text color for a paragraph.

    If a paragraph contains non-white body text but begins with a white bullet,
    number badge, or icon (e.g. '1' in a blue circle on Beamer slides), avoid
    inheriting the white bullet color, which renders the entire translated text
    invisible on light backgrounds.
    """
    colors = getattr(paragraph, "colors", [])
    if not colors:
        return paragraph.color

    non_white_colors = [c for c in colors if not is_white_or_near_white(c)]
    if non_white_colors:
        def color_key(c: Any) -> Any:
            if isinstance(c, (list, tuple)):
                return tuple(round(float(v), 4) for v in c)
            if isinstance(c, (int, float)):
                return round(float(c), 4)
            return c

        counter: Dict[Any, int] = {}
        for c in non_white_colors:
            k = color_key(c)
            counter[k] = counter.get(k, 0) + 1
        best_key = max(counter.keys(), key=lambda k: counter[k])
        for c in non_white_colors:
            if color_key(c) == best_key:
                return c

    return paragraph.color if paragraph.color is not None else colors[0]


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        super().__init__(rsrcmgr, None, "utf-8", 1, None)
        self.fontid: Dict[Any, str] = {}
        self.fontmap: Dict[str, Any] = {}

    def begin_page(self, page: Any, ctm: Any) -> None:
        x0, y0, x1, y1 = page.cropbox
        x0, y0 = apply_matrix_pt(ctm, (x0, y0))
        x1, y1 = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.cur_item = LTPage(getattr(page, "pageno", 0), mediabox)

    def end_page(self, page: Any) -> Any:
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name: str, bbox: Any, matrix: Any) -> None:
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> Any:
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix: Any,
        font: Any,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs: Any,
        graphicstate: PDFGraphicState,
    ) -> float:
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        self.cur_item.add(item)
        item.cid = cid
        item.font = font
        return item.adv


class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
        vfont: Optional[str] = None,
        vchar: Optional[str] = None,
        thread: int = 0,
        layout: Any = None,
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Optional[Font] = None,
        envs: Optional[Dict[str, Any]] = None,
        prompt: Optional[Template] = None,
        ignore_cache: bool = False,
        translator: Optional[BaseTranslator] = None,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout if layout is not None else {}
        self.noto_name = noto_name
        self.noto = noto
        self.envs = envs or {}
        self.prompt = prompt
        self.ignore_cache = ignore_cache
        self.last_page_text: str = ""

        if translator is not None:
            self.translator = translator
        else:
            param = service.split(":", 1) if service else ["google"]
            service_name = param[0].lower()
            service_model = param[1] if len(param) > 1 else ""

            SUPPORTED_SERVICES = {
                "google",
                "google_free",
                "bing",
                "bing_free",
                "openai",
                "deepseek",
                "gemini",
                "claude",
                "custom",
            }
            if service_name not in SUPPORTED_SERVICES:
                raise ValueError(f"Unsupported translation service: {service_name}")

            api_key = self.envs.get("api_key", "")
            model = service_model or self.envs.get("model", "")
            base_url = self.envs.get("base_url", "")
            custom_prompt = (
                self.envs.get("custom_prompt", "")
                or (prompt.template if prompt else "")
            )
            temperature = float(self.envs.get("temperature", 0.3))

            self.translator = create_translator(
                provider=service_name,
                target_lang=lang_out or "vi",
                source_lang=lang_in or "auto",
                api_key=api_key,
                model=model,
                base_url=base_url,
                custom_prompt=custom_prompt,
                temperature=temperature,
            )

    def receive_layout(self, ltpage: LTPage) -> str:
        # Paragraph stacks
        sstk: List[str] = []
        pstk: List[Paragraph] = []
        vbkt: int = 0

        # Formula buffers
        vstk: List[LTChar] = []
        vlstk: List[LTLine] = []
        vfix: float = 0

        # Formula storage stacks
        var: List[List[LTChar]] = []
        varl: List[List[LTLine]] = []
        varf: List[float] = []
        vlen: List[float] = []

        # Global
        lstk: List[LTLine] = []
        xt: Optional[LTChar] = None
        xt_cls: int = -1
        page_width = getattr(ltpage, "width", 595.0)
        vmax: float = page_width / 4

        def vflag(font: Any, char: str) -> bool:
            if isinstance(font, bytes):
                try:
                    font = font.decode("utf-8")
                except UnicodeDecodeError:
                    font = ""
            font = str(font or "").split("+")[-1]
            if re.match(r"\(cid:", char):
                return True
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if re.match(
                    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Sym|.*Math)",
                    font,
                ):
                    return True
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]
                        or ord(char[0]) in range(0x370, 0x400)
                    )
                ):
                    return True
            return False

        # A. Parse original page layout
        for child in ltpage:
            if isinstance(child, LTChar):
                cur_v = False
                layout = None
                page_id = getattr(ltpage, "pageid", 0)
                if isinstance(self.layout, dict):
                    layout = self.layout.get(page_id)
                elif isinstance(self.layout, (list, tuple)) and 0 <= page_id < len(self.layout):
                    layout = self.layout[page_id]
                if layout is None:
                    h = max(1, int(getattr(ltpage, "height", 100)) + 1)
                    w = max(1, int(getattr(ltpage, "width", 100)) + 1)
                    layout = np.ones((h, w), dtype=int)

                h, w = layout.shape
                cx = int(np.clip(int(child.x0), 0, w - 1))
                cy = int(np.clip(int(child.y0), 0, h - 1))
                cls = layout[cy, cx]

                if child.get_text() == "•":
                    cls = 0

                child_matrix = getattr(child, "matrix", (1, 0, 0, 1, 0, 0))
                if (
                    cls == 0
                    or (
                        cls == xt_cls
                        and sstk
                        and len(sstk[-1].strip()) > 1
                        and pstk
                        and child.size < pstk[-1].size * 0.79
                        and xt is not None
                        and child.x0 >= xt.x0
                        and abs(child.y0 - xt.y0) < pstk[-1].size * 0.8
                    )
                    or vflag(getattr(child, "fontname", ""), child.get_text())
                    or (child_matrix[0] == 0 and child_matrix[3] == 0)
                ):
                    cur_v = True

                if not cur_v:
                    if vstk and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1

                if (
                    not cur_v
                    or cls != xt_cls
                    or (
                        sstk
                        and sstk[-1] != ""
                        and xt is not None
                        and abs(child.x0 - xt.x0) > vmax
                    )
                ):
                    if vstk:
                        if (
                            not cur_v
                            and cls == xt_cls
                            and child.x0 > max([vch.x0 for vch in vstk])
                        ):
                            vfix = vstk[0].y0 - child.y0
                        if sstk and sstk[-1] == "":
                            xt_cls = -1
                        if sstk:
                            sstk[-1] += f"{{v{len(var)}}}"
                        else:
                            sstk.append(f"{{v{len(var)}}}")
                            pstk.append(
                                Paragraph(
                                    get_char_baseline_y(child),
                                    child.x0,
                                    child.x0,
                                    child.x0,
                                    child.y0,
                                    child.y1,
                                    child.size,
                                    False,
                                )
                            )
                        var.append(vstk)
                        varl.append(vlstk)
                        varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0

                if not vstk:
                    font_size_shifted = (
                        xt is not None
                        and (
                            abs(child.size - xt.size) > 1.2
                            or (
                                xt.size > 0
                                and (
                                    child.size / xt.size > 1.25
                                    or child.size / xt.size < 0.8
                                )
                            )
                        )
                    )
                    vertical_gap_large = (
                        xt is not None
                        and child.x1 < xt.x0
                        and (xt.y0 - child.y1) > 1.8 * max(child.size, xt.size)
                    )
                    if (
                        cls == xt_cls
                        and sstk
                        and pstk
                        and xt is not None
                        and not font_size_shifted
                        and not vertical_gap_large
                    ):
                        if child.x0 > xt.x1 + 1:
                            sstk[-1] += " "
                        elif child.x1 < xt.x0:
                            sstk[-1] += " "
                            pstk[-1].brk = True
                    else:
                        sstk.append("")
                        pstk.append(
                            Paragraph(
                                get_char_baseline_y(child),
                                child.x0,
                                child.x0,
                                child.x0,
                                child.y0,
                                child.y1,
                                child.size,
                                False,
                            )
                        )

                if not cur_v:
                    if sstk and pstk:
                        if (
                            child.size > pstk[-1].size
                            or len(sstk[-1].strip()) == 1
                        ) and child.get_text() != " ":
                            if len(sstk[-1].strip()) == 1 and child.size > pstk[-1].size:
                                pstk[-1].y = get_char_baseline_y(child)
                            if child.size <= pstk[-1].size * 1.25:
                                pstk[-1].size = max(pstk[-1].size, child.size)
                        sstk[-1] += child.get_text()
                else:
                    if not vstk and cls == xt_cls and xt is not None and child.x0 > xt.x0:
                        vfix = child.y0 - xt.y0
                    vstk.append(child)

                if pstk:
                    pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                    pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                    pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                    pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                    ch_text = child.get_text().strip()
                    if ch_text:
                        graphicstate = getattr(child, "graphicstate", None)
                        ncolor = getattr(graphicstate, "ncolor", None)
                        if ncolor is not None:
                            pstk[-1].colors.append(ncolor)
                            if pstk[-1].color is None:
                                pstk[-1].color = ncolor
                xt = child
                xt_cls = cls

            elif isinstance(child, LTFigure):
                pass
            elif isinstance(child, LTLine):
                layout = None
                page_id = getattr(ltpage, "pageid", 0)
                if isinstance(self.layout, dict):
                    layout = self.layout.get(page_id)
                elif isinstance(self.layout, (list, tuple)) and 0 <= page_id < len(self.layout):
                    layout = self.layout[page_id]
                if layout is None:
                    h = max(1, int(getattr(ltpage, "height", 100)) + 1)
                    w = max(1, int(getattr(ltpage, "width", 100)) + 1)
                    layout = np.ones((h, w), dtype=int)
                h, w = layout.shape
                cx = int(np.clip(int(child.x0), 0, w - 1))
                cy = int(np.clip(int(child.y0), 0, h - 1))
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:
                    vlstk.append(child)
                else:
                    lstk.append(child)

        if vstk:
            if sstk:
                sstk[-1] += f"{{v{len(var)}}}"
            else:
                sstk.append(f"{{v{len(var)}}}")
                pstk.append(
                    Paragraph(
                        get_char_baseline_y(vstk[0]),
                        vstk[0].x0,
                        vstk[0].x0,
                        vstk[0].x0,
                        vstk[0].y0,
                        vstk[0].y1,
                        vstk[0].size,
                        False,
                        getattr(getattr(vstk[0], "graphicstate", None), "ncolor", None),
                    )
                )
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)

        for v in var:
            l = max([vch.x1 for vch in v]) - v[0].x0
            vlen.append(l)

        # B. Paragraph translation
        translation_sources: List[str] = []
        for index, source_text in enumerate(sstk):
            toc_entry = split_toc_entry(source_text)
            if toc_entry is not None and index < len(pstk):
                label, page_number = toc_entry
                pstk[index].toc_page_number = page_number
                translation_sources.append(label)
            else:
                translation_sources.append(source_text)

        def worker(s: str) -> str:
            if not s.strip() or re.match(r"^\{v\d+\}$", s.strip()):
                return s
            try:
                return self.translator.translate(s)
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e

        translate_worker = worker
        if not getattr(self.translator, "handles_retries", False):
            translate_worker = retry(
                wait=wait_fixed(1), stop=stop_after_attempt(3), reraise=True
            )(worker)

        max_workers = self.thread if (self.thread is not None and self.thread > 0) else None
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            news = list(executor.map(translate_worker, translation_sources))
        news = [unicodedata.normalize("NFC", t) for t in news]
        summary_lines = []
        for index, translated_text in enumerate(news):
            page_number = pstk[index].toc_page_number if index < len(pstk) else None
            summary_lines.append(
                f"{translated_text} … {page_number}"
                if page_number is not None
                else translated_text
            )
        self.last_page_text = "\n\n".join(summary_lines)

        # C. Typesetting
        def raw_string(fcur: Optional[str], cstk: str) -> str:
            if fcur == self.noto_name:
                if self.noto is not None and hasattr(self.noto, "has_glyph"):
                    return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
                else:
                    return "".join(["%04x" % ord(c) for c in cstk])
            elif fcur in self.fontmap and isinstance(self.fontmap[fcur], PDFCIDFont):
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        LANG_LINEHEIGHT_MAP = {
            "zh-cn": 1.4,
            "zh-tw": 1.4,
            "zh-hans": 1.4,
            "zh-hant": 1.4,
            "zh": 1.4,
            "ja": 1.1,
            "ko": 1.2,
            "en": 1.2,
            "vi": 1.2,
            "ar": 1.0,
            "ru": 0.8,
            "uk": 0.8,
            "ta": 0.8,
        }
        lang_out_val = getattr(self.translator, "lang_out", "vi")
        default_line_height = LANG_LINEHEIGHT_MAP.get(str(lang_out_val).lower(), 1.1)
        _x, _y = 0.0, 0.0
        ops_list: List[str] = []

        def gen_op_color(color: Any) -> str:
            if color is None:
                return "0 g "
            values = color if isinstance(color, (tuple, list)) else (color,)
            try:
                components = tuple(float(value) for value in values)
            except (TypeError, ValueError):
                return "0 g "
            if len(components) == 1:
                return f"{components[0]:.6f} g "
            if len(components) == 3:
                return " ".join(f"{value:.6f}" for value in components) + " rg "
            if len(components) == 4:
                return " ".join(f"{value:.6f}" for value in components) + " k "
            return "0 g "

        def gen_op_txt(
            font: str,
            size: float,
            x: float,
            y: float,
            rtxt: str,
            color: Any,
            horizontal_scale: float = 1.0,
        ) -> str:
            text_matrix = (
                "1 0 0 1"
                if abs(horizontal_scale - 1.0) < 1e-6
                else f"{horizontal_scale:.6f} 0 0 1"
            )
            return (
                f"{gen_op_color(color)}/{font} {size:f} Tf "
                f"{text_matrix} {x:f} {y:f} Tm [<{rtxt}>] TJ "
            )

        def gen_op_line(
            x: float, y: float, xlen: float, ylen: float, linewidth: float
        ) -> str:
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        def font_and_advance_at(ch: str, font_size: float) -> tuple[str, float]:
            font_name: Optional[str] = None
            if self.noto_name:
                try:
                    if (
                        self.noto is None
                        or not hasattr(self.noto, "has_glyph")
                        or self.noto.has_glyph(ord(ch))
                    ):
                        font_name = self.noto_name
                except Exception:
                    font_name = self.noto_name
            if font_name is None:
                try:
                    if (
                        "tiro" in self.fontmap
                        and self.fontmap["tiro"].to_unichr(ord(ch)) == ch
                    ):
                        font_name = "tiro"
                except Exception:
                    pass
            if font_name is None:
                font_name = self.noto_name or "tiro"

            if font_name == self.noto_name:
                if self.noto is not None and hasattr(self.noto, "char_lengths"):
                    advance = self.noto.char_lengths(ch, font_size)[0]
                else:
                    advance = font_size * 0.5
            elif font_name in self.fontmap and hasattr(
                self.fontmap[font_name], "char_width"
            ):
                advance = self.fontmap[font_name].char_width(ord(ch)) * font_size
            else:
                advance = font_size * 0.5
            return font_name, advance

        def text_width(text: str, font_size: float) -> float:
            return sum(font_and_advance_at(ch, font_size)[1] for ch in text)

        def append_text_runs(
            text: str,
            font_size: float,
            start_x: float,
            y: float,
            color: Any,
            horizontal_scale: float = 1.0,
        ) -> float:
            cursor = start_x
            run_font: Optional[str] = None
            run_chars: List[str] = []
            run_width = 0.0

            def flush_run() -> None:
                nonlocal cursor, run_font, run_chars, run_width
                if not run_chars or run_font is None:
                    return
                chars = "".join(run_chars)
                ops_list.append(
                    gen_op_txt(
                        run_font,
                        font_size,
                        cursor,
                        y,
                        raw_string(run_font, chars),
                        color,
                        horizontal_scale,
                    )
                )
                cursor += run_width * horizontal_scale
                run_chars = []
                run_width = 0.0

            for ch in text:
                font_name, advance = font_and_advance_at(ch, font_size)
                if run_font is not None and font_name != run_font:
                    flush_run()
                run_font = font_name
                run_chars.append(ch)
                run_width += advance
            flush_run()
            return cursor

        for id, new in enumerate(news):
            if id >= len(pstk):
                continue
            x: float = pstk[id].x
            y: float = pstk[id].y
            x0: float = pstk[id].x0
            x1: float = pstk[id].x1
            height: float = pstk[id].y1 - pstk[id].y0
            size: float = pstk[id].size
            brk: bool = pstk[id].brk
            cstk: str = ""
            fcur: Optional[str] = None
            lidx: int = 0
            tx: float = x
            fcur_ = fcur
            ptr: int = 0
            paragraph_color = resolve_paragraph_color(pstk[id])

            toc_page_number = pstk[id].toc_page_number
            if toc_page_number is not None:
                label = " ".join(new.split()) or translation_sources[id].strip()
                gap = max(2.0, size * 0.35)
                page_width = text_width(toc_page_number, size)
                dot_width = max(text_width(".", size), 0.1)
                page_x = max(x, x1 - page_width)
                minimum_leader_width = dot_width * 3
                available_label_width = max(
                    size,
                    page_x - x - (2 * gap) - minimum_leader_width,
                )

                label_size = size
                label_width = text_width(label, label_size)
                if label_width > available_label_width:
                    label_size = max(
                        size * 0.7,
                        size * available_label_width / max(label_width, 0.1),
                    )
                    label_width = text_width(label, label_size)
                label_scale = min(
                    1.0,
                    available_label_width / max(label_width, 0.1),
                )

                label_end = append_text_runs(
                    label,
                    label_size,
                    x,
                    y,
                    paragraph_color,
                    label_scale,
                )
                leader_start = label_end + gap
                leader_end = page_x - gap
                leader_count = max(
                    3,
                    int((leader_end - leader_start) / dot_width),
                )
                append_text_runs(
                    "." * leader_count,
                    size,
                    leader_start,
                    y,
                    paragraph_color,
                )
                append_text_runs(
                    toc_page_number,
                    size,
                    page_x,
                    y,
                    paragraph_color,
                )
                continue

            ops_vals: List[dict] = []

            def font_and_advance(ch: str) -> tuple[str, float]:
                return font_and_advance_at(ch, size)

            def measure_word(start: int) -> float:
                end = start
                width = 0.0
                while end < len(new) and not new[end].isspace():
                    if re.match(r"\{\s*v[\d\s]+\}", new[end:], re.IGNORECASE):
                        break
                    _, char_width = font_and_advance(new[end])
                    width += char_width
                    end += 1
                return width

            while ptr < len(new):
                vy_regex = re.match(r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE)
                mod: float = 0.0
                if vy_regex:
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue
                    if (
                        var[vid][-1].get_text()
                        and unicodedata.category(var[vid][-1].get_text()[0])
                        in ["Lm", "Mn", "Sk"]
                    ):
                        mod = getattr(var[vid][-1], "width", 0.0)
                else:
                    ch = new[ptr]
                    if (
                        brk
                        and not ch.isspace()
                        and (ptr == 0 or new[ptr - 1].isspace())
                        and x > x0
                        and x + measure_word(ptr) > x1 + 0.1 * size
                    ):
                        if cstk:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur or self.noto_name,
                                "size": size,
                                "x": tx,
                                "dy": 0.0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "color": paragraph_color,
                            })
                            cstk = ""
                        x = x0
                        lidx += 1
                    fcur_, adv = font_and_advance(ch)
                    ptr += 1

                if (
                    fcur_ != fcur
                    or vy_regex
                    or (brk and x + adv > x1 + 0.1 * size)
                ):
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur or self.noto_name,
                            "size": size,
                            "x": tx,
                            "dy": 0.0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "color": paragraph_color,
                        })
                        cstk = ""

                if brk and x + adv > x1 + 0.1 * size:
                    x = x0
                    lidx += 1

                if vy_regex:
                    fix: float = 0.0
                    if fcur is not None:
                        fix = varf[vid]
                    for vch in var[vid]:
                        vc = chr(
                            getattr(
                                vch,
                                "cid",
                                ord(vch.get_text()[:1]) if vch.get_text() else 32,
                            )
                        )
                        font_id_key = self.fontid.get(
                            getattr(vch, "font", None),
                            str(getattr(vch, "fontname", self.noto_name)),
                        )
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": font_id_key,
                            "size": vch.size,
                            "x": x + vch.x0 - var[vid][0].x0,
                            "dy": fix + vch.y0 - var[vid][0].y0,
                            "rtxt": raw_string(str(font_id_key), vc),
                            "lidx": lidx,
                            "color": getattr(
                                getattr(vch, "graphicstate", None),
                                "ncolor",
                                paragraph_color,
                            ),
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(
                                LTLine(
                                    0.1,
                                    (_x, _y),
                                    (
                                        x + vch.x0 - var[vid][0].x0,
                                        fix + y + vch.y0 - var[vid][0].y0,
                                    ),
                                )
                            )
                            _x, _y = (
                                x + vch.x0 - var[vid][0].x0,
                                fix + y + vch.y0 - var[vid][0].y0,
                            )
                    for l in varl[vid]:
                        if l.linewidth < 5:
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l.pts[0][0] + x - var[vid][0].x0,
                                "dy": l.pts[0][1] + fix - var[vid][0].y0,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": lidx,
                            })
                else:
                    if not cstk:
                        tx = x
                        if x == x0 and ch == " ":
                            adv = 0.0
                        else:
                            cstk += ch
                    else:
                        cstk += ch

                adv -= mod
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y

            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur or self.noto_name,
                    "size": size,
                    "x": tx,
                    "dy": 0.0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx,
                    "color": paragraph_color,
                })

            line_height = default_line_height
            while (lidx + 1) * size * line_height > height and line_height >= 1:
                line_height -= 0.05

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(
                        gen_op_txt(
                            vals["font"],
                            vals["size"],
                            vals["x"],
                            vals["dy"] + y - vals["lidx"] * size * line_height,
                            vals["rtxt"],
                            vals["color"],
                        )
                    )
                elif vals["type"] == OpType.LINE:
                    ops_list.append(
                        gen_op_line(
                            vals["x"],
                            vals["dy"] + y - vals["lidx"] * size * line_height,
                            vals["xlen"],
                            vals["ylen"],
                            vals["linewidth"],
                        )
                    )

        for l in lstk:
            if l.linewidth < 5:
                ops_list.append(
                    gen_op_line(
                        l.pts[0][0],
                        l.pts[0][1],
                        l.pts[1][0] - l.pts[0][0],
                        l.pts[1][1] - l.pts[0][1],
                        l.linewidth,
                    )
                )

        ops = f"BT {''.join(ops_list)}ET "
        return ops


def refine_toc_row_layout(page: Any, layout: np.ndarray) -> np.ndarray:
    """Give each dotted-leader TOC row its own paragraph class."""
    height, width = layout.shape
    try:
        page_dict = page.get_text("dict")
        page_rect = page.rect
        scale_x = width / max(float(page_rect.width), 1.0)
        scale_y = height / max(float(page_rect.height), 1.0)
    except Exception as exc:
        log.warning("Could not refine table-of-contents rows: %s", exc)
        return layout

    next_class = max(2, int(np.max(layout)) + 1)
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [
                span
                for span in line.get("spans", [])
                if str(span.get("text", "")).strip()
            ]
            if not spans:
                continue
            line_text = "".join(str(span.get("text", "")) for span in spans)
            if split_toc_entry(line_text) is None:
                continue

            x0 = min(float(span["bbox"][0]) for span in spans)
            y0 = min(float(span["bbox"][1]) for span in spans)
            x1 = max(float(span["bbox"][2]) for span in spans)
            y1 = max(float(span["bbox"][3]) for span in spans)
            pixel_x0 = (x0 - float(page_rect.x0)) * scale_x
            pixel_x1 = (x1 - float(page_rect.x0)) * scale_x
            pixel_y0 = (y0 - float(page_rect.y0)) * scale_y
            pixel_y1 = (y1 - float(page_rect.y0)) * scale_y

            left = int(np.clip(np.floor(pixel_x0 - 1), 0, width - 1))
            right = int(np.clip(np.ceil(pixel_x1 + 1), 1, width))
            bottom = int(np.clip(np.floor(height - pixel_y1), 0, height - 1))
            top = int(np.clip(np.ceil(height - pixel_y0), 1, height))
            if right <= left or top <= bottom:
                continue

            layout[bottom:top, left:right] = next_class
            next_class += 1

    return layout


def refine_table_layout(page: Any, layout: np.ndarray) -> np.ndarray:
    """Assign a unique paragraph class to each table cell detected by PyMuPDF.

    Without cell-level segmentation, all cells in a table share the same layout
    class, causing the converter to merge text across multiple columns and rows
    into one long paragraph. Giving each cell a distinct class ensures each cell's
    text is translated and positioned inside its own cell boundaries.
    """
    if not hasattr(page, "find_tables"):
        return layout

    height, width = layout.shape
    try:
        table_finder = page.find_tables()
        tabs = getattr(table_finder, "tables", [])
    except Exception as exc:
        log.warning("Could not extract tables for layout refinement: %s", exc)
        return layout

    if not tabs:
        return layout

    try:
        page_rect = page.rect
        scale_x = width / max(float(page_rect.width), 1.0)
        scale_y = height / max(float(page_rect.height), 1.0)
        page_x0 = float(page_rect.x0)
        page_y0 = float(page_rect.y0)
    except Exception:
        scale_x = 1.0
        scale_y = 1.0
        page_x0 = 0.0
        page_y0 = 0.0

    next_class = max(2, int(np.max(layout)) + 1)
    for t in tabs:
        cells = getattr(t, "cells", None)
        if not cells:
            continue
        for cell in cells:
            x0, y0, x1, y1 = cell
            px0 = (x0 - page_x0) * scale_x
            px1 = (x1 - page_x0) * scale_x
            py0 = (y0 - page_y0) * scale_y
            py1 = (y1 - page_y0) * scale_y

            left = int(np.clip(np.floor(px0), 0, width - 1))
            right = int(np.clip(np.ceil(px1), 1, width))
            bottom = int(np.clip(np.floor(height - py1), 0, height - 1))
            top = int(np.clip(np.ceil(height - py0), 1, height))
            if right > left and top > bottom:
                layout[bottom:top, left:right] = next_class
                next_class += 1

    return layout


def build_text_block_layout(page: Any, height: int, width: int) -> np.ndarray:
    """Build a safe layout mask from non-empty PyMuPDF text blocks.

    PDFMiner expects bottom-left coordinates while PyMuPDF text boxes use a
    top-left origin.  Each text block receives its own class so the converter
    cannot merge a cover-page header, title, subtitle and footer into one
    paragraph when DocLayout is unavailable.
    """
    layout = np.ones((height, width), dtype=int)
    try:
        page_dict = page.get_text("dict")
        page_rect = page.rect
        scale_x = width / max(float(page_rect.width), 1.0)
        scale_y = height / max(float(page_rect.height), 1.0)
    except Exception as exc:
        log.warning("Could not build text-block fallback layout: %s", exc)
        return layout

    next_class = 2
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:
            continue

        spans = [
            span
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if str(span.get("text", "")).strip()
        ]
        if not spans:
            continue

        x0 = min(float(span["bbox"][0]) for span in spans)
        y0 = min(float(span["bbox"][1]) for span in spans)
        x1 = max(float(span["bbox"][2]) for span in spans)
        y1 = max(float(span["bbox"][3]) for span in spans)

        pixel_x0 = (x0 - float(page_rect.x0)) * scale_x
        pixel_x1 = (x1 - float(page_rect.x0)) * scale_x
        pixel_y0 = (y0 - float(page_rect.y0)) * scale_y
        pixel_y1 = (y1 - float(page_rect.y0)) * scale_y

        left = int(np.clip(np.floor(pixel_x0 - 1), 0, width - 1))
        right = int(np.clip(np.ceil(pixel_x1 + 1), 1, width))
        bottom = int(np.clip(np.floor(height - pixel_y1 - 1), 0, height - 1))
        top = int(np.clip(np.ceil(height - pixel_y0 + 1), 1, height))
        if right <= left or top <= bottom:
            continue

        layout[bottom:top, left:right] = next_class
        next_class += 1

    layout = refine_table_layout(page, layout)
    return refine_toc_row_layout(page, layout)


def build_page_layout(page: Any, pix: Any, model: Optional[Any]) -> np.ndarray:
    """Build a DocLayout mask, falling back to native PDF text blocks."""
    height, width = pix.height, pix.width
    if model is None:
        return build_text_block_layout(page, height, width)

    layout = np.ones((height, width), dtype=int)
    try:
        image = np.frombuffer(pix.samples, np.uint8).reshape(
            height, width, 3
        )[:, :, ::-1]
        image_size = max(32, int(height / 32) * 32)
        results = model.predict(image, imgsz=image_size)
    except Exception as exc:
        log.warning("DocLayout prediction failed; using text-block fallback: %s", exc)
        return build_text_block_layout(page, height, width)

    if not results:
        return build_text_block_layout(page, height, width)

    page_layout = results[0]
    frozen_classes = {
        "abandon",
        "figure",
        "table",
        "isolate_formula",
        "formula_caption",
    }
    boxes = getattr(page_layout, "boxes", [])
    names = getattr(page_layout, "names", {})
    if boxes is None or len(boxes) == 0:
        return build_text_block_layout(page, height, width)

    def class_name(item: Any) -> str:
        class_id = int(item.cls)
        if isinstance(names, dict):
            return names.get(class_id, "")
        return names[class_id] if class_id < len(names) else ""

    def clipped_box(item: Any) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = np.squeeze(item.xyxy)
        return (
            int(np.clip(int(x0 - 1), 0, width - 1)),
            int(np.clip(int(height - y1 - 1), 0, height - 1)),
            int(np.clip(int(x1 + 1), 0, width)),
            int(np.clip(int(height - y0 + 1), 0, height)),
        )

    valid_boxes = []
    for item in boxes:
        x0, y0, x1, y1 = clipped_box(item)
        box_w = abs(x1 - x0)
        box_h = abs(y1 - y0)
        if class_name(item) in frozen_classes and box_w >= 0.85 * width and box_h >= 0.80 * height:
            log.warning(
                "Ignoring full-page false-positive %s box (%dx%d on %dx%d page)",
                class_name(item),
                box_w,
                box_h,
                width,
                height,
            )
            continue
        valid_boxes.append((item, (x0, y0, x1, y1)))

    if not any(class_name(item) not in frozen_classes for item, _ in valid_boxes):
        return build_text_block_layout(page, height, width)

    for index, (item, (x0, y0, x1, y1)) in enumerate(valid_boxes):
        if class_name(item) not in frozen_classes:
            layout[y0:y1, x0:x1] = index + 2

    for item, (x0, y0, x1, y1) in valid_boxes:
        if class_name(item) in frozen_classes:
            layout[y0:y1, x0:x1] = 0

    try:
        page_dict = page.get_text("dict")
        page_rect = page.rect
        scale_x = width / max(float(page_rect.width), 1.0)
        scale_y = height / max(float(page_rect.height), 1.0)
        next_class = max(2, int(np.max(layout)) + 1)
        for block in page_dict.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            spans = [
                s
                for l in block.get("lines", [])
                for s in l.get("spans", [])
                if str(s.get("text", "")).strip()
            ]
            if not spans:
                continue
            x0 = min(float(s["bbox"][0]) for s in spans)
            y0 = min(float(s["bbox"][1]) for s in spans)
            x1 = max(float(s["bbox"][2]) for s in spans)
            y1 = max(float(s["bbox"][3]) for s in spans)
            px0 = (x0 - float(page_rect.x0)) * scale_x
            px1 = (x1 - float(page_rect.x0)) * scale_x
            py0 = (y0 - float(page_rect.y0)) * scale_y
            py1 = (y1 - float(page_rect.y0)) * scale_y
            left = int(np.clip(np.floor(px0 - 1), 0, width - 1))
            right = int(np.clip(np.ceil(px1 + 1), 1, width))
            bottom = int(np.clip(np.floor(height - py1 - 1), 0, height - 1))
            top = int(np.clip(np.ceil(height - py0 + 1), 1, height))
            if right > left and top > bottom:
                layout[bottom:top, left:right] = next_class
                next_class += 1
    except Exception as exc:
        log.warning("Could not overlay native text blocks on layout: %s", exc)

    layout = refine_table_layout(page, layout)
    return refine_toc_row_layout(page, layout)


def patch_page(
    page: Any,
    model: Optional[Any] = None,
    converter: Optional[TranslateConverter] = None,
) -> str:
    """
    Perform layout analysis and translate/patch PDF page content.
    Supports PyMuPDF fitz.Page, PDFMiner PDFPage, or LTPage.
    Returns the patched PDF stream operator string.
    """
    if converter is None:
        raise ValueError("A TranslateConverter instance must be provided to patch_page")

    # If it's directly an LTPage:
    if isinstance(page, LTPage):
        return converter.receive_layout(page)

    # PyMuPDF Page (has parent and get_pixmap)
    if hasattr(page, "parent") and hasattr(page, "get_pixmap"):
        pix = page.get_pixmap()
        box = build_page_layout(page, pix, model)

        page_no = getattr(page, "number", 0)
        converter.layout[page_no] = box

        pdf_bytes = page.parent.tobytes()
        parser = PDFParser(io.BytesIO(pdf_bytes))
        miner_doc = PDFDocument(parser)
        miner_pages = list(PDFPage.create_pages(miner_doc))
        target_miner_page = None
        for pno, mp in enumerate(miner_pages):
            if pno == page_no:
                target_miner_page = mp
                target_miner_page.pageno = pno
                break
        if target_miner_page is None and miner_pages:
            target_miner_page = miner_pages[0]
            target_miner_page.pageno = page_no

        obj_patch: Dict[Any, str] = {}
        interpreter = PDFPageInterpreterEx(converter.rsrcmgr, converter, obj_patch)
        interpreter.process_page(target_miner_page)
        return obj_patch.get(getattr(target_miner_page, "page_xref", page_no), "")

    # PDFMiner PDFPage
    if hasattr(page, "cropbox"):
        pno = getattr(page, "pageno", 0)
        if pno not in converter.layout:
            h = int(abs(page.cropbox[3] - page.cropbox[1]))
            w = int(abs(page.cropbox[2] - page.cropbox[0]))
            converter.layout[pno] = np.ones((max(1, h), max(1, w)), dtype=int)
        obj_patch = {}
        interpreter = PDFPageInterpreterEx(converter.rsrcmgr, converter, obj_patch)
        interpreter.process_page(page)
        return obj_patch.get(getattr(page, "page_xref", pno), "")

    raise TypeError(f"Unsupported page type for patch_page: {type(page)}")
