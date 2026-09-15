"""Application state shared by route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from raida.config import Config
from raida.db import Database

if TYPE_CHECKING:
    from raida.pipeline.scheduler import Scheduler


@dataclass
class AppState:
    config: Config
    db: Database
    scheduler: Scheduler


def get_state(request: Request) -> AppState:
    return request.app.state.raida


State = Annotated[AppState, Depends(get_state)]
