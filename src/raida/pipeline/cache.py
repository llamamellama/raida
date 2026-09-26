"""Content-addressed cache of processed documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from raida.config import Config
from raida.db import Database
from raida.llm.tokens import estimate_tokens
from raida.models import ProcessedCacheEntry, ProcessedSource, utc_now

# Bump when a fix changes processed output for the same input; v2: the media stage
# passes regional language codes (zh-TW) to the transcriber instead of the base language;
# v3: Chinese transcripts are normalized to the chosen script with OpenCC.
PIPELINE_VERSION = "v3"


def cache_key(sha256: str, kind: str, language: str, variant: str) -> str:
    raw = "|".join((PIPELINE_VERSION, sha256, kind, language, variant))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def processed_paths(config: Config, key: str) -> tuple[Path, Path]:
    return config.processed_dir / f"{key}.md", config.processed_dir / f"{key}.json"


def store_processed(
    config: Config, db: Database, key: str, sha256: str, doc: ProcessedSource
) -> Path:
    md_path, json_path = processed_paths(config, key)
    md_path.write_text(doc.text_markdown, encoding="utf-8")
    sidecar = doc.model_dump(exclude={"text_markdown"})
    json_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
    db.put_processed_cache(
        ProcessedCacheEntry(
            cache_key=key,
            sha256=sha256,
            processed_path=str(md_path),
            token_estimate=doc.token_estimate,
            meta=doc.meta,
            created_at=utc_now(),
        )
    )
    return md_path


def load_processed(
    path: str | Path, source_id: str, title: str, chars_per_token: float | None = None
) -> ProcessedSource:
    """Load a processed document. With ``chars_per_token`` the token estimate is recomputed
    with the current estimator instead of trusting the stored one, so planning stays correct
    after the estimator changes (it once under-counted CJK text almost threefold)."""
    md_path = Path(path)
    json_path = md_path.with_suffix(".json")
    sidecar = json.loads(json_path.read_text(encoding="utf-8"))
    sidecar.update({"source_id": source_id, "title": title})
    text = md_path.read_text(encoding="utf-8")
    if chars_per_token is not None:
        sidecar["token_estimate"] = estimate_tokens(text, chars_per_token)
    return ProcessedSource(text_markdown=text, **sidecar)


def lookup(db: Database, key: str) -> ProcessedCacheEntry | None:
    entry = db.get_processed_cache(key)
    if entry is None:
        return None
    md_path = Path(entry.processed_path)
    if not md_path.exists() or not md_path.with_suffix(".json").exists():
        return None
    return entry
