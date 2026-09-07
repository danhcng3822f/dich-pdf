import asyncio
import base64
import io
import logging
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import numpy as np
import pymupdf
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from app.services.pdf2zh_engine.adapter import BaseTranslator
from app.services.pdf2zh_engine.converter import TranslateConverter
from app.services.pdf2zh_engine.doclayout import load_layout_model
from app.services.pdf2zh_engine.font_manager import get_font_path
from app.services.pdf2zh_engine.pdfinterp import PDFPageInterpreterEx

logger = logging.getLogger(__name__)


def render_pixmap_base64(page: pymupdf.Page, zoom: float = 1.5) -> str:
    """
    Render a PyMuPDF Page to a base64 PNG data URL string.
    """
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def create_dual_pdf(
    original_pdf_path: Union[str, Path],
    translated_pdf_path: Union[str, Path],
    output_path: Union[str, Path],
    page_indices: Optional[List[int]] = None,
) -> Path:
    """
    Combine original and translated PDFs into an interleaved dual PDF.
    Page order: Page 1 Orig, Page 1 Trans, Page 2 Orig, Page 2 Trans, ...
    """
    original_pdf_path = Path(original_pdf_path)
    translated_pdf_path = Path(translated_pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc_orig = pymupdf.open(str(original_pdf_path))
    doc_trans = pymupdf.open(str(translated_pdf_path))
    doc_dual = pymupdf.open()

    try:
        orig_count = len(doc_orig)
        trans_count = len(doc_trans)

        if page_indices is not None:
            if trans_count == len(page_indices):
                for idx, orig_pno in enumerate(page_indices):
                    if 0 <= orig_pno < orig_count:
                        doc_dual.insert_pdf(doc_orig, from_page=orig_pno, to_page=orig_pno)
                        doc_dual.insert_pdf(doc_trans, from_page=idx, to_page=idx)
            else:
                for orig_pno in page_indices:
                    if 0 <= orig_pno < orig_count and 0 <= orig_pno < trans_count:
                        doc_dual.insert_pdf(doc_orig, from_page=orig_pno, to_page=orig_pno)
                        doc_dual.insert_pdf(doc_trans, from_page=orig_pno, to_page=orig_pno)
        else:
            for pno in range(orig_count):
                doc_dual.insert_pdf(doc_orig, from_page=pno, to_page=pno)
                if pno < trans_count:
                    doc_dual.insert_pdf(doc_trans, from_page=pno, to_page=pno)

        doc_dual.save(str(output_path), deflate=True, garbage=3)
    finally:
        for d in (doc_dual, doc_orig, doc_trans):
            if not d.is_closed:
                d.close()

    return output_path


async def process_pdf2zh_stream(
    file_path: Union[str, Path],
    page_indices: Optional[List[int]],
    target_lang: str,
    translator: BaseTranslator,
    mono_out_path: Union[str, Path],
    dual_out_path: Union[str, Path],
    thread: int = 2,
    cancellation_event: Optional[asyncio.Event] = None,
    zoom: float = 1.5,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Asynchronous generator processing PDF pages via pdf2zh pipeline.
    Yields 'page_progress' and 'page_completed' events for each page,
    and a final 'completed' event upon generating mono and dual PDFs.
    """
    file_path = Path(file_path)
    mono_out_path = Path(mono_out_path)
    dual_out_path = Path(dual_out_path)

    mono_out_path.parent.mkdir(parents=True, exist_ok=True)
    dual_out_path.parent.mkdir(parents=True, exist_ok=True)

    doc_orig = pymupdf.open(str(file_path))
    doc_zh = None
    try:
        total_doc_pages = len(doc_orig)

        if page_indices is None:
            target_pages = list(range(total_doc_pages))
        else:
            target_pages = [p for p in page_indices if 0 <= p < total_doc_pages]

        total_selected = len(target_pages)

        # Clone document for translation
        doc_zh = pymupdf.open(stream=doc_orig.tobytes(), filetype="pdf")

        # Embed Tiro and Noto fonts into doc_zh (with graceful fallback)
        try:
            font_path = get_font_path(target_lang)
            noto_name = "noto"
            noto_font = pymupdf.Font(noto_name, font_path)
            font_list = [("tiro", None), (noto_name, font_path)]
        except Exception as font_err:
            logger.warning("Could not load font for %s: %s. Using helv fallback.", target_lang, font_err)
            noto_name = "helv"
            noto_font = pymupdf.Font("helv")
            font_list = [("tiro", None), ("helv", None)]

        font_id = {}
        for page in doc_zh:
            for font in font_list:
                font_id[font[0]] = page.insert_font(font[0], font[1])

        # Bind fonts into resource dictionaries across PDF xrefs
        xreflen = doc_zh.xref_length()
        for xref in range(1, xreflen):
            for label in ["Resources/", ""]:
                try:
                    font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                    target_key_prefix = f"{label}Font/"
                    target_xref = xref
                    if font_res[0] == "xref":
                        m = re.search(r"(\d+) 0 R", font_res[1])
                        if m:
                            target_xref = int(m.group(1))
                            font_res = ("dict", doc_zh.xref_object(target_xref))
                            target_key_prefix = ""

                    if font_res[0] == "dict":
                        for font in font_list:
                            target_key = f"{target_key_prefix}{font[0]}"
                            font_exist = doc_zh.xref_get_key(target_xref, target_key)
                            if font_exist[0] == "null":
                                doc_zh.xref_set_key(
                                    target_xref,
                                    target_key,
                                    f"{font_id[font[0]]} 0 R",
                                )
                except Exception:
                    pass

        # Initialize PDFMiner parser from font-injected doc_zh bytes
        fp = io.BytesIO(doc_zh.tobytes())
        parser = PDFParser(fp)
        miner_doc = PDFDocument(parser)
        miner_pages = list(PDFPage.create_pages(miner_doc))

        model = load_layout_model()
        rsrcmgr = PDFResourceManager()
        layout: Dict[int, Any] = {}
        converter = TranslateConverter(
            rsrcmgr,
            thread=thread,
            layout=layout,
            lang_in=getattr(translator, "lang_in", "auto"),
            lang_out=target_lang,
            translator=translator,
            noto_name=noto_name,
            noto=noto_font,
        )
        obj_patch: Dict[int, str] = {}
        interpreter = PDFPageInterpreterEx(rsrcmgr, converter, obj_patch)

        for seq_idx, pno in enumerate(target_pages):
            converter.last_page_text = ""
            if cancellation_event and cancellation_event.is_set():
                raise asyncio.CancelledError("PDF translation task cancelled")

            # 1. Yield page progress event
            yield {
                "event": "page_progress",
                "page_number": pno + 1,
                "current_index": seq_idx + 1,
                "total_pages": total_selected,
            }

            # 2. Render original page image
            orig_b64 = render_pixmap_base64(doc_orig[pno], zoom=zoom)

            # Allow async event loop to breathe
            await asyncio.sleep(0)

            # 3. Predict page layout (with fallback if model is None)
            page = doc_zh[pno]
            pix = page.get_pixmap()
            box = np.ones((pix.height, pix.width), dtype=int)
            h, w = box.shape

            if model is not None:
                image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]
                imgsz = max(32, int(pix.height / 32) * 32)
                results = model.predict(image, imgsz=imgsz)
                vcls = {"abandon", "figure", "table", "isolate_formula", "formula_caption"}
                if results:
                    pl = results[0]
                    boxes = getattr(pl, "boxes", [])
                    names = getattr(pl, "names", {})
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

            converter.layout[pno] = box

            # 4. Prepare stream xref on page in doc_zh
            page_xref = doc_zh.get_new_xref()
            doc_zh.update_object(page_xref, "<<>>")
            doc_zh.update_stream(page_xref, b"")
            doc_zh[pno].set_contents(page_xref)

            mp = miner_pages[pno]
            mp.pageno = pno
            mp.page_xref = page_xref

            # 5. Process page translation and PDF patching
            await asyncio.to_thread(interpreter.process_page, mp)

            # 6. Update new stream operators into doc_zh
            ops_new = obj_patch.get(page_xref, "")
            doc_zh.update_stream(page_xref, ops_new.encode("latin1"))

            # 7. Render translated page image
            trans_b64 = render_pixmap_base64(doc_zh[pno], zoom=zoom)
            text_summary = getattr(converter, "last_page_text", "")

            # 8. Yield page completed event
            yield {
                "event": "page_completed",
                "page_number": pno + 1,
                "current_index": seq_idx + 1,
                "total_pages": total_selected,
                "original_image": orig_b64,
                "translated_image": trans_b64,
                "translated_text": text_summary or "",
            }

        # Font subsetting (optional fallback)
        try:
            import importlib.util
            if importlib.util.find_spec("fontTools.subset") is not None:
                doc_zh.subset_fonts(fallback=True)
        except Exception:
            pass

        # Save mono translated PDF
        doc_zh.save(str(mono_out_path), deflate=True, garbage=3)
        doc_zh.close()
        doc_orig.close()

        # Generate interleaved dual PDF
        create_dual_pdf(file_path, mono_out_path, dual_out_path, page_indices=target_pages)

        # 9. Yield completion event
        yield {
            "event": "completed",
            "mono_pdf": str(mono_out_path),
            "dual_pdf": str(dual_out_path),
            "total_pages": total_selected,
        }
    finally:
        if doc_zh is not None and not doc_zh.is_closed:
            doc_zh.close()
        if not doc_orig.is_closed:
            doc_orig.close()
