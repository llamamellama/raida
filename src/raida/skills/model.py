"""A skill: a saved instruction that runs in any session as ``@name``.

On disk a skill is a folder in the Agent Skills layout (agentskills.io), the open format that
Claude, ChatGPT, Codex, Gemini, Copilot and Cursor load: ``SKILL.md`` starts with YAML
frontmatter (``name``, ``description``) followed by the instructions in Markdown. raida adds
two optional files in the format's directories, ``assets/example.md`` (an example of the output
format) and ``references/reference.md`` (terminology, names and conventions to apply), and
keeps its own options in the free-form ``metadata`` map under a ``raida-`` prefix, so the
folder stays valid for those tools. The SKILL.md raida writes ends with a marked block that
points to the two files, so other tools read them too; raida drops the block when reading, as
it puts the files into the prompt itself. Frontmatter raida does not use is kept and written
back, so an imported skill exports as it came.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from raida.llm.tokens import estimate_tokens
from raida.transcribe.languages import WRITTEN_LANGUAGE_NAMES, written_language_name

# The Agent Skills name rule: lowercase letters, digits and single hyphens, at most 64.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_TITLE = 80
MAX_DESCRIPTION = 1024  # the Agent Skills limit
MAX_HINT = 200
# Instructions, example and reference go into the question's prompt; keep them affordable.
MAX_TEXT_TOKENS = 8_000
# Frontmatter is a few lines of metadata. The cap, with aliases refused, keeps a crafted file
# from expanding into gigabytes (YAML aliases nest: a 500-byte file can stand for 200 GB).
MAX_FRONTMATTER_CHARS = 16_384
CHARS_PER_TOKEN = 3.7
# How the answer's language is chosen, besides a language code from WRITTEN_LANGUAGE_NAMES:
# "instructions" = the language the instructions (and anything typed after @name) are in,
# "sources" = the main language of the session's sources.
LANGUAGE_MODES = ("instructions", "sources")

SkillOrigin = Literal["builtin", "user", "override"]

_RAIDA_KEYS = {
    "title": "raida-title",
    "argument_hint": "raida-argument-hint",
    "language": "raida-language",
    "full_text": "raida-full-text",
    "think": "raida-think",
}
EXAMPLE_PATH = "assets/example.md"
REFERENCE_PATH = "references/reference.md"
FILES_MARKER = "<!-- raida: files -->"
_POINTERS = {
    "example": f"Follow the output format in [{EXAMPLE_PATH}]({EXAMPLE_PATH}).",
    "reference": f"Apply the terms and conventions in [{REFERENCE_PATH}]({REFERENCE_PATH}).",
}
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)


class SkillError(ValueError):
    """A skill that cannot be read or saved, with a message for the user."""


class _NoAliasLoader(yaml.SafeLoader):
    """YAML without aliases (``*name``), which no skill needs and which can multiply a tiny
    file into an enormous value."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            mark = self.peek_event().start_mark
            raise SkillError(
                f"SKILL.md frontmatter uses a YAML alias (line {mark.line + 1}); write the value "
                "out instead"
            )
        return super().compose_node(parent, index)


class SkillConflictError(SkillError):
    """A skill with that name already exists."""


def title_from_name(name: str) -> str:
    return name.replace("-", " ").strip().capitalize() or name


def _canonical_language(value: str) -> str:
    if value in LANGUAGE_MODES:
        return value
    wanted = value.replace("_", "-").lower()
    return next((code for code in WRITTEN_LANGUAGE_NAMES if code.lower() == wanted), value)


class SkillFields(BaseModel):
    """What a user edits. Also the body of create and update requests."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str = ""
    description: str
    instructions: str
    example: str = ""
    reference: str = ""
    argument_hint: str = ""
    language: str = "instructions"
    full_text: bool = False
    think: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if len(value) > MAX_NAME or not NAME_PATTERN.match(value):
            raise ValueError(
                "must be 1 to 64 lowercase letters, digits and single hyphens, for example "
                "'meeting-minutes'"
            )
        return value

    @field_validator("title", "argument_hint")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("description", "instructions")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("example", "reference")
    @classmethod
    def _optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        value = _canonical_language(value.strip() or "instructions")
        if value not in LANGUAGE_MODES and written_language_name(value) is None:
            raise ValueError(
                f"must be one of {', '.join(LANGUAGE_MODES)} or a language code such as "
                f"en, zh-TW or fr; got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _limits(self) -> SkillFields:
        if not self.title:
            self.title = title_from_name(self.name)
        if len(self.title) > MAX_TITLE:
            raise ValueError(f"title: at most {MAX_TITLE} characters")
        if len(self.description) > MAX_DESCRIPTION:
            raise ValueError(f"description: at most {MAX_DESCRIPTION} characters")
        if len(self.argument_hint) > MAX_HINT:
            raise ValueError(f"argument_hint: at most {MAX_HINT} characters")
        size = sum(
            estimate_tokens(text, CHARS_PER_TOKEN)
            for text in (self.instructions, self.example, self.reference)
        )
        if size > MAX_TEXT_TOKENS:
            raise ValueError(
                f"instructions, example and reference are about {size:,} tokens together; "
                f"keep them under {MAX_TEXT_TOKENS:,}, because they are read with every run"
            )
        return self


class Skill(SkillFields):
    # Frontmatter raida does not use (license, compatibility, other metadata), kept for export.
    extra: dict[str, Any] = Field(default_factory=dict)


class SkillInfo(BaseModel):
    """A skill as listed in the UI's menu."""

    name: str
    title: str
    description: str
    argument_hint: str = ""
    language: str = "instructions"
    full_text: bool = False
    think: bool = True
    origin: SkillOrigin
    updated_at: str | None = None


class SkillDetail(SkillFields):
    origin: SkillOrigin
    updated_at: str | None = None


def build_skill(**fields: Any) -> Skill:
    """Validate fields into a Skill, turning validation errors into one readable SkillError."""
    try:
        return Skill.model_validate(fields)
    except ValidationError as exc:
        problems = "; ".join(
            (f"{'.'.join(str(p) for p in err['loc'])}: " if err["loc"] else "")
            + str(err["msg"]).removeprefix("Value error, ")
            for err in exc.errors()
        )
        raise SkillError(f"Invalid skill: {problems}") from exc


def _text(value: Any, key: str) -> str:
    """A frontmatter value raida uses as text: a string, number or boolean, never a list or map
    (whose text form could be huge)."""
    if value is None:
        return ""
    if isinstance(value, str | int | float | bool):
        return str(value)
    raise SkillError(f"Invalid skill: {key} must be text, not a {type(value).__name__}")


def _flag(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    raise SkillError(f"Invalid skill: metadata {key} must be true or false, not {value!r}")


def parse_skill_md(
    text: str, *, fallback_name: str | None = None, example: str = "", reference: str = ""
) -> Skill:
    """Read a SKILL.md file. ``fallback_name`` names a skill whose frontmatter has no name."""
    text = text.lstrip("\ufeff")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillError(
            "SKILL.md must start with YAML frontmatter between two '---' lines, giving at "
            "least a name and a description"
        )
    if len(match.group(1)) > MAX_FRONTMATTER_CHARS:
        raise SkillError(
            f"SKILL.md frontmatter is longer than {MAX_FRONTMATTER_CHARS:,} characters; it should "
            "hold a few lines of metadata"
        )
    try:
        data = yaml.load(match.group(1), Loader=_NoAliasLoader) or {}  # a SafeLoader
    except SkillError:
        raise
    except yaml.YAMLError as exc:
        raise SkillError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        # PyYAML raises these for values it cannot build, such as the date 2025-02-29.
        raise SkillError(f"SKILL.md frontmatter has a value that cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError("SKILL.md frontmatter must be a set of 'key: value' lines")
    data = {str(k): v for k, v in data.items()}
    metadata = data.pop("metadata", None) or {}
    if not isinstance(metadata, dict):
        raise SkillError("SKILL.md frontmatter 'metadata' must be a set of 'key: value' lines")
    metadata = {str(k): v for k, v in metadata.items()}
    fields: dict[str, Any] = {
        "name": _text(data.pop("name", None), "name") or fallback_name or "",
        "description": _text(data.pop("description", None), "description"),
        "instructions": text[match.end() :].split(FILES_MARKER)[0],
        "example": example,
        "reference": reference,
    }
    # Claude Code puts the hint for arguments at the top level.
    hint = data.pop("argument-hint", None)
    for field, key in _RAIDA_KEYS.items():
        if key in metadata:
            value = metadata.pop(key)
            if field in ("full_text", "think"):
                fields[field] = _flag(value, key)
            else:
                fields[field] = _text(value, key)
    if "argument_hint" not in fields and hint is not None:
        fields["argument_hint"] = _text(hint, "argument-hint")
    extra = dict(data)
    if metadata:
        extra["metadata"] = metadata
    return build_skill(**fields, extra=extra)


def render_skill_md(skill: Skill | SkillFields) -> str:
    """The SKILL.md text for a skill: frontmatter in the Agent Skills format, then its
    instructions."""
    extra = dict(getattr(skill, "extra", {}) or {})
    metadata = {str(k): str(v) for k, v in (extra.pop("metadata", None) or {}).items()}
    metadata.update(
        {
            "raida-title": skill.title,
            "raida-language": skill.language,
            "raida-full-text": "true" if skill.full_text else "false",
            "raida-think": "true" if skill.think else "false",
        }
    )
    if skill.argument_hint:
        metadata["raida-argument-hint"] = skill.argument_hint
    front: dict[str, Any] = {"name": skill.name, "description": skill.description}
    front.update({k: v for k, v in extra.items() if k not in ("name", "description")})
    front["metadata"] = metadata
    # A wide line limit keeps each value on one line, which is easier to edit by hand.
    dumped = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=10_000)
    pointers = [_POINTERS[kind] for kind in ("example", "reference") if getattr(skill, kind)]
    files = f"\n\n{FILES_MARKER}\n" + "\n".join(pointers) if pointers else ""
    return f"---\n{dumped}---\n\n{skill.instructions.strip()}{files}\n"
