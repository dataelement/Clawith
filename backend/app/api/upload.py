"""File upload API for chat — saves files to agent workspace and extracts text."""

import base64
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Form
from app.core.security import get_current_user
from app.models.user import User
from app.services.storage import ensure_local_path, get_storage_backend, guess_content_type, normalize_storage_key
from app.services.text_extractor import extract_text as extract_document_text

router = APIRouter(prefix="/chat", tags=["chat"])

# Supported extensions and their text extraction method
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".css", ".sql", ".sh", ".log",
    ".ini", ".cfg", ".conf", ".env", ".toml",
}
OFFICE_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
EXTRACTABLE = TEXT_EXTENSIONS | OFFICE_EXTENSIONS

MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def extract_text(file_path: Path, extension: str) -> str:
    """Extract text content from a file."""
    if extension in TEXT_EXTENSIONS:
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return file_path.read_text(encoding="gbk", errors="replace")

    extraction_failures = {
        ".pdf": "[PDF内容提取失败]",
        ".docx": "[DOCX内容提取失败]",
        ".xlsx": "[Excel内容提取失败]",
        ".xls": "[Excel内容提取失败]",
    }
    if extension in extraction_failures:
        try:
            # Pass file bytes to the trusted extractor; never interpolate an upload path into executable code.
            text = extract_document_text(file_path.read_bytes(), file_path.name)
            return text[:8000] if text else extraction_failures[extension]
        except Exception as e:
            format_name = "PDF" if extension == ".pdf" else "DOCX" if extension == ".docx" else "Excel"
            return f"[{format_name}解析错误: {e}]"

    return f"[不支持的文件格式: {extension}]"


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    agent_id: str = Form(""),
    current_user: User = Depends(get_current_user),
):
    """Upload a file for chat context. Saves to agent workspace/uploads/ and returns extracted text."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    ext = os.path.splitext(file.filename)[1].lower()

    content = await file.read()

    # Determine save directory
    workspace_path = ""
    if agent_id:
        storage = get_storage_backend()
        filename = file.filename.replace("/", "_").replace("\\", "_")
        workspace_path = f"workspace/uploads/{filename}"
        key = normalize_storage_key(f"{agent_id}/{workspace_path}")
        counter = 1
        while await storage.exists(key):
            stem, ext = os.path.splitext(filename)
            filename = f"{stem}_{counter}{ext}"
            workspace_path = f"workspace/uploads/{filename}"
            key = normalize_storage_key(f"{agent_id}/{workspace_path}")
            counter += 1
        await storage.write_bytes(key, content, content_type=guess_content_type(filename))
        save_path = await ensure_local_path(key)
    else:
        # Fallback: save to /tmp (legacy behavior)
        fallback_dir = Path("/tmp/clawith_uploads")
        fallback_dir.mkdir(exist_ok=True)
        file_id = str(uuid.uuid4())[:8]
        save_path = fallback_dir / f"{file_id}_{file.filename}"
        save_path.write_bytes(content)

    # Extract text (only for known formats)
    is_image = ext in IMAGE_EXTENSIONS
    image_data_url = ""
    if is_image:
        # For images: generate base64 data URL for vision models
        if len(content) > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
        mime = MIME_MAP.get(ext, "image/png")
        b64 = base64.b64encode(content).decode("ascii")
        image_data_url = f"data:{mime};base64,{b64}"
        extracted = f"[图片文件: {file.filename}，需要视觉模型分析]"
    elif ext in EXTRACTABLE:
        extracted = extract_text(save_path, ext)
    else:
        extracted = f"[文件已保存，格式 {ext} 暂不支持文本提取，Agent 可通过 read_document 工具读取]"

    # Truncate if too long
    if len(extracted) > 6000:
        extracted = extracted[:6000] + "\n\n...[内容已截断，共 " + str(len(extracted)) + " 字]"

    return {
        "filename": file.filename,
        "saved_filename": save_path.name,
        "size": len(content),
        "extracted_text": extracted,
        "workspace_path": workspace_path,
        "is_image": is_image,
        "image_data_url": image_data_url,
    }
