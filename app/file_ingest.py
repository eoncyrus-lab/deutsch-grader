from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


MAX_FILE_BYTES = 8 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


class FileIngestError(ValueError):
    pass


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileIngestError("无法识别文本编码，请另存为 UTF-8 后重试")


def _docx_text(data: bytes) -> str:
    try:
        document = Document(BytesIO(data))
    except Exception as exc:
        raise FileIngestError(f"Word 文件无法读取：{exc}") from exc
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise FileIngestError(f"PDF 文件无法读取：{exc}") from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise FileIngestError("文件超过 8 MB，请压缩或改用图片上传")
    suffix = Path(filename or "").suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = _decode_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        raise FileIngestError("图片请走图片上传入口，先确认脱敏副本")
    else:
        raise FileIngestError("支持 TXT、Markdown、PDF、DOCX；图片请走图片上传")
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:
        raise FileIngestError("文件里没有足够的可提取文字；扫描版 PDF 请改用图片上传")
    return text
