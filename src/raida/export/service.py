"""Turn an assistant message into a downloadable artifact."""

from __future__ import annotations

import re

from raida.config import Config
from raida.db import Database
from raida.export.docx import md_to_docx
from raida.export.markdown import md_to_text
from raida.export.pdf import select_pdf_renderer
from raida.models import Artifact, ExportFormat, Message, new_id, utc_now

MEDIA_TYPES: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def slugify(text: str, fallback: str = "raida-output") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:60] or fallback


def render(markdown: str, fmt: ExportFormat, title: str, renderer_pref: str) -> bytes:
    if fmt == "md":
        return markdown.encode("utf-8")
    if fmt == "txt":
        return md_to_text(markdown).encode("utf-8")
    if fmt == "pdf":
        return select_pdf_renderer(renderer_pref).render(markdown, title)
    if fmt == "docx":
        return md_to_docx(markdown, title)
    raise ValueError(f"Unsupported export format: {fmt}")


def export_message(config: Config, db: Database, message: Message, fmt: ExportFormat) -> Artifact:
    if message.role != "assistant" or message.status != "done":
        raise ValueError("Only completed assistant messages can be exported")
    session = db.get_session(message.session_id)
    title = session.title
    stamp = utc_now()[:19].replace(":", "").replace("-", "")
    filename = f"{slugify(title)}-{stamp}.{fmt}"
    data = render(message.content, fmt, title, config.export.pdf_renderer)
    artifact_id = new_id()
    target_dir = config.artifacts_dir / message.id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{artifact_id}.{fmt}"
    path.write_bytes(data)
    artifact = Artifact(
        id=artifact_id,
        message_id=message.id,
        format=fmt,
        path=str(path),
        filename=filename,
        size_bytes=len(data),
        created_at=utc_now(),
    )
    return db.create_artifact(artifact)
