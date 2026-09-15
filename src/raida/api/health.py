from __future__ import annotations

from fastapi import APIRouter

from raida.api.deps import State
from raida.doctor import run_health
from raida.models import HealthReport

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthReport)
async def health(state: State) -> HealthReport:
    report = await run_health(state.config)
    state.scheduler.publish_health(report)
    return report
