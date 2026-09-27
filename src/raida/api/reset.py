"""Nuke: reset raida to factory settings (docs/adr/0008-factory-reset-and-deletable-builtins.md)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from raida.api.deps import State
from raida.models import ResetSummary

router = APIRouter(prefix="/api", tags=["reset"])

# The word the user types in the confirmation dialog. The request must carry it, so a stray or
# replayed request without it deletes nothing.
CONFIRMATION = "NUKE"


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str


@router.post("/reset", response_model=ResetSummary)
async def factory_reset(body: ResetRequest, state: State) -> ResetSummary:
    """Delete every session, library file, answer, export, skill and database backup, and
    stop all work. raida.toml, the downloaded models and files added by path are kept."""
    if body.confirm != CONFIRMATION:
        raise HTTPException(status_code=400, detail=f"Type {CONFIRMATION} to confirm.")
    return await state.scheduler.factory_reset()
