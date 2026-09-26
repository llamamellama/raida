"""FastAPI application factory."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from raida import __version__
from raida.api import exports, health, messages, sessions, skills, sources
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


def load_ui(root: Path) -> dict[str, bytes]:
    """The UI files as they are when the server starts, by path relative to ``root``. The
    server serves these and never the files on disk: files change while the app runs (an
    update, an editor, an agent), and a page given scripts newer than the server it talks to
    could not even list the sessions."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def asset_version(files: dict[str, bytes]) -> str:
    """A hash of the UI files. The page loads them under /static/<hash>/, so a browser that
    cached an older UI (before no-cache headers existed, or heuristically) cannot mix old and
    new modules after an update: a changed file is a new URL."""
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode())
        digest.update(data)
    return digest.hexdigest()[:12]


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

    ui = load_ui(web_root())
    version = asset_version(ui)
    app = FastAPI(title="raida", version=__version__, lifespan=lifespan)
    app.state.raida = AppState(config=config, db=db, scheduler=scheduler, ui_version=version)

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(sources.router)
    app.include_router(messages.router)
    app.include_router(exports.router)
    app.include_router(skills.router)

    @app.middleware("http")
    async def _revalidate_ui(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Without this, browsers cache the UI's scripts heuristically for hours; with it they
        # revalidate, a cheap 304 on localhost.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    etag = f'"{version}"'

    @app.get("/static/{path:path}", include_in_schema=False)
    async def static(path: str, request: Request) -> Response:
        # /static/<version>/<file> is what the page asks for; /static/<file> works too. Edited
        # files are served after a restart, together with the server code that matches them.
        head, _, rest = path.partition("/")
        name = rest if head == version else path
        data = ui.get(name)
        if data is None:
            raise HTTPException(status_code=404, detail=f"no UI file {name}")
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(data, media_type=media_type, headers={"ETag": etag})

    # The page learns its own version here and the server's on every connection (sse.py), so
    # a tab left open while raida was updated reloads instead of running the old UI.
    page = (
        ui["index.html"]
        .decode("utf-8")
        .replace('"/static/', f'"/static/{version}/')
        .replace("{{ui_version}}", version)
    )

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(page)

    return app
