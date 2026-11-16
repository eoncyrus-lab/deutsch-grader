from io import BytesIO

import pytest
from docx import Document

from app.file_ingest import FileIngestError, extract_text


def test_extract_txt_and_docx():
    text = extract_text("essay.txt", "Meine beste Reise war im Sommer.\\nIch war am Strand.".encode("utf-8"))
    assert "Qingdao" not in text
    assert "Meine beste Reise" in text
    document = Document()
    document.add_paragraph("Meine beste Reise war im Sommer.")
    document.add_paragraph("Ich war am Strand.")
    buffer = BytesIO()
    document.save(buffer)
    docx_text = extract_text("essay.docx", buffer.getvalue())
    assert "Meine beste Reise" in docx_text
    assert "Strand" in docx_text


def test_extract_rejects_empty_or_unsupported_files():
    with pytest.raises(FileIngestError):
        extract_text("essay.txt", b"")
    with pytest.raises(FileIngestError):
        extract_text("essay.doc", b"legacy")
    with pytest.raises(FileIngestError):
        extract_text("scan.pdf", b"not really a pdf")
