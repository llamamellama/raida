"""Running a skill: ``@name what the user typed`` becomes the instruction the model is given.

The instructions come first, with ``$ARGUMENTS`` replaced by what the user typed after the
command (the placeholder Claude Code uses); without the placeholder, the text is added at the
end. Then the reference and the output example follow, each marked for what it is, so the
model applies the reference and imitates the example's form without taking content from it.
All of it goes into the final turn of the prompt: the sources stay a stable, cached prefix.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from raida.models import SkillUse
from raida.script import han_script
from raida.skills.model import Skill, render_skill_md

PLACEHOLDER = "$ARGUMENTS"
_COMMAND = re.compile(r"@([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)")

REFERENCE_INTRO = (
    "Reference for this task: terminology, names and conventions to apply. It is not a source; "
    "do not summarize or cite it."
)
EXAMPLE_INTRO = (
    "Output format: write the answer in the structure, heading levels, length and tone of this "
    "example. Text in angle brackets describes what goes there; write every heading and label "
    "in the language of the answer, not the example's. The example shows the form only; take "
    "every fact from the sources, never from the example."
)
_ADDITIONS = {
    "hant": "補充說明：",
    "hans": "补充说明：",
}
ENGLISH_ADDITIONS = "Additional instructions: "


@dataclass(frozen=True)
class Command:
    name: str
    arguments: str


def parse_command(content: str) -> Command | None:
    """``@name rest`` -> Command(name, rest). A leading ``@@`` is an escaped at sign, not a
    skill; so is anything that is not a lowercase skill name ("@Anna", "@ noon")."""
    text = content.strip()
    if text.startswith("@@"):
        return None
    match = _COMMAND.match(text)
    if match is None:
        return None
    return Command(name=match.group(1), arguments=text[match.end() :].strip())


def unescape(content: str) -> str:
    """Text the user started with ``@@`` to send a literal at sign."""
    stripped = content.strip()
    return stripped[1:] if stripped.startswith("@@") else stripped


def fill(instructions: str, arguments: str) -> str:
    """Instructions with the user's arguments in place of $ARGUMENTS, or added at the end."""
    arguments = arguments.strip()
    if PLACEHOLDER in instructions:
        return instructions.replace(PLACEHOLDER, arguments).strip()
    if not arguments:
        return instructions.strip()
    label = _ADDITIONS.get(han_script(instructions) or "", ENGLISH_ADDITIONS)
    return f"{instructions.strip()}\n\n{label}{arguments}"


def render(skill: Skill, arguments: str) -> tuple[SkillUse, str]:
    """The record kept with the message, and the instruction the model is given."""
    body = fill(skill.instructions, arguments)
    parts = [body]
    if skill.reference:
        parts.append(f"{REFERENCE_INTRO}\n<reference>\n{skill.reference}\n</reference>")
    if skill.example:
        parts.append(f"{EXAMPLE_INTRO}\n<example>\n{skill.example}\n</example>")
    use = SkillUse(
        name=skill.name,
        title=skill.title,
        description=skill.description,
        arguments=arguments.strip(),
        body=body,
        language=skill.language,
        full_text=skill.full_text,
        think=skill.think,
        revision=revision(skill),
    )
    return use, "\n\n".join(parts)


def revision(skill: Skill) -> str:
    """A short hash of everything in the skill's folder, recorded on each run."""
    content = "\x1e".join((render_skill_md(skill), skill.example, skill.reference))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def history_text(content: str, use: SkillUse) -> str:
    """How an earlier skill run appears in the conversation history: the command and what the
    skill does, not its full prompt."""
    return f'{content}\n(Ran the skill "{use.title}": {use.description})'
