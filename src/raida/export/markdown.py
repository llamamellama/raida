"""Markdown parsing shared by every exporter."""

from __future__ import annotations

import html
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin


def parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"typographer": False, "html": False})
    md.enable(["table", "strikethrough"])
    md.use(footnote_plugin)
    md.use(tasklists_plugin)
    return md


def md_to_html(markdown: str) -> str:
    return parser().render(markdown)


_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ul",
    "ol",
    "pre",
    "blockquote",
    "tr",
    "table",
    "hr",
    "section",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._list_depth = 0
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("ul", "ol"):
            self._list_depth += 1
        elif tag == "li":
            self.parts.append("\n" + "  " * (self._list_depth - 1) + "- ")
        elif tag == "pre":
            self._in_pre = True
            self.parts.append("\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol"):
            self._list_depth -= 1
            self.parts.append("\n")
        elif tag == "pre":
            self._in_pre = False
            self.parts.append("\n")
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "table"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(
            data if self._in_pre else " ".join(data.split()) or (" " if data.isspace() else "")
        )

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [line.rstrip() for line in raw.splitlines()]
        out: list[str] = []
        blank = 0
        for line in lines:
            if line.strip():
                out.append(line)
                blank = 0
            else:
                blank += 1
                if blank <= 1 and out:
                    out.append("")
        return "\n".join(out).strip() + "\n"


def md_to_text(markdown: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(md_to_html(markdown))
    extractor.close()
    return html.unescape(extractor.text())
