"""Word documents to markdown-ish text."""

from __future__ import annotations

from pathlib import Path


def docx_to_markdown(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    lines: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = docx.text.paragraph.Paragraph(block, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            if style.startswith("Heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                lines.append(f"{'#' * min(int(level), 6)} {text}")
            elif style.startswith("List"):
                lines.append(f"- {text}")
            else:
                lines.append(text)
        elif tag == "tbl":
            table = docx.table.Table(block, document)
            rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in table.rows]
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("|" + "---|" * len(rows[0]))
                for row in rows[1:]:
                    lines.append("| " + " | ".join(row) + " |")
    return "\n\n".join(lines) + ("\n" if lines else "")
