from pathlib import Path

from raida.export.docx import md_to_docx
from raida.export.markdown import md_to_html, md_to_text
from raida.export.pdf import Fpdf2Renderer, select_pdf_renderer
from raida.pipeline.stages.pdf import PageText, extract_pages, needs_ocr, pages_markdown

FIXTURES = Path(__file__).parent.parent / "fixtures"
SAMPLE = (
    "# Title\n\nSome **bold** text and `code`.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
)


def test_needs_ocr_heuristic() -> None:
    assert needs_ocr([], 100, 0.4)
    assert needs_ocr([PageText(1, "x" * 10), PageText(2, "")], 100, 0.4)
    assert not needs_ocr([PageText(1, "x" * 500), PageText(2, "y" * 500)], 100, 0.4)


def test_extract_text_pdf_both_extractors() -> None:
    for name in ("pymupdf4llm", "pypdfium2"):
        pages = extract_pages(FIXTURES / "text.pdf", name)
        assert len(pages) == 2
        assert "sentence 3 on page 1" in pages[0].text
        assert not needs_ocr(pages, 100, 0.4)
    md = pages_markdown(pages)
    assert md.startswith("[p. 1]")
    assert "[p. 2]" in md


def test_scanned_pdf_triggers_ocr_heuristic() -> None:
    pages = extract_pages(FIXTURES / "scanned.pdf", "pypdfium2")
    assert needs_ocr(pages, 100, 0.4)


def test_markdown_to_html_and_text() -> None:
    html = md_to_html(SAMPLE)
    assert "<table>" in html and "<strong>bold</strong>" in html
    text = md_to_text(SAMPLE)
    assert "Title" in text and "bold" in text and "- one" in text
    assert "<" not in text


def test_pdf_renderers_produce_pdf_bytes() -> None:
    for renderer in (select_pdf_renderer("auto"), Fpdf2Renderer()):
        data = renderer.render(SAMPLE, "Test")
        assert data[:5] == b"%PDF-", renderer.name


def test_docx_export() -> None:
    data = md_to_docx(SAMPLE, "Test")
    assert data[:2] == b"PK"
