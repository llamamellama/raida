"""File intake: uploads streamed to disk, local paths referenced in place, kind detection."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from raida.config import Config
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
    original_name: str,
    stored_path: Path,
    sha256: str,
    size_bytes: int,
    language: str,
    managed: bool,
) -> Source:
    """A library entry for a file not yet processed. Adding it to the library and to a
    session is the scheduler's job (``Scheduler.add_source``)."""
    now = utc_now()
    return Source(
        id=new_id(),
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


def discard_upload(config: Config, path: Path, kept: Source) -> None:
    """Delete a copy just uploaded of a file the library already has at ``kept.stored_path``
    (the same content under another name). Files added by path are never touched."""
    if path == Path(kept.stored_path) or config.uploads_dir not in path.parents:
        return
    path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):  # not empty: the kept copy lives there too
        path.parent.rmdir()


def remove_file_data(config: Config, source: Source, processed_paths: list[str]) -> None:
    """Delete what raida stored for a file removed from the library: the uploaded copy (never
    a file added by path, which is the user's), the decoded audio and the processed text."""
    stored = Path(source.stored_path)
    if source.managed and stored.parent == config.uploads_dir / source.sha256:
        # Every file in uploads/<sha256>/ has this content; older builds kept one per name.
        for path in stored.parent.glob("*"):
            if path.is_file():
                path.unlink()
        with contextlib.suppress(OSError):
            stored.parent.rmdir()
    (config.media_dir / f"{source.sha256}.wav").unlink(missing_ok=True)
    for processed in processed_paths:
        md = Path(processed)
        md.unlink(missing_ok=True)
        md.with_suffix(".json").unlink(missing_ok=True)
