"""Skills: saved, reusable instructions a user runs in any session with ``@name``.

See ``docs/skills.md`` for the file format and ``docs/adr/0006-skills.md`` for the design.
"""

from raida.skills.model import (
    Skill,
    SkillConflictError,
    SkillDetail,
    SkillError,
    SkillFields,
    SkillInfo,
    build_skill,
    parse_skill_md,
    render_skill_md,
)
from raida.skills.render import Command, parse_command, render, unescape
from raida.skills.store import ImportResult, SkillNotFoundError, SkillProblem, SkillStore

__all__ = [
    "Command",
    "ImportResult",
    "Skill",
    "SkillConflictError",
    "SkillDetail",
    "SkillError",
    "SkillFields",
    "SkillInfo",
    "SkillNotFoundError",
    "SkillProblem",
    "SkillStore",
    "build_skill",
    "parse_command",
    "parse_skill_md",
    "render",
    "render_skill_md",
    "unescape",
]
