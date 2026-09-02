import io
import json
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from app.config import settings
from app.services.pdf_service import get_pdf_metadata, parse_page_ranges, extract_page_content
from app.services.ai_service import AIConfig, translate_text, test_api_connection
from app.services.doc_service import generate_docx, generate_pdf, generate_markdown, generate_latex

router = APIRouter()

# Memory job cache for downloads
JOB_STORE: dict[str, dict] = {}

class TranslationStreamRequest(BaseModel):
    file_id: str
    target_lang: str = "Vietnamese"
    style: str = "Default"
    page_range: str = "all"
    ai_config: AIConfig

@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file định dạng PDF (.pdf)")
    
    file_id = str(uuid.uuid4())
    save_path = settings.upload_dir / f"{file_id}.pdf"
    
    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File vượt quá dung lượng cho phép ({settings.max_upload_size_mb}MB)")
    
    with open(save_path, "wb") as f:
        f.write(content)
        
    try:
        meta = get_pdf_metadata(save_path)
    except Exception as e:
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Không thể đọc file PDF: {str(e)}")
        
    return {
        "file_id": file_id,
        "filename": file.filename,
        "total_pages": meta["total_pages"],
        "file_size": meta["file_size"],
    }

@router.post("/test-connection")
async def test_connection(config: AIConfig):
    try:
        res = await test_api_connection(config)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Kết nối API thất bại: {str(e)}")

@router.post("/translate/stream")
async def translate_stream(req: TranslationStreamRequest):
    pdf_path = settings.upload_dir / f"{req.file_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="File PDF không tồn tại hoặc đã hết hạn")
    
    meta = get_pdf_metadata(pdf_path)
    page_indices = parse_page_ranges(req.page_range, meta["total_pages"])
    total_selected = len(page_indices)
    
    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {
        "filename": meta["filename"],
        "pages": []
    }
    
    async def event_generator():
        # Yield start event
        yield f"event: start\ndata: {json.dumps({'job_id': job_id, 'total_pages': total_selected, 'pages': [p + 1 for p in page_indices]})}\n\n"
        
        translated_pages = []
        for seq_idx, page_idx in enumerate(page_indices):
            page_num = page_idx + 1
            # Signal page processing
            yield f"event: page_progress\ndata: {json.dumps({'current_index': seq_idx + 1, 'total_pages': total_selected, 'page_number': page_num})}\n\n"
            
            try:
                page_data = extract_page_content(pdf_path, page_idx)
                raw_text = page_data["text"]
                
                if raw_text.strip():
                    translated_text = await translate_text(
                        raw_text,
                        target_lang=req.target_lang,
                        style=req.style,
                        config=req.ai_config
                    )
                else:
                    translated_text = "[Trang không có nội dung chữ hoặc là trang ảnh]"
                
                page_result = {
                    "page_number": page_num,
                    "original_text": raw_text,
                    "translated_text": translated_text,
                    "image_base64": page_data["image_base64"]
                }
                translated_pages.append(page_result)
                JOB_STORE[job_id]["pages"].append(page_result)
                
                yield f"event: page_completed\ndata: {json.dumps(page_result)}\n\n"
            except Exception as e:
                err_msg = str(e)
                error_result = {
                    "page_number": page_num,
                    "error": err_msg
                }
                yield f"event: page_error\ndata: {json.dumps(error_result)}\n\n"
                
        # Generate export files
        try:
            docx_path = settings.export_dir / f"{job_id}.docx"
            pdf_out_path = settings.export_dir / f"{job_id}.pdf"
            md_path = settings.export_dir / f"{job_id}.md"
            tex_path = settings.export_dir / f"{job_id}.tex"
            
            doc_title = f"Bản dịch - {req.target_lang}"
            generate_docx(translated_pages, docx_path, doc_title)
            generate_pdf(translated_pages, pdf_out_path, doc_title)
            generate_markdown(translated_pages, md_path, doc_title)
            generate_latex(translated_pages, tex_path, doc_title, is_beamer=(req.style == "LaTeX_Beamer"))
            
            yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'docx_ready': True, 'pdf_ready': True, 'md_ready': True, 'tex_ready': True})}\n\n"
        except Exception as e:
            yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'error': f'Lỗi xuất file: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/download/{job_id}/{fmt}")
async def download_file(job_id: str, fmt: str):
    fmt = fmt.lower()
    if fmt not in ["docx", "pdf", "md", "tex"]:
        raise HTTPException(status_code=400, detail="Định dạng không hỗ trợ (chỉ docx, pdf, md, tex)")
    
    file_path = settings.export_dir / f"{job_id}.{fmt}"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File xuất không tồn tại hoặc đã hết hạn")
    
    original_name = "document"
    if job_id in JOB_STORE:
        raw_name = Path(JOB_STORE[job_id]["filename"]).stem
        original_name = f"{raw_name}_translated"
        
    download_filename = f"{original_name}.{fmt}"
    
    media_types = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "md": "text/markdown",
        "tex": "application/x-tex"
    }
    
    return FileResponse(
        path=file_path,
        filename=download_filename,
        media_type=media_types.get(fmt, "application/octet-stream")
    )
