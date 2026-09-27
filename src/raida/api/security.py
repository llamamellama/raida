"""Only this machine's own pages may use the API (docs/adr/0009-local-requests-only.md).

raida has no login: it listens on 127.0.0.1 only. A web page from elsewhere can still make the
browser send requests there, and by pointing its own host name at 127.0.0.1 (DNS rebinding) it
can read the answers too, since the browser then treats raida as that page's own site. Such
requests carry the page's host name in the Host header, so any name other than this machine's
is refused. A change (POST, PUT, PATCH, DELETE) sent by a page of another site carries that
site's Origin, so it is refused too; requests without an Origin (the command line, curl) pass.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hostname(netloc: str) -> str | None:
    """The host name in ``host[:port]`` ("[::1]:8765" gives "::1"), lowercase; None if
    malformed."""
    try:
        return urlsplit(f"//{netloc}").hostname
    except ValueError:
        return None


class LocalRequestsOnly:
    def __init__(self, app: ASGIApp, extra_hosts: tuple[str, ...] = ()) -> None:
        self.app = app
        self.hosts = LOOPBACK_NAMES | {h.lower() for h in extra_hosts if h}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if _hostname(host) not in self.hosts:
            response = PlainTextResponse(
                "raida only answers requests addressed to this Mac, such as "
                "http://127.0.0.1:8765/ or http://localhost:8765/.",
                status_code=400,
            )
            await response(scope, receive, send)
            return
        origin = headers.get("origin")
        if (
            scope["method"] not in SAFE_METHODS
            and origin is not None
            and urlsplit(origin).netloc.lower() != host.lower()
        ):
            response = PlainTextResponse(
                f"raida refused a change sent by a page of another site ({origin}).",
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
