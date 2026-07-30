"""The HTTP transport for the access layer: sockets, headers, JSON.

Thin on purpose. Every decision about *what* an endpoint returns lives in
:mod:`blockchain.access.routes`; this module only reads a request, hands it over,
and writes the answer back.

Built on the standard library's :mod:`http.server` rather than a framework, so
the project keeps its single dependency on py-ipv8. That is a real constraint of
the assignment, not an aesthetic preference, and it costs little here: the API is
a dozen endpoints serving one local browser.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from blockchain.access.node import FarmNode
from blockchain.access.routes import ApiError, handle

#: Refuse a body larger than this rather than read it into memory. Requests here
#: are small JSON objects; anything bigger is a mistake or an attack.
MAX_BODY_BYTES = 64 * 1024


class ApiHandler(BaseHTTPRequestHandler):
    """Serves one request against the node in :attr:`node`."""

    #: Injected by :func:`build_server` via a subclass, since
    #: ``BaseHTTPRequestHandler`` is instantiated per request by the server.
    node: FarmNode

    protocol_version = "HTTP/1.1"
    server_version = "BoniatoChain/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802 - name fixed by the base class
        """Answer a CORS preflight so a browser on another port may proceed."""
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    # -- internals ------------------------------------------------------------

    def _serve(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._read_body() if method == "POST" else None
            status, payload = handle(
                self.node, method, parsed.path, parse_qs(parsed.query), body
            )
        except ApiError as error:
            self._respond(error.status, {"error": error.message})
        except Exception as error:  # pragma: no cover - last-resort safety net
            # A node that dies on a malformed request is a node that cannot be
            # demoed. Answer 500 and keep serving.
            self._respond(500, {"error": f"internal error: {error}"})
        else:
            self._respond(status, payload)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "request body too large")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "body must be valid JSON") from None
        if not isinstance(parsed, dict):
            raise ApiError(400, "body must be a JSON object")
        return parsed

    def _respond(self, status: int, payload: dict | list) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def _send_cors_headers(self) -> None:
        """Wide-open CORS.

        Demo-only, and stated as such in ``docs/api.md``. It lets a Vite dev
        server on another port call the API directly. A deployed node would name
        its origins instead; there is nothing to protect here because the node is
        local, holds throwaway keys and can be restarted from genesis.
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args) -> None:
        """Log one line per request, prefixed so it is clear who is talking."""
        print(f"  api  {self.address_string()}  {format % args}")


def build_server(
    node: FarmNode, host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    """A threaded HTTP server serving ``node``.

    Threaded because mining blocks while a browser polls would otherwise make the
    poll wait for the Proof-of-Work. :class:`~blockchain.access.node.FarmNode`
    locks its own mutations, which is what makes that safe.
    """
    handler = type("BoundApiHandler", (ApiHandler,), {"node": node})
    return ThreadingHTTPServer((host, port), handler)
