"""Small authorized test endpoint which always returns a configurable block response."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ForbiddenHandler(BaseHTTPRequestHandler):
    server_version = "ProxyPool403/1.0"

    def _respond(self, *, head: bool = False) -> None:
        status = self.server.response_status  # type: ignore[attr-defined]
        payload = {
            "ok": False,
            "status": status,
            "message": "intentional test response",
            "client": self.client_address[0],
            "path": self.path,
            "time": int(time.time()),
        }
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-WAF-Intercept", "true")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._respond()

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(head=True)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self._respond()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string}] {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an intentional 403 test endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--status", type=int, default=403)
    args = parser.parse_args()
    if not 400 <= args.status <= 599:
        raise SystemExit("--status must be between 400 and 599")
    server = ThreadingHTTPServer((args.host, args.port), ForbiddenHandler)
    server.response_status = args.status
    print(f"403 test server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
