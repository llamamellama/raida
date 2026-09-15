"""Plain text and markdown sources."""

from __future__ import annotations

from pathlib import Path

from charset_normalizer import from_path


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        best = from_path(path).best()
        if best is None:
            raise ValueError(f"Could not determine text encoding of {path.name}") from None
        return str(best)
