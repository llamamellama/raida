"""Configuration loading with fail-fast validation.

Sources, in increasing precedence:
1. ``raida.toml`` (path from the ``--config`` flag, else ``RAIDA_CONFIG``, else ``./raida.toml``)
2. Environment variables ``RAIDA_<SECTION>__<KEY>`` (double underscore separates section and key;
   values are parsed as JSON when possible, otherwise taken as strings).

``llm.model`` is required and has no default: the app refuses to start without it.
"""

from __future__ import annotations

import json
import os
import platform
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ENV_PREFIX = "RAIDA_"
CONFIG_ENV_VAR = "RAIDA_CONFIG"
DEFAULT_CONFIG_FILENAME = "raida.toml"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def default_data_dir() -> Path:
    # Expanded here as well as in the field validator: pydantic does not validate defaults,
    # so a bare "~" would become a directory literally named "~" in the working directory.
    if platform.system() == "Darwin":
        return Path("~/Library/Application Support/raida").expanduser()
    return Path("~/.local/share/raida").expanduser()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmConfig(StrictModel):
    backend: Literal["ollama", "openai_compatible", "fake"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(min_length=1, description="Model name as known to the local server.")
    api_key: str = "raida-local"
    synthesis_budget_tokens: int = Field(default=64_000, ge=4_000)
    output_reserve_tokens: int = Field(default=8_192, ge=512)
    prompt_overhead_tokens: int = Field(default=2_048, ge=256)
    condensation_target_tokens: int = Field(default=6_000, ge=500)
    map_chunk_tokens: int = Field(default=24_000, ge=2_000)
    history_budget_tokens: int = Field(default=6_000, ge=0)
    keep_alive: str = "1h"
    # Ollama sends nothing until the prompt is read; 100k tokens of Chinese took over 15 minutes
    # on an M2 Max, so the read timeout must cover the whole prefill of the largest prompt.
    request_timeout_s: float = Field(default=3600.0, gt=0)
    chars_per_token: float = Field(default=3.7, gt=1.0)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    # Ollama only. Reasoning models stream their reasoning in a separate field that raida does
    # not show, so it only costs time and output budget. False turns it off for hybrid models
    # with a non-thinking mode (Qwen3 2504 tags, Qwen3.5/3.6); "low" | "medium" | "high" sets
    # the effort for gpt-oss. Thinking-only tags (Qwen3 Thinking-2507) ignore False and then
    # leak their reasoning into the answer, so leave it None for them (server default).
    think: bool | Literal["low", "medium", "high"] | None = None
    # Name a session after its first answer (one extra short model call per session).
    suggest_titles: bool = True

    @property
    def num_ctx(self) -> int:
        return (
            self.synthesis_budget_tokens + self.output_reserve_tokens + self.prompt_overhead_tokens
        )


class TranscribeConfig(StrictModel):
    backend: Literal["parakeet", "whisper", "apple", "fake"] = "parakeet"
    # parakeet-mlx reads config.json + model.safetensors; the mlx-community repo has them,
    # the nvidia/ repo ships a .nemo archive instead.
    parakeet_model: str = "mlx-community/parakeet-tdt-0.6b-v3"
    whisper_model: str = "mlx-community/whisper-large-v3-mlx"
    language_detection: bool = True
    detection_seconds: int = Field(default=30, ge=5, le=120)
    allow_model_download: bool = False
    ffmpeg_path: str | None = None
    paragraph_seconds: int = Field(default=45, ge=10, le=600)


class OcrConfig(StrictModel):
    enabled: bool = True
    languages: list[str] = ["en-US"]
    dpi: int = Field(default=300, ge=72, le=600)
    min_chars_per_page: int = Field(default=100, ge=0)
    empty_page_ratio: float = Field(default=0.4, ge=0.0, le=1.0)


class PdfConfig(StrictModel):
    extractor: Literal["pymupdf4llm", "pypdfium2"] = "pymupdf4llm"


class PathsConfig(StrictModel):
    data_dir: Path = Field(default_factory=default_data_dir)
    allowed_roots: list[Path] = []

    @field_validator("data_dir", mode="after")
    @classmethod
    def _expand_data_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("allowed_roots", mode="after")
    @classmethod
    def _expand_roots(cls, value: list[Path]) -> list[Path]:
        return [p.expanduser() for p in value]


class ServerConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    open_browser: bool = True


class WorkersConfig(StrictModel):
    cpu: int | Literal["auto"] = "auto"
    gpu: int = Field(default=1, ge=1, le=4)
    llm: int = Field(default=1, ge=1, le=8)
    subprocess: int = Field(default=2, ge=1, le=16)


class UploadConfig(StrictModel):
    max_bytes: int = Field(default=8 * 1024**3, ge=1024)


class ExportConfig(StrictModel):
    pdf_renderer: Literal["auto", "weasyprint", "fpdf2"] = "auto"


class Config(StrictModel):
    llm: LlmConfig
    transcribe: TranscribeConfig = TranscribeConfig()
    ocr: OcrConfig = OcrConfig()
    pdf: PdfConfig = PdfConfig()
    paths: PathsConfig = PathsConfig()
    server: ServerConfig = ServerConfig()
    workers: WorkersConfig = WorkersConfig()
    upload: UploadConfig = UploadConfig()
    export: ExportConfig = ExportConfig()
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def uploads_dir(self) -> Path:
        return self.paths.data_dir / "uploads"

    @property
    def processed_dir(self) -> Path:
        return self.paths.data_dir / "processed"

    @property
    def artifacts_dir(self) -> Path:
        return self.paths.data_dir / "artifacts"

    @property
    def media_dir(self) -> Path:
        return self.paths.data_dir / "media"

    @property
    def models_dir(self) -> Path:
        return self.paths.data_dir / "models"

    @property
    def db_path(self) -> Path:
        return self.paths.data_dir / "raida.sqlite3"

    def ensure_dirs(self) -> None:
        for d in (
            self.paths.data_dir,
            self.uploads_dir,
            self.processed_dir,
            self.artifacts_dir,
            self.media_dir,
            self.models_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def _parse_env_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith(ENV_PREFIX) or key == CONFIG_ENV_VAR:
            continue
        path = key[len(ENV_PREFIX) :].lower().split("__")
        if len(path) == 1:
            overrides[path[0]] = _parse_env_value(raw)
            continue
        if len(path) != 2:
            raise ConfigError(
                f"Environment variable {key} must look like RAIDA_<SECTION>__<KEY> "
                f"(exactly one double underscore)."
            )
        section, field = path
        overrides.setdefault(section, {})[field] = _parse_env_value(raw)
    return overrides


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_config_path(explicit: str | Path | None, environ: dict[str, str]) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    if CONFIG_ENV_VAR in environ:
        return Path(environ[CONFIG_ENV_VAR]).expanduser()
    return Path.cwd() / DEFAULT_CONFIG_FILENAME


def load_config(
    explicit_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """Load, merge and validate configuration. Raises ConfigError on any problem."""
    env = dict(os.environ if environ is None else environ)
    path = resolve_config_path(explicit_path, env)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    elif explicit_path is not None or CONFIG_ENV_VAR in env:
        raise ConfigError(f"Config file not found: {path}")
    data = _deep_merge(data, _env_overrides(env))
    data.setdefault("llm", {})  # so a missing section reports "llm.model: Field required"
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        hint = (
            f" (no config file at {path}; copy raida.example.toml to raida.toml "
            f"or set RAIDA_LLM__MODEL)"
            if not path.exists()
            else f" (config file: {path})"
        )
        raise ConfigError(f"Invalid configuration: {problems}{hint}") from exc
