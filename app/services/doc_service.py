from pathlib import Path
import os
try:
    import pymupdf as fitz
except ImportError:
    import fitz
from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab.rl_config

def _get_unicode_font_name() -> str:
    """Find and register a system Unicode font (Arial, Tahoma, Times New Roman, or Segoe UI) on Windows/Linux."""
    font_name = "Helvetica"
    
    # Common Windows font paths
    win_fonts = [
        ("Arial", "C:/Windows/Fonts/arial.ttf"),
        ("SegoeUI", "C:/Windows/Fonts/segoeui.ttf"),
        ("Tahoma", "C:/Windows/Fonts/tahoma.ttf"),
        ("TimesNewRoman", "C:/Windows/Fonts/times.ttf"),
    ]
    for name, path in win_fonts:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except Exception:
                continue
    return font_name

def generate_docx(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    doc = Document()
    
    title = doc.add_heading(doc_title, level=0)
    title.alignment = 1 # Center
    
    for i, page in enumerate(translated_pages):
        page_num = page.get("page_number", i + 1)
        doc.add_heading(f"Trang {page_num}", level=2)
        
        text = page.get("translated_text", "")
        for line in text.split("\n"):
            line = line.strip()
            if line:
                p = doc.add_paragraph(line)
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.line_spacing = 1.15
        
        if i < len(translated_pages) - 1:
            doc.add_page_break()
            
    doc.save(str(output_path))
    return output_path

def generate_pdf(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    font_name = _get_unicode_font_name()
    
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=18,
        leading=22,
        alignment=1,
        spaceAfter=15
    )
    page_header_style = ParagraphStyle(
        'PageHeader',
        parent=styles['Heading2'],
        fontName=font_name,
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#2563EB"), # Tailwind Blue-600
        spaceBefore=10,
        spaceAfter=8
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10,
        leading=15,
        spaceAfter=6
    )
    
    story = [Paragraph(doc_title, title_style), Spacer(1, 10)]
    
    for i, page in enumerate(translated_pages):
        page_num = page.get("page_number", i + 1)
        story.append(Paragraph(f"--- Trang {page_num} ---", page_header_style))
        
        text = page.get("translated_text", "")
        for line in text.split("\n"):
            clean_line = line.strip().replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            if clean_line:
                story.append(Paragraph(clean_line, body_style))
        
        if i < len(translated_pages) - 1:
            story.append(PageBreak())
            
    doc.build(story)
    return output_path

def generate_markdown(translated_pages: list[dict], output_path: Path, doc_title: str = "Translated Document") -> Path:
    lines = [f"# {doc_title}\n\n"]
    for page in translated_pages:
        page_num = page.get("page_number", 1)
        lines.append(f"## Trang {page_num}\n\n")
        lines.append(f"{page.get('translated_text', '')}\n\n")
        lines.append("---\n\n")
        
    output_path.write_text("".join(lines), encoding="utf-8")
    return output_path
