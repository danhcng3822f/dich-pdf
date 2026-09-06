import asyncio
import io
import json
import logging
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
from app.services.pdf2zh_engine.adapter import create_translator
from app.services.pdf2zh_engine.pipeline import process_pdf2zh_stream

logger = logging.getLogger(__name__)

router = APIRouter()

# Memory job cache for downloads
JOB_STORE: dict[str, dict] = {}

class TranslationStreamRequest(BaseModel):
    file_id: str
    target_lang: str = "Vietnamese"
    style: str = "Default"
    page_range: str = "all"
    engine_mode: str = "pdf2zh_layout"  # "pdf2zh_layout" | "beamer_slide"
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
    provider_norm = config.provider.lower()
    if provider_norm in ["google", "google_free", "bing", "bing_free"]:
        try:
            translator = create_translator(provider_norm, target_lang="vi")
            res = await asyncio.to_thread(translator.translate, "Hello")
            return {"status": "success", "sample_translation": res}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Kết nối dịch miễn phí thất bại: {str(e)}")
    
    if not config.api_key or not config.api_key.strip():
        raise HTTPException(status_code=400, detail="API key là bắt buộc cho nhà cung cấp này")

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
        "pages": [],
        "engine_mode": req.engine_mode,
    }
    
    async def event_generator():
        if req.engine_mode == "pdf2zh_layout":
            pages_list = [p + 1 for p in page_indices]
            yield f"event: start\ndata: {json.dumps({'job_id': job_id, 'total_pages': total_selected, 'pages': pages_list, 'engine_mode': 'pdf2zh_layout'})}\n\n"

            mono_path = settings.export_dir / f"{job_id}_mono.pdf"
            dual_path = settings.export_dir / f"{job_id}_dual.pdf"
            docx_path = settings.export_dir / f"{job_id}.docx"
            md_path = settings.export_dir / f"{job_id}.md"

            try:
                translator = create_translator(
                    provider=req.ai_config.provider,
                    target_lang=req.target_lang,
                    api_key=req.ai_config.api_key or "",
                    model=req.ai_config.model or "",
                    base_url=req.ai_config.base_url or "",
                    custom_prompt=req.ai_config.custom_prompt or "",
                    temperature=req.ai_config.temperature,
                )
            except Exception as e:
                yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'error': f'Lỗi khởi tạo translator: {str(e)}', 'engine_mode': 'pdf2zh_layout'})}\n\n"
                return

            translated_pages = []
            try:
                async for event_item in process_pdf2zh_stream(
                    file_path=pdf_path,
                    page_indices=page_indices,
                    target_lang=req.target_lang,
                    translator=translator,
                    mono_out_path=mono_path,
                    dual_out_path=dual_path,
                ):
                    ev_type = event_item.get("event")
                    if ev_type == "page_progress":
                        progress_data = {
                            "current_index": event_item.get("current_index"),
                            "total_pages": event_item.get("total_pages", total_selected),
                            "page_number": event_item.get("page_number"),
                        }
                        yield f"event: page_progress\ndata: {json.dumps(progress_data)}\n\n"
                    elif ev_type == "page_completed":
                        pno = event_item.get("page_number")
                        orig_b64 = event_item.get("original_image", "")
                        trans_b64 = event_item.get("translated_image", "")
                        text = event_item.get("translated_text", "")
                        page_result = {
                            "page_number": pno,
                            "original_image": orig_b64,
                            "translated_image": trans_b64,
                            "translated_text": text,
                            "engine_mode": "pdf2zh_layout",
                        }
                        translated_pages.append(page_result)
                        JOB_STORE[job_id]["pages"].append(page_result)
                        yield f"event: page_completed\ndata: {json.dumps(page_result)}\n\n"
                    elif ev_type == "completed":
                        pass
            except Exception as e:
                yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'error': f'Lỗi xử lý tài liệu: {str(e)}', 'engine_mode': 'pdf2zh_layout'})}\n\n"
                return

            # Generate docx and md from translated pages text so all export buttons work
            doc_title = f"Bản dịch - {req.target_lang}"
            try:
                if translated_pages:
                    generate_docx(translated_pages, docx_path, doc_title)
                    generate_markdown(translated_pages, md_path, doc_title)
            except Exception as e:
                logger.warning(f"Error generating docx/md: {e}")

            mono_ready = mono_path.exists()
            dual_ready = dual_path.exists()
            docx_ready = docx_path.exists()
            md_ready = md_path.exists()

            yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'mono_ready': mono_ready, 'dual_ready': dual_ready, 'docx_ready': docx_ready, 'md_ready': md_ready, 'engine_mode': 'pdf2zh_layout'})}\n\n"

        else:
            # Beamer slide mode: keep current Beamer logic untouched
            yield f"event: start\ndata: {json.dumps({'job_id': job_id, 'total_pages': total_selected, 'pages': [p + 1 for p in page_indices], 'engine_mode': 'beamer_slide'})}\n\n"
            
            translated_pages = []
            for seq_idx, page_idx in enumerate(page_indices):
                page_num = page_idx + 1
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
                
                yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'docx_ready': True, 'pdf_ready': True, 'md_ready': True, 'tex_ready': True, 'engine_mode': 'beamer_slide'})}\n\n"
            except Exception as e:
                yield f"event: completed\ndata: {json.dumps({'job_id': job_id, 'error': f'Lỗi xuất file: {str(e)}', 'engine_mode': 'beamer_slide'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/download/{job_id}/{fmt}")
async def download_file(job_id: str, fmt: str):
    fmt = fmt.lower()
    allowed_fmts = ["docx", "pdf", "md", "tex", "mono_pdf", "dual_pdf", "mono", "dual"]
    if fmt not in allowed_fmts:
        raise HTTPException(
            status_code=400,
            detail="Định dạng không hỗ trợ (chỉ docx, pdf, md, tex, mono_pdf, dual_pdf, mono, dual)"
        )
    
    raw_name = "document"
    if job_id in JOB_STORE:
        raw_name = Path(JOB_STORE[job_id]["filename"]).stem
        
    mono_file = settings.export_dir / f"{job_id}_mono.pdf"
    dual_file = settings.export_dir / f"{job_id}_dual.pdf"
    
    if fmt in ["mono_pdf", "mono"] or (fmt == "pdf" and mono_file.exists()):
        file_path = mono_file
        download_filename = f"{raw_name}_translated.pdf"
        media_type = "application/pdf"
    elif fmt in ["dual_pdf", "dual"]:
        file_path = dual_file
        download_filename = f"{raw_name}_bilingual.pdf"
        media_type = "application/pdf"
    else:
        file_path = settings.export_dir / f"{job_id}.{fmt}"
        download_filename = f"{raw_name}_translated.{fmt}"
        media_types = {
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
            "md": "text/markdown",
            "tex": "application/x-tex",
        }
        media_type = media_types.get(fmt, "application/octet-stream")
        
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File xuất không tồn tại hoặc đã hết hạn")
        
    return FileResponse(
        path=file_path,
        filename=download_filename,
        media_type=media_type
    )
