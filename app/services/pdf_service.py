import base64
from pathlib import Path
try:
    import pymupdf as fitz
except ImportError:
    import fitz

def parse_page_ranges(range_str: str, total_pages: int) -> list[int]:
    if not range_str or range_str.strip().lower() in ["all", ""]:
        return list(range(total_pages))
    
    pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start, end = int(sub[0]), int(sub[1])
                for p in range(start, end + 1):
                    if 1 <= p <= total_pages:
                        pages.add(p - 1)
        elif part.isdigit():
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
                
    result = sorted(list(pages))
    return result if result else list(range(total_pages))

def get_pdf_metadata(file_path: Path) -> dict:
    doc = fitz.open(str(file_path))
    total_pages = len(doc)
    doc.close()
    file_size = file_path.stat().st_size
    return {
        "filename": file_path.name,
        "total_pages": total_pages,
        "file_size": file_size,
    }

def render_page_image(page: fitz.Page, zoom: float = 1.5) -> str:
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"

def extract_page_content(file_path: Path, page_index: int) -> dict:
    doc = fitz.open(str(file_path))
    if page_index < 0 or page_index >= len(doc):
        doc.close()
        raise IndexError(f"Page index {page_index} out of range (0-{len(doc)-1})")
    
    page = doc[page_index]
    text = page.get_text("text").strip()
    image_b64 = render_page_image(page)
    doc.close()
    
    return {
        "page_number": page_index + 1,
        "page_index": page_index,
        "text": text,
        "image_base64": image_b64,
    }
