"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from raida import __version__
from raida.api import exports, health, messages, sessions, sources
from raida.api.deps import AppState
from raida.config import Config
from raida.db import Database
from raida.db.repo import NotFoundError
from raida.model_env import apply_model_env
from raida.models import HealthReport
from raida.pipeline.scheduler import Scheduler

log = logging.getLogger(__name__)


def web_root() -> Path:
    return Path(str(resources.files("raida").joinpath("web")))


def create_app(config: Config, initial_health: HealthReport | None = None) -> FastAPI:
    config.ensure_dirs()
    apply_model_env(config)
    db = Database(config.db_path)
    scheduler = Scheduler(config, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await scheduler.start()
        if initial_health is not None:
            scheduler.publish_health(initial_health)
        log.info(
            "app_started", extra={"version": __version__, "data_dir": str(config.paths.data_dir)}
        )
        try:
            yield
        finally:
            await scheduler.stop()
            db.close()
            log.info("app_stopped")

    app = FastAPI(title="raida", version=__version__, lifespan=lifespan)
    app.state.raida = AppState(config=config, db=db, scheduler=scheduler)

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(sources.router)
    app.include_router(messages.router)
    app.include_router(exports.router)

    root = web_root()
    app.mount("/static", StaticFiles(directory=root), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(root / "index.html")

    return app
