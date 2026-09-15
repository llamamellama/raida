"""Markdown to PDF with two renderers behind one interface.

WeasyPrint gives real typography but needs Pango from Homebrew; fpdf2 is pure pip and is the
fallback so a fresh machine never hard-fails on export.
"""

from __future__ import annotations

import html
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Protocol

from raida.export.markdown import md_to_html

log = logging.getLogger(__name__)

_UNICODE_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


class PdfRenderer(Protocol):
    name: str

    def render(self, markdown: str, title: str) -> bytes: ...


def _print_css() -> str:
    return resources.files("raida.export").joinpath("styles/print.css").read_text("utf-8")


def _document_html(markdown: str, title: str, css: str | None) -> str:
    body = md_to_html(markdown)
    head = f"<title>{html.escape(title)}</title>"
    if css is not None:
        head += f"<style>{css}</style>"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>{body}</body></html>"
    )


class WeasyPrintRenderer:
    name = "weasyprint"

    def render(self, markdown: str, title: str) -> bytes:
        from weasyprint import HTML

        document = _document_html(markdown, title, _print_css())
        return HTML(string=document).write_pdf()


class Fpdf2Renderer:
    name = "fpdf2"

    def __init__(self) -> None:
        self.font_path = next((p for p in _UNICODE_FONT_CANDIDATES if Path(p).exists()), None)

    def render(self, markdown: str, title: str) -> bytes:
        from fpdf import FPDF

        pdf = FPDF(format="A4")
        pdf.set_margins(20, 20, 20)
        pdf.set_auto_page_break(auto=True, margin=22)
        pdf.set_title(title)
        pdf.add_page()
        font_family = "Helvetica"
        if self.font_path:
            for style in ("", "B", "I", "BI"):
                pdf.add_font("body", style, self.font_path)
            font_family = "body"
        pdf.set_font(font_family, size=11)
        body = md_to_html(markdown)
        # fpdf2's HTML subset has no CSS and no <table> width hints; simplify what it cannot do.
        body = re.sub(r"<(/?)(section|div|span|input)[^>]*>", "", body)
        if font_family != "body":
            body = body.encode("latin-1", "replace").decode("latin-1")
        pdf.write_html(body, font_family=font_family)
        return bytes(pdf.output())


def select_pdf_renderer(preference: str) -> PdfRenderer:
    if preference in ("auto", "weasyprint"):
        try:
            import weasyprint  # noqa: F401

            return WeasyPrintRenderer()
        except (ImportError, OSError) as exc:
            if preference == "weasyprint":
                raise RuntimeError(
                    f"WeasyPrint unavailable ({exc}). On macOS: `brew install pango` and export "
                    f"DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib, or set "
                    f"export.pdf_renderer='fpdf2'."
                ) from exc
            log.warning("weasyprint_unavailable", extra={"reason": str(exc)[:200]})
    return Fpdf2Renderer()
