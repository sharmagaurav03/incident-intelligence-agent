"""Webhook receiver for the ServiceNow trigger.

Body is just {"sys_id": ...} -- we re-read the ticket from the source of
truth, so a forged body can't inject anything. Signature is
hex(hmac_sha256(secret, raw_body)) in X-Triage-Signature.

Plain stdlib http.server on purpose: tiny, auditable, no framework to
patch. TODO: swap for uvicorn behind a queue if volume ever justifies it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig


def verify_signature(secret: str, body: bytes, provided_hex: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (provided_hex or "").strip().lower())


class IdempotencyStore:
    def __init__(self, path: str = ""):
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            self._seen = set(json.loads(self._path.read_text()))

    def first_time(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            if self._path:
                self._path.write_text(json.dumps(sorted(self._seen)))
            return True


class TriggerService:
    """HTTP-free core so tests don't need a server."""

    def __init__(self, cfg: AppConfig, triage_by_sys_id, sleep=time.sleep):
        self.cfg = cfg
        self.triage_by_sys_id = triage_by_sys_id
        self.store = IdempotencyStore(cfg.webhook.idempotency_file)
        self._sleep = sleep

    def handle_trigger(self, body: bytes, signature_header: str) -> tuple[int, str]:
        secret = self.cfg.webhook.secret()
        if not secret:
            return 503, "webhook secret not configured"
        if not verify_signature(secret, body, signature_header):
            return 401, "invalid signature"
        try:
            payload = json.loads(body.decode())
            sys_id = str(payload["sys_id"])
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return 400, "body must be JSON with a sys_id field"
        if not sys_id or len(sys_id) > 64:
            return 400, "invalid sys_id"
        if not self.store.first_time(sys_id):
            return 200, "duplicate delivery ignored (idempotent)"
        if self.cfg.webhook.debounce_seconds:
            # let the rest of the storm arrive so clustering can do its job
            self._sleep(self.cfg.webhook.debounce_seconds)
        self.triage_by_sys_id(sys_id)
        return 202, "accepted"


def serve(cfg: AppConfig, triage_by_sys_id: Callable[[str], Any]) -> None:
    service = TriggerService(cfg, triage_by_sys_id)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if self.path != "/trigger":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            code, msg = service.handle_trigger(
                body, self.headers.get("X-Triage-Signature", "")
            )
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": msg}).encode())

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # default logger echoes paths/bodies; keep PII out of logs

    server = HTTPServer((cfg.webhook.host, cfg.webhook.port), Handler)
    print(f"triage-agent webhook listening on "
          f"http://{cfg.webhook.host}:{cfg.webhook.port}/trigger")
    server.serve_forever()
