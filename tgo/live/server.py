"""``tgo serve``: a local web server for the live dashboard (Python standard library only).

    python -m tgo serve --input data/case --out out/case            # http://127.0.0.1:8765/

* ``/``                 the dashboard (same page as dashboard.html, in live mode)
* ``/api/dashboard``    the current payload (JSON)
* ``/api/events``       server-sent events: one ``update`` event per new version, heartbeats in between
* ``/api/status``       engine status and the ingestion log
* ``/report.html``      the investigation view of the current graph, built on demand
* ``/healthz``          liveness

Security: binds to 127.0.0.1 by default. Any other bind requires a token, which is generated and printed
if not given. The token is accepted once as ``?token=`` (then kept in an HttpOnly cookie) or as an
``X-TGO-Token`` header. Responses carry a Content-Security-Policy that allows no external origin at all,
so the browser cannot leak data off the machine even if a name in the data tried. The server makes no
outbound connections.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ..dashboard.build import render_html
from .engine import LiveEngine

CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
       "connect-src 'self'; font-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
HEARTBEAT = 15.0


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """Browsers drop keep-alive and event-stream connections all the time; that is not an error."""
        import sys

        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def __init__(self, engine: LiveEngine, host: str = "127.0.0.1", port: int = 8765, token: str | None = None):
        if not _is_loopback(host) and not token:
            token = secrets.token_urlsafe(18)
        self.engine, self.token, self.stopping = engine, token, threading.Event()
        super().__init__((host, port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: LiveServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):      # quiet: the engine logs what matters
        pass

    # ---------------------------------------------------------------- helpers
    def _authorised(self, query: dict) -> bool:
        tok = self.server.token
        if not tok:
            return True
        cand = (query.get("token") or [None])[0] or self.headers.get("X-TGO-Token")
        if not cand:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "tgo_token":
                    cand = v
        return bool(cand) and hmac.compare_digest(cand, tok)

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---------------------------------------------------------------- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        if url.path == "/healthz":
            return self._json({"ok": True, "version": self.server.engine.version})
        if not self._authorised(q):
            return self._send(HTTPStatus.UNAUTHORIZED, b"token required: open the URL printed by `tgo serve`",
                              "text/plain; charset=utf-8")
        cookie = {}
        if self.server.token and q.get("token"):
            cookie = {"Set-Cookie": f"tgo_token={self.server.token}; HttpOnly; SameSite=Strict; Path=/"}
        eng = self.server.engine
        if url.path in ("/", "/index.html", "/dashboard.html"):
            if eng.payload is None:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE,     # refreshes itself until the first update
                                  b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="2">'
                                  b'<title>TGO</title><body style="font:16px system-ui;padding:40px;color:#52514e">'
                                  b'Ingesting the first data&hellip; this page opens the dashboard by itself.',
                                  "text/html; charset=utf-8", {"Retry-After": "2", **cookie})
            return self._send(200, render_html(eng.payload, live=True).encode("utf-8"),
                              "text/html; charset=utf-8", cookie)
        if url.path == "/api/dashboard":
            return self._json(eng.payload or {})
        if url.path == "/api/status":
            return self._json(eng.status())
        if url.path == "/report.html":
            if eng.graph is None:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"not ready", "text/plain; charset=utf-8")
            return self._send(200, eng.report_html().encode("utf-8"), "text/html; charset=utf-8", cookie)
        if url.path == "/api/events":
            return self._events()
        return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    def _events(self) -> None:
        eng = self.server.engine
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        seen = -1
        try:
            while not self.server.stopping.is_set():
                with eng.cond:
                    if eng.version == seen:
                        eng.cond.wait(timeout=HEARTBEAT)
                    v = eng.version
                if v != seen:
                    msg = json.dumps({"version": v, "updated": (eng.payload or {}).get("live", {}).get("updated"),
                                      "error": eng.error})
                    self.wfile.write(f"event: update\ndata: {msg}\n\n".encode("utf-8"))
                    seen = v
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        self.close_connection = True


def watch(engine: LiveEngine, stop: threading.Event, interval: float = 5.0) -> None:
    """Scan every ``interval`` seconds; on change, re-derive and publish (runs until ``stop`` is set)."""
    engine.cycle(force=True)
    while not stop.wait(interval):
        engine.cycle()


def serve(engine: LiveEngine, *, host: str = "127.0.0.1", port: int = 8765, token: str | None = None,
          interval: float = 5.0, log=print, ready=None) -> None:
    """Run the watcher and the HTTP server until interrupted."""
    srv = LiveServer(engine, host, port, token)
    t = threading.Thread(target=watch, args=(engine, srv.stopping, interval), daemon=True, name="tgo-live-watch")
    t.start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{srv.server_address[1]}/" + (f"?token={srv.token}" if srv.token else "")
    log(f"TGO live dashboard: {url}  (watching {engine.input}, every {interval:g}s; Ctrl+C to stop)")
    if not _is_loopback(host):
        log("  note: listening beyond this machine; anyone with the token can read the case data")
    if ready:
        ready(srv, url)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stopping.set()
        with engine.cond:
            engine.cond.notify_all()
        srv.server_close()
        t.join(timeout=120)          # let a running update finish before the store is released
        engine.close()
