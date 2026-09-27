"""Skills on disk: the built-in ones shipped with raida and the user's, one folder per skill.

User skills live in ``<data_dir>/skills/<name>/``. A user skill with the name of a built-in one
replaces it (origin "override"); resetting it brings the built-in back. Built-in skills can be
deleted too: the package's folders stay, and their names are listed in
``<data_dir>/skills/.deleted-builtins`` until the built-in skills are restored. Files are read
on every call, so a skill edited in a text editor is picked up without a restart.
"""

from __future__ import annotations

import io
import logging
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from raida.db.repo import NotFoundError
from raida.skills.model import (
    EXAMPLE_PATH,
    NAME_PATTERN,
    REFERENCE_PATH,
    Skill,
    SkillConflictError,
    SkillDetail,
    SkillError,
    SkillInfo,
    SkillOrigin,
    parse_skill_md,
    render_skill_md,
)

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
# Names of deleted built-in skills, one per line. A dot file: the folder scan skips it.
DELETED_BUILTINS_FILE = ".deleted-builtins"
# Where raida 0.1 kept the two files before following the format's directories; still read.
LEGACY_EXAMPLE = "example.md"
LEGACY_REFERENCE = "reference.md"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024


class SkillNotFoundError(NotFoundError):
    pass


class SkillProblem(BaseModel):
    """A skill folder that could not be read, shown in the skills list instead of failing it."""

    path: str
    error: str


class ImportResult(BaseModel):
    skill: SkillDetail
    ignored: list[str]  # files in the upload raida does not use (scripts, images, ...)


def builtin_dir() -> Path:
    return Path(str(resources.files("raida.skills").joinpath("builtin")))


def _read_optional(*paths: Path) -> str:
    return next((p.read_text("utf-8") for p in paths if p.is_file()), "")


def read_folder(folder: Path) -> Skill:
    """Read one skill folder. The folder name is the skill's name, as the format requires."""
    if not NAME_PATTERN.match(folder.name):
        raise SkillError(f"folder name {folder.name!r} is not a valid skill name")
    skill = parse_skill_md(
        (folder / SKILL_FILE).read_text("utf-8"),
        fallback_name=folder.name,
        example=_read_optional(folder / EXAMPLE_PATH, folder / LEGACY_EXAMPLE),
        reference=_read_optional(folder / REFERENCE_PATH, folder / LEGACY_REFERENCE),
    )
    if skill.name != folder.name:
        log.warning(
            "skill_name_mismatch", extra={"folder": str(folder), "frontmatter_name": skill.name}
        )
        skill = skill.model_copy(update={"name": folder.name})
    return skill


def _mtime(folder: Path) -> str:
    newest = max(
        (p.stat().st_mtime for p in folder.rglob("*") if p.is_file()),
        default=folder.stat().st_mtime,
    )
    return datetime.fromtimestamp(newest, tz=UTC).isoformat(timespec="seconds")


class SkillStore:
    def __init__(self, user_dir: Path, builtin: Path | None = None) -> None:
        self.user_dir = user_dir
        self.builtin = builtin if builtin is not None else builtin_dir()

    # -- reading ---------------------------------------------------------------------------

    def _scan(self, root: Path) -> tuple[dict[str, tuple[Skill, Path]], list[SkillProblem]]:
        found: dict[str, tuple[Skill, Path]] = {}
        problems: list[SkillProblem] = []
        if not root.is_dir():
            return found, problems
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            if not (folder / SKILL_FILE).is_file():
                problems.append(SkillProblem(path=str(folder), error=f"no {SKILL_FILE}"))
                continue
            try:
                found[folder.name] = (read_folder(folder), folder)
            except (SkillError, OSError, UnicodeDecodeError) as exc:
                problems.append(SkillProblem(path=str(folder / SKILL_FILE), error=str(exc)))
        return found, problems

    def _all(self) -> tuple[dict[str, SkillDetail], list[SkillProblem]]:
        builtins, problems = self._scan(self.builtin)
        users, user_problems = self._scan(self.user_dir)
        deleted = self._deleted()
        merged: dict[str, SkillDetail] = {}
        for name, (skill, folder) in builtins.items():
            if name in deleted:
                continue
            # A changed copy that cannot be read is still a change: listed as one (its problem
            # is reported too), so the list offers Reset and Delete to repair it.
            origin: SkillOrigin = "override" if (self.user_dir / name).is_dir() else "builtin"
            merged[name] = self._detail(skill, origin, folder)
        for name, (skill, folder) in users.items():
            origin = "override" if name in builtins and name not in deleted else "user"
            merged[name] = self._detail(skill, origin, folder)
        return dict(sorted(merged.items())), problems + user_problems

    def _deleted(self) -> set[str]:
        """Names of the built-in skills the user deleted."""
        try:
            lines = (self.user_dir / DELETED_BUILTINS_FILE).read_text("utf-8").splitlines()
        except FileNotFoundError:
            return set()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("deleted_builtins_unreadable", extra={"error": str(exc)})
            return set()
        return {line.strip() for line in lines if NAME_PATTERN.match(line.strip())}

    def _set_deleted(self, names: set[str]) -> None:
        path = self.user_dir / DELETED_BUILTINS_FILE
        if not names:
            path.unlink(missing_ok=True)
            return
        self.user_dir.mkdir(parents=True, exist_ok=True)
        staging = self.user_dir / f"{DELETED_BUILTINS_FILE}.{uuid.uuid4().hex}.tmp"
        staging.write_text("".join(f"{name}\n" for name in sorted(names)), "utf-8")
        staging.replace(path)

    def deleted_builtins(self) -> list[str]:
        """The deleted built-in skills that restore_builtins() would bring back."""
        return sorted(name for name in self._deleted() if self._is_builtin(name))

    @staticmethod
    def _detail(skill: Skill, origin: SkillOrigin, folder: Path) -> SkillDetail:
        fields = skill.model_dump(exclude={"extra"})
        return SkillDetail(**fields, origin=origin, updated_at=_mtime(folder))

    def list(self) -> tuple[list[SkillInfo], list[SkillProblem]]:
        skills, problems = self._all()
        infos = [
            SkillInfo(**d.model_dump(include=set(SkillInfo.model_fields))) for d in skills.values()
        ]
        return infos, problems

    def get(self, name: str) -> SkillDetail:
        detail = self._all()[0].get(name)
        if detail is None:
            raise SkillNotFoundError(f"no skill named {name!r}")
        return detail

    def load(self, name: str) -> Skill:
        """The skill with its preserved extra frontmatter, for running and exporting."""
        if NAME_PATTERN.match(name):
            roots = [self.user_dir]
            if name not in self._deleted():
                roots.append(self.builtin)
            for root in roots:
                folder = root / name
                if (folder / SKILL_FILE).is_file():
                    return read_folder(folder)
        raise SkillNotFoundError(f"no skill named {name!r}")

    def names(self) -> list[str]:
        return list(self._all()[0])

    def _is_builtin(self, name: str) -> bool:
        return (self.builtin / name / SKILL_FILE).is_file()

    def _taken(self, name: str) -> bool:
        """A readable skill has this name. A folder that cannot be read does not count: saving
        or importing a skill with its name replaces it, which is how the user repairs it."""
        return name in self._all()[0]

    # -- writing ---------------------------------------------------------------------------

    def _write(self, skill: Skill) -> None:
        """Write a user skill folder atomically: a half-written skill is never visible."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        target = self.user_dir / skill.name
        staging = self.user_dir / f".{skill.name}.{uuid.uuid4().hex}.tmp"
        staging.mkdir()
        try:
            (staging / SKILL_FILE).write_text(render_skill_md(skill), "utf-8")
            for relative, text in (
                (EXAMPLE_PATH, skill.example),
                (REFERENCE_PATH, skill.reference),
            ):
                if text:
                    (staging / relative).parent.mkdir(exist_ok=True)
                    (staging / relative).write_text(text + "\n", "utf-8")
            if target.exists():
                retired = self.user_dir / f".{skill.name}.{uuid.uuid4().hex}.old"
                target.rename(retired)
                staging.rename(target)
                shutil.rmtree(retired, ignore_errors=True)
            else:
                staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        log.info("skill_saved", extra={"skill": skill.name})

    def create(self, skill: Skill) -> SkillDetail:
        if self._taken(skill.name):
            raise SkillConflictError(f"a skill named {skill.name!r} already exists")
        self._write(skill)
        return self.get(skill.name)

    def update(self, name: str, skill: Skill) -> SkillDetail:
        """Save changes to a skill. Saving a built-in one stores the user's own version."""
        current = self.get(name)
        if skill.name != name:
            if current.origin != "user":
                raise SkillError(
                    f"{name!r} is a built-in skill and keeps its name; duplicate it to save it "
                    "under another name"
                )
            if self._taken(skill.name):
                raise SkillConflictError(f"a skill named {skill.name!r} already exists")
        if not skill.extra:
            # Keep frontmatter from an import (license, other metadata) across edits in the UI.
            skill = skill.model_copy(update={"extra": self._kept_frontmatter(name)})
        self._write(skill)
        if skill.name != name:
            shutil.rmtree(self.user_dir / name, ignore_errors=True)
        return self.get(skill.name)

    def _kept_frontmatter(self, name: str) -> dict[str, Any]:
        """Frontmatter raida does not use, from the user's copy or else the built-in one. A
        copy that cannot be read keeps nothing: saving over it is how the user repairs it."""
        for root in (self.user_dir, self.builtin):
            folder = root / name
            if (folder / SKILL_FILE).is_file():
                try:
                    return read_folder(folder).extra
                except (SkillError, OSError, UnicodeDecodeError):
                    continue
        return {}

    def delete(self, name: str) -> None:
        """Delete a skill: the user's own, or a built-in one together with any changes to it.
        A deleted built-in skill stays away until restore_builtins()."""
        try:
            self.get(name)
        except SkillNotFoundError:
            # A folder that cannot be read is not listed as a skill, but can still be removed.
            if not (NAME_PATTERN.match(name) and (self.user_dir / name).is_dir()):
                raise
        if (self.user_dir / name).is_dir():
            shutil.rmtree(self.user_dir / name)
        if self._is_builtin(name):
            self._set_deleted(self._deleted() | {name})
        log.info("skill_deleted", extra={"skill": name})

    def reset(self, name: str) -> SkillDetail:
        """Discard the user's changes to a built-in skill and return the original."""
        if self.get(name).origin != "override":
            raise SkillError(f"{name!r} has no changes to discard")
        shutil.rmtree(self.user_dir / name)
        log.info("skill_reset", extra={"skill": name})
        return self.get(name)

    def restore_builtins(self) -> list[str]:
        """Bring back every deleted built-in skill; returns their names."""
        restored = self.deleted_builtins()
        self._set_deleted(set())
        if restored:
            log.info("builtin_skills_restored", extra={"skills": restored})
        return restored

    # -- import and export -----------------------------------------------------------------

    def import_upload(self, filename: str, data: bytes, replace: bool = False) -> ImportResult:
        skill, ignored = parse_upload(filename, data)
        if self._taken(skill.name) and not replace:
            raise SkillConflictError(f"a skill named {skill.name!r} already exists")
        self._write(skill)
        return ImportResult(skill=self.get(skill.name), ignored=ignored)

    def export_zip(self, name: str) -> bytes:
        """A .zip holding the skill folder, importable by raida and other Agent Skills tools."""
        skill = self.load(name)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{name}/{SKILL_FILE}", render_skill_md(skill))
            if skill.example:
                archive.writestr(f"{name}/{EXAMPLE_PATH}", skill.example + "\n")
            if skill.reference:
                archive.writestr(f"{name}/{REFERENCE_PATH}", skill.reference + "\n")
        return buffer.getvalue()


def parse_upload(filename: str, data: bytes) -> tuple[Skill, list[str]]:
    """Read an uploaded SKILL.md, or a .zip or .skill archive of a skill folder. Nothing is
    extracted to disk and nothing in the upload is run: only Markdown text is used."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise SkillError(f"the file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    lower = filename.lower()
    if lower.endswith(".md"):
        stem = PurePosixPath(filename).stem
        fallback = stem.lower() if NAME_PATTERN.match(stem.lower()) and stem != "SKILL" else None
        return parse_skill_md(_decode(data, filename), fallback_name=fallback), []
    if lower.endswith((".zip", ".skill")):
        return _parse_archive(data)
    raise SkillError("import a SKILL.md file, or a .zip or .skill archive of a skill folder")


def _decode(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"{name} is not UTF-8 text") from exc


def _parse_archive(data: bytes) -> tuple[Skill, list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("the archive is not a valid .zip file") from exc
    with archive:
        files = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and not PurePosixPath(info.filename).name.startswith(".")
            and "__MACOSX" not in PurePosixPath(info.filename).parts
        ]
        if sum(info.file_size for info in files) > MAX_UNPACKED_BYTES:
            raise SkillError("the archive unpacks to more than 4 MB; a skill is a few text files")
        skill_files = [i for i in files if PurePosixPath(i.filename).name == SKILL_FILE]
        if not skill_files:
            raise SkillError(f"the archive has no {SKILL_FILE}")
        depth = min(len(PurePosixPath(i.filename).parts) for i in skill_files)
        top = [i for i in skill_files if len(PurePosixPath(i.filename).parts) == depth]
        if len(top) > 1:
            raise SkillError("the archive holds several skills; import them one at a time")
        root = PurePosixPath(top[0].filename).parent

        def read(info: zipfile.ZipInfo) -> str:
            with archive.open(info) as handle:
                raw = handle.read(MAX_UNPACKED_BYTES + 1)
            if len(raw) > MAX_UNPACKED_BYTES:
                raise SkillError(f"{info.filename} is too large")
            return _decode(raw, info.filename)

        by_path = {PurePosixPath(i.filename): i for i in files}
        used = {root / SKILL_FILE}
        example = ""
        for candidate in (EXAMPLE_PATH, LEGACY_EXAMPLE, "assets/template.md"):
            if root / candidate in by_path:
                example = read(by_path[root / candidate])
                used.add(root / candidate)
                break
        # raida's own reference file first, then any other Markdown under references/ (other
        # tools split documentation into several files), each under its file name.
        main = [root / p for p in (REFERENCE_PATH, LEGACY_REFERENCE) if root / p in by_path][:1]
        others = sorted(
            p
            for p in by_path
            if p.parent == root / "references" and p.suffix.lower() == ".md" and p not in main
        )
        reference_parts = [read(by_path[p]).strip() for p in main]
        if others:
            reference_parts += [f"## {p.name}\n\n{read(by_path[p]).strip()}" for p in others]
        used.update(main, others)
        fallback = root.name if root.name and NAME_PATTERN.match(root.name) else None
        skill = parse_skill_md(
            read(by_path[root / SKILL_FILE]),
            fallback_name=fallback,
            example=example,
            reference="\n\n".join(p.strip() for p in reference_parts),
        )
        ignored = sorted(str(p) for p in by_path if p not in used)
    return skill, ignored
