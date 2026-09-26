"""A disposable web server that behaves the way a real one does.

This is the compatibility fixture for managed web apps. It is not a Vela app and
it never becomes one: it is an ordinary HTTP server with its own accounts, its
own SQLite database and its own attachments, run as a child process exactly the
way Memos is, so the host's installer, supervisor, gateway, snapshot and
recovery code can be tested against real behaviour instead of a mock.

What it does on purpose, because each one is something the gateway has to carry:

* signs in with a password and answers with **both** a bearer token and
  cookies, and reports back exactly which credentials it received -- which is how
  a test proves Vela's own never arrive;
* sets one session cookie a browser will send inside a Vela window
  (`SameSite=None`) and one it will not (`SameSite=Lax`), because that
  difference decides whether a real application can be used in a window at all;
* sets several cookies in one response, one of them with a `Domain` attribute
  and one with a name the gateway reserves;
* stores notes in SQLite with a write-ahead log, and attachments as files, so a
  snapshot has something to be consistent about;
* serves ranged downloads, redirects, deep links and an event stream;
* takes a slow, long-lived request, so revoking a session mid-stream is testable;
* and can be told to start slowly, fail its migration, refuse to become ready or
  exit without warning.

Everything it accepts is disposable. The test account is `tester` / `fixture-pw`.

Run it by hand:

    python service.py --port 8099 --data ./scratch --public-url http://localhost:8099
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

USER = "tester"
PASSWORD = "fixture-pw"
MAX_UPLOAD = 8 * 1024 * 1024

STATE: dict[str, object] = {"ready": False, "tokens": {}, "sessions": {}}


def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Store:
    """Notes in SQLite with a WAL, attachments as ordinary files.

    The connection is held open for the life of the process, the way a real
    server holds one. That matters for the tests around it: SQLite removes the
    write-ahead log when the last connection closes, so a fixture that opened
    and closed a connection per request would never have the `-wal` file whose
    handling is the whole point of stopping a service before copying its data.
    """

    def __init__(self, root: Path):
        self.root = root
        self.files = root / "attachments"
        self.files.mkdir(parents=True, exist_ok=True)
        self.path = root / "fixture.db"
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " body TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            self.db.commit()

    def add_note(self, body: str) -> dict:
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO notes (body, created_at) VALUES (?, ?)",
                (body, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            self.db.commit()
            return {"id": cursor.lastrowid, "body": body}

    def notes(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM notes ORDER BY id")]

    def put_file(self, name: str, data: bytes) -> dict:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "upload"
        (self.files / safe).write_bytes(data)
        return {"name": safe, "size": len(data)}

    def get_file(self, name: str) -> bytes | None:
        target = (self.files / name).resolve()
        if not target.is_relative_to(self.files.resolve()) or not target.is_file():
            return None
        return target.read_bytes()


def migrate(root: Path, version: str) -> None:
    """Pretend to migrate, at whatever speed and success the test asked for."""
    marker = root / "schema.json"
    previous = None
    if marker.is_file():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8")).get("version")
        except ValueError:
            previous = None
    delay = env_float("FIXTURE_MIGRATE_SECONDS")
    if previous != version and delay:
        time.sleep(delay)
    if previous != version and os.environ.get("FIXTURE_MIGRATE_FAIL") == "1":
        print(f"migration from {previous} to {version} failed", flush=True)
        raise SystemExit(3)
    marker.write_text(
        json.dumps({"version": version, "previous": previous}), encoding="utf-8"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vela-fixture/1"

    # -------------------------------------------------------------- plumbing --

    def log_message(self, fmt, *args):  # noqa: A003 - BaseHTTPRequestHandler's name
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def _send(self, status: int, body: bytes = b"", *, content_type="application/json",
              headers: list[tuple[str, str]] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, headers=None):
        self._send(status, json.dumps(payload).encode("utf-8"), headers=headers)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return b""
        return self.rfile.read(length) if length else b""

    def _cookies(self) -> dict[str, str]:
        jar: dict[str, str] = {}
        for raw in self.headers.get_all("Cookie") or []:
            for part in raw.split(";"):
                if "=" in part:
                    name, value = part.split("=", 1)
                    jar[name.strip()] = value.strip()
        return jar

    def _identity(self) -> str | None:
        """Who this request is, by this app's own credentials and nothing else."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return STATE["tokens"].get(auth[7:])  # type: ignore[union-attr]
        jar = self._cookies()
        for name in ("fixture_session", "fixture_plain", "fixture_lax"):
            if name in jar:
                return STATE["sessions"].get(jar[name])  # type: ignore[union-attr]
        return None

    # ---------------------------------------------------------------- routing --

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        self.route("GET")

    def do_HEAD(self):  # noqa: N802
        self.route("HEAD")

    def do_POST(self):  # noqa: N802
        self.route("POST")

    def do_PUT(self):  # noqa: N802
        self.route("PUT")

    def do_DELETE(self):  # noqa: N802
        self.route("DELETE")

    def route(self, method: str):
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        store: Store = self.server.store  # type: ignore[attr-defined]

        if path == "/healthz":
            if not STATE["ready"]:
                return self._json(503, {"status": "starting"})
            return self._json(200, {"status": "ok", "version": self.server.version})  # type: ignore[attr-defined]

        if path == "/" or path == "/index.html":
            body = (
                f"<!doctype html><title>Fixture {self.server.version}</title>"  # type: ignore[attr-defined]
                f"<h1>Fixture app</h1><p>version {self.server.version}</p>"  # type: ignore[attr-defined]
                f"<p>{len(store.notes())} notes</p>"
                f"<p>public url: {self.server.public_url}</p>"  # type: ignore[attr-defined]
            ).encode("utf-8")
            return self._send(200, body, content_type="text/html; charset=utf-8")

        if path == "/api/login" and method == "POST":
            payload = _parse_json(self._body())
            if payload.get("user") != USER or payload.get("password") != PASSWORD:
                return self._json(401, {"detail": "no"})
            token = secrets.token_urlsafe(16)
            session = secrets.token_urlsafe(16)
            STATE["tokens"][token] = USER  # type: ignore[index]
            STATE["sessions"][session] = USER  # type: ignore[index]
            # Three session cookies on purpose, because three different things
            # need proving and no one cookie can prove them all:
            #
            # `fixture_session` says `SameSite=None; Secure`, which is what an
            # application has to say to stay signed in inside a Vela window --
            # the window frames it cross-site, and a browser sends nothing
            # weaker there. The browser suite checks that one.
            #
            # `fixture_lax` is the other kind of application, kept so a test can
            # show it does *not* reach a framed app and that Vela passed its
            # policy through rather than rewriting it.
            #
            # `fixture_plain` carries no `Secure`, because `httpx` -- which the
            # Python suite uses -- will not store a `Secure` cookie over plain
            # HTTP however trustworthy the origin is. It is how those tests
            # exercise cookie-only authentication at all.
            return self._json(
                200,
                {"token": token, "user": USER},
                headers=[
                    ("Set-Cookie",
                     f"fixture_session={session}; Path=/; HttpOnly; SameSite=None; Secure"),
                    ("Set-Cookie", f"fixture_lax={session}; Path=/; HttpOnly; SameSite=Lax"),
                    ("Set-Cookie", f"fixture_plain={session}; Path=/; HttpOnly"),
                    ("Set-Cookie", "fixture_refresh=r1; Path=/api; HttpOnly"),
                ],
            )

        if path == "/api/me":
            who = self._identity()
            if who is None:
                return self._json(401, {"detail": "sign in"})
            return self._json(200, {"user": who})

        if path == "/api/received":
            # Everything this app was handed. A test reads it to prove what did
            # and did not cross the gateway.
            return self._json(200, {
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "cookies": sorted(self._cookies()),
                "path": path,
                "query": {key: value for key, value in query.items()},
            })

        if path == "/api/set-cookies":
            return self._json(200, {"ok": True}, headers=[
                ("Set-Cookie", "plain=1; Path=/"),
                ("Set-Cookie", "wide=2; Path=/; Domain=apps.localhost"),
                ("Set-Cookie", "__Host-vela-app=stolen; Path=/; Secure; HttpOnly"),
                ("Set-Cookie", "third=3; Path=/; Max-Age=60"),
            ])

        if path == "/api/notes":
            if method == "POST":
                payload = _parse_json(self._body())
                if self._identity() is None:
                    return self._json(401, {"detail": "sign in"})
                return self._json(201, store.add_note(str(payload.get("body", ""))[:2000]))
            return self._json(200, {"notes": store.notes()})

        if path.startswith("/api/files/"):
            name = path[len("/api/files/"):]
            data = store.get_file(name)
            if data is None:
                return self._json(404, {"detail": "no such file"})
            return self._ranged(data, name)

        if path == "/api/files" and method == "POST":
            name = self.headers.get("X-Filename", "upload.bin")
            return self._json(201, store.put_file(name, self._body()))

        if path == "/api/redirect":
            return self._send(302, b"", headers=[("Location", "/api/notes")])

        if path == "/api/events":
            return self._events(int(query.get("count", ["3"])[0]))

        if path == "/api/slow":
            return self._slow(int(query.get("seconds", ["30"])[0]))

        if path.startswith("/api/deep/"):
            return self._json(200, {"deep": path, "query": {k: v for k, v in query.items()}})

        if path == "/api/exit" and method == "POST":
            # A crash, on request. The response is flushed first so the caller
            # sees it rather than a broken connection.
            self._json(200, {"exiting": True})
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(9)), daemon=True).start()
            return None

        return self._json(404, {"detail": "not found", "path": path})

    # -------------------------------------------------------------- responses --

    def _ranged(self, data: bytes, name: str):
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        raw = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw.strip()) if raw else None
        if not match:
            return self._send(200, data, content_type=kind,
                              headers=[("Accept-Ranges", "bytes")])
        start = int(match.group(1) or 0)
        end = int(match.group(2)) if match.group(2) else len(data) - 1
        end = min(end, len(data) - 1)
        if start > end:
            return self._send(416, b"", content_type=kind)
        chunk = data[start:end + 1]
        return self._send(206, chunk, content_type=kind, headers=[
            ("Content-Range", f"bytes {start}-{end}/{len(data)}"),
            ("Accept-Ranges", "bytes"),
        ])

    def _events(self, count: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for index in range(max(1, min(count, 50))):
                payload = f"data: tick {index}\n\n".encode("utf-8")
                self.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _slow(self, seconds: int):
        """A stream that stays open and says nothing, like a real event stream."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        deadline = time.monotonic() + max(1, min(seconds, 120))
        try:
            payload = b"data: open\n\n"
            self.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
            self.wfile.flush()
            while time.monotonic() < deadline:
                time.sleep(0.25)
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def _parse_json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Vela managed web app test fixture")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--addr", default="127.0.0.1")
    parser.add_argument("--data", required=True)
    parser.add_argument("--public-url", default="")
    parser.add_argument("--version", default=os.environ.get("FIXTURE_VERSION", "1.0.0"))
    args = parser.parse_args()

    root = Path(args.data)
    root.mkdir(parents=True, exist_ok=True)
    print(f"fixture {args.version} starting in {root}", flush=True)

    if os.environ.get("FIXTURE_REFUSE_START") == "1":
        print("refusing to start, as asked", flush=True)
        return 2

    migrate(root, args.version)
    store = Store(root)

    server = ThreadingHTTPServer((args.addr, args.port), Handler)
    server.daemon_threads = True
    server.store = store
    server.version = args.version
    server.public_url = args.public_url

    delay = env_float("FIXTURE_START_DELAY")
    exit_after = env_float("FIXTURE_EXIT_AFTER")

    def become_ready():
        if delay:
            time.sleep(delay)
        if os.environ.get("FIXTURE_NEVER_READY") == "1":
            print("bound the port and will never be ready, as asked", flush=True)
            return
        STATE["ready"] = True
        print(f"fixture ready on {args.addr}:{args.port}", flush=True)

    threading.Thread(target=become_ready, daemon=True).start()
    if exit_after:
        threading.Thread(
            target=lambda: (time.sleep(exit_after), os._exit(7)), daemon=True
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
