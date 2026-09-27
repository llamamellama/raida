# ADR-0009: The app answers only requests addressed to this Mac

- Status: accepted
- Date: 2026-09-26

## Context

raida has no login. It is safe only because it listens on 127.0.0.1, so other machines cannot
reach it. A web page from anywhere can still make the browser on this Mac send requests to
`http://127.0.0.1:8765`. The browser does not let that page read the answers, because raida
sends no CORS headers. DNS rebinding gets around that: the page's own host name is made to
resolve to 127.0.0.1, and the browser then treats raida as the page's own site. An audit on
2026-09-26 confirmed the app served the library to a request whose Host header was
`evil.example`. With the factory reset (ADR-0008), an unchecked request could also delete
everything.

## Decision

An ASGI middleware (`raida.api.security.LocalRequestsOnly`), the outermost layer, checks two
headers on every request:

1. **Host** must name this machine: `127.0.0.1`, `localhost`, `::1` or `server.host`, with any
   port. Anything else gets 400 and a message saying which address to open. A rebinding page's
   requests carry its own host name, so they are refused.
2. **Origin**, on POST, PUT, PATCH and DELETE, must be the same site as the Host, when the
   header is present. Anything else, including `null`, gets 403. Requests without an Origin
   pass: the command line, curl, and browsers that omit it on same-site requests.

Reading from another site stays blocked by the browser, since raida still sends no CORS
headers. The factory reset also requires `{"confirm": "NUKE"}` in a JSON body. A cross-site
form cannot send that without a CORS preflight, which raida does not answer.

## Consequences

- Opening raida by the Mac's network name or LAN address (for example
  `http://my-mac.local:8765`) is refused, even with `server.host = "0.0.0.0"`. That is
  intended, because raida has no login to protect it on a network.
- Tests call the app as `http://127.0.0.1`; `tests/integration/test_local_requests.py` covers
  both checks.
