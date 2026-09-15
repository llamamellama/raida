"""OCR of rendered PDF pages with Apple Vision (via ocrmac). macOS only."""

from __future__ import annotations

import io
import platform
from pathlib import Path

from raida.pipeline.stages.pdf import PageText, render_page_png


def ocr_available() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        import ocrmac  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_png(png: bytes, languages: list[str]) -> str:
    from ocrmac import ocrmac
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    annotations = ocrmac.OCR(
        image,
        recognition_level="accurate",
        language_preference=languages or None,
        framework="vision",
    ).recognize()
    # Vision bounding boxes are normalized with the origin at the bottom-left; sort into
    # reading order: top to bottom (descending y), then left to right.
    ordered = sorted(annotations, key=lambda a: (-round(a[2][1], 2), a[2][0]))
    return "\n".join(text.strip() for text, _conf, _bbox in ordered if text.strip())


def ocr_pdf_page(path: Path, index: int, dpi: int, languages: list[str]) -> PageText:
    png = render_page_png(path, index, dpi)
    return PageText(number=index + 1, text=ocr_png(png, languages))
