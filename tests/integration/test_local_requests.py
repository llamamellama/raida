"""Only this machine's own pages may use the API: requests for another host name (DNS
rebinding) and changes sent by another site's page are refused."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
async def test_this_machine_is_served(client: httpx.AsyncClient, host: str) -> None:
    r = await client.get("/api/health", headers={"Host": host})
    assert r.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example"])
async def test_other_host_names_are_refused(client: httpx.AsyncClient, host: str) -> None:
    for path in ("/", "/api/health", "/api/library", "/api/sessions"):
        r = await client.get(path, headers={"Host": host})
        assert r.status_code == 400 and "this Mac" in r.text, path


async def test_changes_from_another_site_are_refused(client: httpx.AsyncClient) -> None:
    foreign = await client.post(
        "/api/sessions", json={}, headers={"Origin": "https://evil.example"}
    )
    assert foreign.status_code == 403 and "another site" in foreign.text
    nuke = await client.post(
        "/api/reset", json={"confirm": "NUKE"}, headers={"Origin": "http://localhost:3000"}
    )
    assert nuke.status_code == 403
    assert (
        await client.post("/api/sessions", json={}, headers={"Origin": "null"})
    ).status_code == 403
    assert (await client.get("/api/sessions")).json() == []  # nothing was created

    own = await client.post("/api/sessions", json={}, headers={"Origin": "http://127.0.0.1"})
    assert own.status_code == 201
    no_origin = await client.post("/api/sessions", json={})  # the command line, curl
    assert no_origin.status_code == 201
    # Reading is allowed from anywhere on this machine; the browser keeps other sites from
    # seeing the answer (no CORS headers).
    read = await client.get("/api/sessions", headers={"Origin": "https://evil.example"})
    assert read.status_code == 200 and "access-control-allow-origin" not in read.headers
