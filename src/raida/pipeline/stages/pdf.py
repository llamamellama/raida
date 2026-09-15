"""PDF text extraction behind a small interface, plus the OCR escalation heuristic.

The default extractor (pymupdf4llm) is AGPL-licensed; the alternative (pypdfium2) is
permissive. Both run inside the CPU process pool, so they import lazily in the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class PageText:
    number: int  # 1-based
    text: str


class TextExtractor(Protocol):
    name: str

    def extract(self, path: Path) -> list[PageText]: ...


class PyMuPdf4LlmExtractor:
    name = "pymupdf4llm"

    def extract(self, path: Path) -> list[PageText]:
        import pymupdf4llm  # AGPL; see docs/adr/0004

        chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True, show_progress=False)
        pages: list[PageText] = []
        for index, chunk in enumerate(chunks, start=1):
            number = int(chunk.get("metadata", {}).get("page_number", index) or index)
            pages.append(PageText(number=number, text=str(chunk.get("text", "")).strip()))
        return pages


class PypdfiumExtractor:
    name = "pypdfium2"

    def extract(self, path: Path) -> list[PageText]:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        try:
            pages = []
            for index in range(len(doc)):
                page = doc[index]
                textpage = page.get_textpage()
                text = textpage.get_text_range() or ""
                textpage.close()
                page.close()
                pages.append(PageText(number=index + 1, text=text.strip()))
            return pages
        finally:
            doc.close()


def get_extractor(name: str) -> TextExtractor:
    if name == "pymupdf4llm":
        return PyMuPdf4LlmExtractor()
    if name == "pypdfium2":
        return PypdfiumExtractor()
    raise ValueError(f"Unknown pdf extractor: {name}")


def extract_pages(path: Path, extractor_name: str) -> list[PageText]:
    return get_extractor(extractor_name).extract(path)


def needs_ocr(pages: list[PageText], min_chars_per_page: int, empty_page_ratio: float) -> bool:
    if not pages:
        return True
    lengths = [len(p.text) for p in pages]
    mean = sum(lengths) / len(lengths)
    near_empty = sum(1 for n in lengths if n < min_chars_per_page) / len(lengths)
    return mean < min_chars_per_page or near_empty > empty_page_ratio


def pages_markdown(pages: list[PageText]) -> str:
    parts = [f"[p. {p.number}]\n\n{p.text.strip()}" for p in pages if p.text.strip()]
    return "\n\n".join(parts) + ("\n" if parts else "")


def page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def render_page_png(path: Path, index: int, dpi: int) -> bytes:
    """Render one page (0-based) to PNG bytes for OCR."""
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[index]
        bitmap = page.render(scale=dpi / 72)
        image = bitmap.to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        page.close()
        return buf.getvalue()
    finally:
        doc.close()
