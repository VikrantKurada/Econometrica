"""Turning an uploaded file into the text retrieval indexes.

Kept separate from the route and from `rag.py`: the route handles HTTP, `rag.py`
chunks and embeds, and this decides only how bytes become text. Text formats are
read directly; PDFs go through `pypdf`. Anything else is refused by name rather
than mis-parsed — a `.docx` read as UTF-8 is mojibake that would embed as noise.
"""

import io
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

#: Read directly as UTF-8. Everything else is refused.
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
SUPPORTED_SUFFIXES = _TEXT_SUFFIXES | {".pdf"}


class DocumentError(ValueError):
    """A file could not be turned into indexable text."""


class UnsupportedDocumentError(DocumentError):
    """The file type is not one this system extracts text from."""


class EmptyDocumentError(DocumentError):
    """The file parsed but carried no text to index."""


def extract_text(filename: str, data: bytes) -> str:
    """The document's text, or a `DocumentError` naming why not."""
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text = data.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        text = _extract_pdf(data)
    else:
        raise UnsupportedDocumentError(
            f"cannot index {suffix or 'a file with no extension'};"
            f" supported types are {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    if not text.strip():
        # A scanned (image-only) PDF lands here, and so does an empty text file.
        raise EmptyDocumentError(f"{filename} carried no text to index")
    return text


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, ValueError) as exc:
        raise DocumentError(f"the PDF could not be read: {exc}") from exc
