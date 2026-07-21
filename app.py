"""Stage 1 — Minimal Hello World server for Railway.

This stage only verifies that the Railway pipeline works end-to-end:
- Build succeeds
- Container starts
- Healthcheck at /health passes
- Deployment turns green

Once stage 1 is green, we'll layer on the actual bot in stages 2-6.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/healthz"):
            body = json.dumps({
                "status": "ok",
                "stage": 1,
                "service": "telegram-subtitle-bot",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return  # silence


def main() -> None:
    port = int(os.getenv("PORT") or os.getenv("HEALTH_PORT") or "8080")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[stage1] listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
