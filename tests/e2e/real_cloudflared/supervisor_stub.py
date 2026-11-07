"""Supervisor API stub for the real-cloudflared E2E harness.

Serves the add-on options from <nodes dir>/<token>.json, where <token> is the
container's SUPERVISOR_TOKEN. The file is re-read on every request, so a new
container sees the options written just before it started. Unknown paths get
an empty "ok" answer and are logged.

Usage: python3 supervisor_stub.py <port> <nodes dir> <log file>
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    nodes: Path
    log_file: Path

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, data: object, status: int = 200) -> None:
        body = json.dumps({"result": "ok", "data": data}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        path = self.path.split("?", 1)[0]
        with self.log_file.open("a") as log:
            log.write(f"{token} {self.command} {path}\n")
        node_file = self.nodes / f"{token}.json"
        if not token or not node_file.is_file():
            self._reply({}, 401)
            return
        node = json.loads(node_file.read_text())
        options = node["options"]
        if path == "/addons/self/options/config":
            self._reply(options)
        elif path == "/addons/self/info":
            self._reply(
                {
                    "name": "Cloudflared",
                    "slug": "cloudflared",
                    "ip_address": node["ip"],
                    "options": options,
                }
            )
        elif path == "/info":
            self._reply({"hostname": node["hostname"], "arch": "amd64"})
        else:
            self._reply({})

    do_GET = _handle
    do_POST = _handle


def main() -> None:
    Handler.nodes = Path(sys.argv[2])
    Handler.log_file = Path(sys.argv[3])
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()


if __name__ == "__main__":
    main()
