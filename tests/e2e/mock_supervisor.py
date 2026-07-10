"""Minimal Supervisor API boundary for the containerized add-on E2E test."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast

_OPTIONS_ENDPOINT = "/addons/self/options/config"
_TOKEN_PATHS = {
    "Bearer cloudflared-e2e": Path("/addon-data/options.json"),
    "Bearer cloudflared-invalid-e2e": Path("/invalid-addon-data/options.json"),
}


def supervisor_response(
    path: str,
    authorization: str,
    *,
    token_paths: Mapping[str, Path] | None = None,
) -> tuple[HTTPStatus, dict[str, object]]:
    """Resolve one API request, re-reading options to model restarts faithfully."""
    if path == "/health":
        return HTTPStatus.OK, {"result": "ok", "data": {}}
    if path != _OPTIONS_ENDPOINT:
        return HTTPStatus.NOT_FOUND, {"result": "error"}

    options_path = (token_paths if token_paths is not None else _TOKEN_PATHS).get(
        authorization
    )
    if options_path is None:
        return HTTPStatus.UNAUTHORIZED, {"result": "error"}

    try:
        options: object = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"result": "error"}
    if not isinstance(options, dict):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"result": "error"}
    return HTTPStatus.OK, {
        "result": "ok",
        "data": cast(dict[str, object], options),
    }


class SupervisorHandler(BaseHTTPRequestHandler):
    """Serve the only authenticated Supervisor endpoint used by Bashio."""

    server_version = "E2ESupervisor/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    token_paths: ClassVar[dict[str, Path]] = _TOKEN_PATHS

    def do_GET(self) -> None:
        status, document = supervisor_response(
            self.path,
            self.headers.get("Authorization", ""),
            token_paths=self.token_paths,
        )
        self._respond(status, document)

    def log_message(self, template: str, *args: object) -> None:
        print(f"{self.address_string()} - {template % args}", flush=True)

    def _respond(self, status: HTTPStatus, document: dict[str, object]) -> None:
        payload = json.dumps(document, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    """Run the deterministic API double until Docker stops the container."""
    port = int(os.environ.get("PORT", "80"))
    with ThreadingHTTPServer(("0.0.0.0", port), SupervisorHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
