"""Turning an uploaded file into the text retrieval indexes."""

import io
from pathlib import Path

import pytest

from econometrica.services.documents import (
    EmptyDocumentError,
    UnsupportedDocumentError,
    extract_text,
)


def test_a_text_file_is_its_own_content():
    assert extract_text("notes.txt", b"Beta exceeded one.") == "Beta exceeded one."


def test_a_markdown_file_is_read_as_text():
    assert "heading" in extract_text("r.md", b"# heading\n\nbody").lower()


def test_a_pdf_with_text_yields_that_text():
    """A PDF carrying real text extracts it. Read from a tiny committed fixture,
    because laying out text into a PDF in-process needs a layout library
    (reportlab) that is not — and should not become — a dependency."""
    pdf = Path(__file__).parent / "fixtures" / "one_line.pdf"
    text = extract_text("one_line.pdf", pdf.read_bytes())

    assert "volatility" in text.lower()  # the fixture's single line


def test_a_blank_pdf_is_refused_as_empty():
    """A blank or image-only (scanned) PDF extracts to nothing, so it is refused
    the same way an empty text file is — retrieval never indexes nothing."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(EmptyDocumentError):
        extract_text("blank.pdf", buffer.getvalue())


def test_an_unknown_type_is_refused_naming_the_supported_set():
    with pytest.raises(UnsupportedDocumentError, match=r"\.txt"):
        extract_text("data.xlsx", b"...")


def test_a_document_that_is_all_whitespace_is_refused():
    with pytest.raises(EmptyDocumentError):
        extract_text("blank.txt", b"   \n\t ")
