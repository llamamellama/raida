"""Skills: saved instructions any session runs with ``@name``. Changes are broadcast as a
``skills.updated`` event so every open tab refreshes its menu."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from raida.api.deps import State
from raida.pipeline.events import Event
from raida.skills import (
    ImportResult,
    SkillConflictError,
    SkillDetail,
    SkillError,
    SkillInfo,
    SkillProblem,
    build_skill,
)
from raida.skills.store import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillPayload(BaseModel):
    """The editable fields as sent by the UI. Checked by raida's own rules (``build_skill``),
    so a mistake comes back as one readable sentence rather than a validation list."""

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


class SkillList(BaseModel):
    skills: list[SkillInfo]
    problems: list[SkillProblem]  # skill folders that could not be read, with the reason


class DeleteResult(BaseModel):
    deleted: str
    restored: SkillDetail | None = None  # the built-in skill a deleted change gave way to


def _http(exc: SkillError) -> HTTPException:
    return HTTPException(
        status_code=409 if isinstance(exc, SkillConflictError) else 400, detail=str(exc)
    )


async def _listing(state: State) -> SkillList:
    skills, problems = await asyncio.to_thread(state.scheduler.skills.list)
    return SkillList(skills=skills, problems=problems)


async def _changed(state: State) -> None:
    listing = await _listing(state)
    state.scheduler.events.publish(Event("skills.updated", listing.model_dump()))


@router.get("", response_model=SkillList)
async def list_skills(state: State) -> SkillList:
    return await _listing(state)


@router.get("/{name}", response_model=SkillDetail)
async def get_skill(name: str, state: State) -> SkillDetail:
    return await asyncio.to_thread(state.scheduler.skills.get, name)


@router.post("", response_model=SkillDetail, status_code=201)
async def create_skill(body: SkillPayload, state: State) -> SkillDetail:
    try:
        skill = build_skill(**body.model_dump())
        detail = await asyncio.to_thread(state.scheduler.skills.create, skill)
    except SkillError as exc:
        raise _http(exc) from exc
    await _changed(state)
    return detail


@router.put("/{name}", response_model=SkillDetail)
async def update_skill(name: str, body: SkillPayload, state: State) -> SkillDetail:
    """Save a skill under ``name``, renaming it when ``body.name`` differs. Saving a built-in
    skill stores the user's own version of it."""
    try:
        skill = build_skill(**body.model_dump())
        detail = await asyncio.to_thread(state.scheduler.skills.update, name, skill)
    except SkillError as exc:
        raise _http(exc) from exc
    await _changed(state)
    return detail


@router.delete("/{name}", response_model=DeleteResult)
async def delete_skill(name: str, state: State) -> DeleteResult:
    try:
        restored = await asyncio.to_thread(state.scheduler.skills.delete, name)
    except SkillError as exc:
        raise _http(exc) from exc
    await _changed(state)
    return DeleteResult(deleted=name, restored=restored)


@router.post("/import", response_model=ImportResult, status_code=201)
async def import_skill(file: UploadFile, state: State, replace: bool = False) -> ImportResult:
    """Import a SKILL.md, or a .zip or .skill archive of a skill folder (Agent Skills format).
    Only Markdown is used; scripts and other files are listed as ignored, never run."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)  # parse_upload rejects anything larger
    try:
        result = await asyncio.to_thread(
            state.scheduler.skills.import_upload, file.filename or "SKILL.md", data, replace
        )
    except SkillError as exc:
        raise _http(exc) from exc
    finally:
        await file.close()
    await _changed(state)
    return result


@router.get("/{name}/export")
async def export_skill(name: str, state: State) -> Response:
    data = await asyncio.to_thread(state.scheduler.skills.export_zip, name)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )
