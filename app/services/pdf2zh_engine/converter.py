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
from tenacity import retry, stop_after_attempt, wait_fixed

from app.services.pdf2zh_engine.adapter import BaseTranslator, create_translator
from app.services.pdf2zh_engine.pdfinterp import PDFPageInterpreterEx

log = logging.getLogger(__name__)


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
    ) -> None:
        self.y: float = y
        self.x: float = x
        self.x0: float = x0
        self.x1: float = x1
        self.y0: float = y0
        self.y1: float = y1
        self.size: float = size
        self.brk: bool = brk


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
                source_lang=lang_in or "en",
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
                    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math)",
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
                                    child.y0,
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
                    if cls == xt_cls and sstk and pstk and xt is not None:
                        if child.x0 > xt.x1 + 1:
                            sstk[-1] += " "
                        elif child.x1 < xt.x0:
                            sstk[-1] += " "
                            pstk[-1].brk = True
                    else:
                        sstk.append("")
                        pstk.append(
                            Paragraph(
                                child.y0,
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
                            pstk[-1].y -= child.size - pstk[-1].size
                            pstk[-1].size = child.size
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
                        vstk[0].y0,
                        vstk[0].x0,
                        vstk[0].x0,
                        vstk[0].x0,
                        vstk[0].y0,
                        vstk[0].y1,
                        vstk[0].size,
                        False,
                    )
                )
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)

        for v in var:
            l = max([vch.x1 for vch in v]) - v[0].x0
            vlen.append(l)

        # B. Paragraph translation
        @retry(wait=wait_fixed(1), stop=stop_after_attempt(3), reraise=True)
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

        max_workers = self.thread if (self.thread is not None and self.thread > 0) else None
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            news = list(executor.map(worker, sstk))

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

        def gen_op_txt(font: str, size: float, x: float, y: float, rtxt: str) -> str:
            return f"/{font} {size:f} Tf 1 0 0 1 {x:f} {y:f} Tm [<{rtxt}>] TJ "

        def gen_op_line(
            x: float, y: float, xlen: float, ylen: float, linewidth: float
        ) -> str:
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

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

            ops_vals: List[dict] = []

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
                    fcur_ = None
                    try:
                        if (
                            fcur_ is None
                            and "tiro" in self.fontmap
                            and self.fontmap["tiro"].to_unichr(ord(ch)) == ch
                        ):
                            fcur_ = "tiro"
                    except Exception:
                        pass
                    if fcur_ is None:
                        fcur_ = self.noto_name
                    if fcur_ == self.noto_name:
                        if self.noto is not None and hasattr(self.noto, "char_lengths"):
                            adv = self.noto.char_lengths(ch, size)[0]
                        else:
                            adv = size * 0.5
                    else:
                        if fcur_ in self.fontmap and hasattr(self.fontmap[fcur_], "char_width"):
                            adv = self.fontmap[fcur_].char_width(ord(ch)) * size
                        else:
                            adv = size * 0.5
                    ptr += 1

                if (
                    fcur_ != fcur
                    or vy_regex
                    or x + adv > x1 + 0.1 * size
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
        h, w = pix.height, pix.width
        box = np.ones((h, w), dtype=int)

        if model is not None:
            image = np.frombuffer(pix.samples, np.uint8).reshape(h, w, 3)[:, :, ::-1]
            imgsz = max(32, int(h / 32) * 32)
            results = model.predict(image, imgsz=imgsz)
            if results:
                page_layout = results[0]
                vcls = {"abandon", "figure", "table", "isolate_formula", "formula_caption"}
                boxes = getattr(page_layout, "boxes", [])
                names = getattr(page_layout, "names", {})

                for i, d in enumerate(boxes):
                    cls_id = int(d.cls)
                    cls_name = names.get(cls_id, "") if isinstance(names, dict) else (
                        names[cls_id] if cls_id < len(names) else ""
                    )
                    if cls_name not in vcls:
                        x0, y0, x1, y1 = np.squeeze(d.xyxy)
                        x0, y0, x1, y1 = (
                            np.clip(int(x0 - 1), 0, w - 1),
                            np.clip(int(h - y1 - 1), 0, h - 1),
                            np.clip(int(x1 + 1), 0, w - 1),
                            np.clip(int(h - y0 + 1), 0, h - 1),
                        )
                        box[y0:y1, x0:x1] = i + 2

                for i, d in enumerate(boxes):
                    cls_id = int(d.cls)
                    cls_name = names.get(cls_id, "") if isinstance(names, dict) else (
                        names[cls_id] if cls_id < len(names) else ""
                    )
                    if cls_name in vcls:
                        x0, y0, x1, y1 = np.squeeze(d.xyxy)
                        x0, y0, x1, y1 = (
                            np.clip(int(x0 - 1), 0, w - 1),
                            np.clip(int(h - y1 - 1), 0, h - 1),
                            np.clip(int(x1 + 1), 0, w - 1),
                            np.clip(int(h - y0 + 1), 0, h - 1),
                        )
                        box[y0:y1, x0:x1] = 0

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
