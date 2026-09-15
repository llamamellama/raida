"""File intake: uploads streamed to disk, local paths referenced in place, kind detection."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from raida.config import Config
from raida.db import Database
from raida.models import KIND_BY_SUFFIX, Source, SourceKind, new_id, utc_now

CHUNK = 1024 * 1024


class IngestError(ValueError):
    pass


def detect_kind(filename: str) -> SourceKind:
    suffix = Path(filename).suffix.lower()
    kind = KIND_BY_SUFFIX.get(suffix)
    if kind is None:
        supported = ", ".join(sorted(KIND_BY_SUFFIX))
        raise IngestError(f"Unsupported file type '{suffix or filename}'. Supported: {supported}")
    return kind


def safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/")).strip() or "upload"
    return "".join(c if c.isprintable() and c not in '<>:"/|?*' else "_" for c in base)[:200]


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def store_upload(
    config: Config, filename: str, chunks: AsyncIterator[bytes]
) -> tuple[Path, str, int]:
    """Stream an upload to a temp file while hashing, then place it under uploads/<sha>/."""
    config.ensure_dirs()
    name = safe_filename(filename)
    tmp = config.uploads_dir / f".partial-{new_id()}"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > config.upload.max_bytes:
                    raise IngestError(
                        f"{name} exceeds upload.max_bytes ({config.upload.max_bytes} bytes)"
                    )
                digest.update(chunk)
                out.write(chunk)
        sha = digest.hexdigest()
        target_dir = config.uploads_dir / sha
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.exists():
            tmp.unlink()
        else:
            shutil.move(str(tmp), str(target))
        return target, sha, size
    finally:
        if tmp.exists():
            tmp.unlink()


def check_allowed_path(config: Config, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise IngestError(f"Not a file: {path}")
    roots = [r.resolve() for r in config.paths.allowed_roots]
    if not roots:
        raise IngestError(
            "Adding by path is disabled: set paths.allowed_roots in raida.toml to the folders "
            "you want to reference in place."
        )
    if not any(resolved == r or r in resolved.parents for r in roots):
        raise IngestError(f"{resolved} is outside paths.allowed_roots")
    return resolved


def build_source(
    *,
    session_id: str,
    original_name: str,
    stored_path: Path,
    sha256: str,
    size_bytes: int,
    language: str,
    managed: bool,
) -> Source:
    now = utc_now()
    return Source(
        id=new_id(),
        session_id=session_id,
        kind=detect_kind(original_name),
        original_name=original_name,
        stored_path=str(stored_path),
        managed=managed,
        sha256=sha256,
        size_bytes=size_bytes,
        language=language or "auto",
        status="queued",
        created_at=now,
        updated_at=now,
    )


def remove_stored_file_if_unreferenced(db: Database, source: Source) -> None:
    if not source.managed:
        return
    if db.sources_referencing(source.sha256) > 0:
        return
    path = Path(source.stored_path)
    if path.exists():
        path.unlink()
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
