"""Domain and API models shared by the database layer, pipeline and HTTP API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

SourceKind = Literal["text", "pdf", "audio", "video", "docx", "subtitles"]
SourceStatus = Literal[
    "queued",
    "extracting",
    "transcoding",
    "detecting_language",
    "transcribing",
    "rendering",
    "ocr",
    "noting",
    "ready",
    "failed",
    "cancelled",
]
ResourceClass = Literal["cpu", "gpu", "llm", "subprocess"]
JobState = Literal["queued", "running", "done", "failed", "cancelled"]
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal[
    "pending", "waiting_for_sources", "streaming", "done", "failed", "cancelled"
]
# single_shot: every source in full; notes: long sources through their notes plus passages found
# for the question; map_reduce: full text condensed for the instruction first.
Strategy = Literal["single_shot", "notes", "map_reduce"]
ExportFormat = Literal["txt", "md", "pdf", "docx"]

ACTIVE_SOURCE_STATUSES: frozenset[str] = frozenset(
    {
        "queued",
        "extracting",
        "transcoding",
        "detecting_language",
        "transcribing",
        "rendering",
        "ocr",
        "noting",
    }
)
TERMINAL_SOURCE_STATUSES: frozenset[str] = frozenset({"ready", "failed", "cancelled"})

KIND_BY_SUFFIX: dict[str, SourceKind] = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".text": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".srt": "subtitles",
    ".vtt": "subtitles",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".opus": "audio",
    ".wma": "audio",
    ".aiff": "audio",
    ".aif": "audio",
    ".mp4": "video",
    ".m4v": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "video",
    ".avi": "video",
    ".wmv": "video",
    ".mpg": "video",
    ".mpeg": "video",
}


def new_id() -> str:
    return uuid4().hex


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


class Session(BaseModel):
    id: str
    title: str
    title_auto: bool = True  # False once the user has chosen the title themselves
    created_at: str
    updated_at: str


class Source(BaseModel):
    """One file in the library (ADR-0007): processed once, when it is first added, and usable
    in any session. A session uses a source through a row in ``session_sources``."""

    id: str
    kind: SourceKind
    original_name: str  # the file's own name, as uploaded or found on disk
    # The name shown in every session and given to the model, which cites sources by it. The
    # user can rename a file; until then, and when the name is cleared, it is the file's name.
    title: str = ""
    stored_path: str
    managed: bool = True
    sha256: str
    size_bytes: int
    language: str = "auto"
    status: SourceStatus
    progress: float = 0.0
    error: str | None = None
    processed_path: str | None = None
    token_estimate: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    # Read from session_sources, not stored: how many sessions use the file and when one last
    # added it (the library lists recently used files first).
    sessions: int = 0
    last_used_at: str | None = None

    @model_validator(mode="after")
    def _title_defaults_to_file_name(self) -> Source:
        if not self.title:
            self.title = self.original_name
        return self


SOURCE_COMPUTED_FIELDS = frozenset({"sessions", "last_used_at"})


class Job(BaseModel):
    id: str
    source_id: str | None = None
    message_id: str | None = None
    stage: str
    resource_class: ResourceClass
    state: JobState
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    created_at: str


class SkillUse(BaseModel):
    """A skill as it was when a message invoked it, so the message stays reproducible after the
    skill is edited or removed."""

    name: str
    title: str
    description: str
    arguments: str = ""  # what the user typed after @name
    body: str = ""  # the skill's instructions with the arguments filled in
    language: str = "instructions"
    full_text: bool = False
    think: bool = True
    revision: str = ""  # hash of the skill's files when it ran


class Message(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    content: str = ""
    status: MessageStatus
    strategy: Strategy | None = None
    run_with_ready_only: bool = False
    full_text: bool = False  # read every source in full instead of long sources' notes
    skill: SkillUse | None = None  # set on both messages of a skill invocation
    prompt: str | None = None  # user message of a skill: the instruction the model was given
    token_usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str


class Artifact(BaseModel):
    id: str
    message_id: str
    format: ExportFormat
    path: str
    filename: str
    size_bytes: int
    created_at: str


class ProcessedCacheEntry(BaseModel):
    cache_key: str
    sha256: str
    processed_path: str
    token_estimate: int
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ProcessedSource(BaseModel):
    """Normalized document handed to the LLM. Stored as markdown plus a JSON sidecar."""

    source_id: str
    title: str
    kind: SourceKind
    meta: dict[str, Any] = Field(default_factory=dict)
    text_markdown: str
    token_estimate: int


class NoteSection(BaseModel):
    start: str = ""  # first anchor of the excerpt, e.g. "[00:00:00]" or "[p. 3]"
    end: str = ""  # last anchor of the excerpt
    text: str


class SourceNotes(BaseModel):
    """Notes on one processed document, shared by every session that uses the file."""

    key: str
    sha256: str
    model: str
    overview: str
    sections: list[NoteSection]
    token_estimate: int
    created_at: str

    def markdown(self) -> str:
        parts = [self.overview.strip()] if self.overview.strip() else []
        for section in self.sections:
            ends = (section.start, section.end if section.end != section.start else "")
            span = " - ".join(a for a in ends if a)
            parts.append((f"## {span}\n\n" if span else "") + section.text.strip())
        return "\n\n".join(parts) + "\n"


class SessionDetail(BaseModel):
    session: Session
    sources: list[Source]  # the library files this session uses, in the order they were added
    messages: list[Message]
    artifacts: list[Artifact]


class SessionSnapshot(SessionDetail):
    """What a tab receives when it connects: the session and the whole library, so it can
    offer every processed file for this session."""

    library: list[Source]


class ComponentStatus(BaseModel):
    ok: bool
    detail: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class HealthReport(BaseModel):
    ok: bool
    version: str
    llm: ComponentStatus
    transcriber: ComponentStatus
    ffmpeg: ComponentStatus
    ocr: ComponentStatus
    pdf_renderer: ComponentStatus
    storage: ComponentStatus
    data_dir: str


class ResetSummary(BaseModel):
    """What a factory reset (Nuke) deleted."""

    sessions: int
    sources: int  # library files
    skills: int  # the user's skills and changed built-in ones
    bytes_freed: int
