"""Loopback bootstrap, password-authenticated HTTPS, and scoped app sessions.

Trusted legacy apps/native code remain inside the local-user trust boundary.
"""

import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from .app_storage import AppServiceError
from .access import verify_password


class Auth:
    def __init__(self, config=None):
        self.hub_token = secrets.token_urlsafe(32)
        self.sessions = {}
        self.lock = threading.RLock()
        self.remote = bool(config and config.remote_access)
        self.origin = config.public_origin if config else None
        self.password_file = config.data_dir / "access.json" if config else None
        self.hub_sessions = {}
        self.attempts = {}
        if self.remote and (not self.origin or not self.origin.startswith("https://") or not self.password_file.is_file()):
            raise ValueError("Remote access requires an HTTPS public origin and a configured access password")

    def valid_hub_token(self, token):
        with self.lock:
            if not self.remote and secrets.compare_digest(token.encode(), self.hub_token.encode()): return True
            return self.hub_sessions.get(token, 0) > time.monotonic()

    def allowed_origin(self, request):
        if self.remote:
            return (request.url.scheme == "https" and str(request.base_url).rstrip("/") == self.origin
                    and request.headers.get("origin", self.origin) == self.origin
                    and request.headers.get("sec-fetch-site") != "cross-site")
        return self.local_request(request)

    def bootstrap(self, request):
        if not self.remote: return self.hub_token
        token = request.cookies.get("__Host-vela-session", "")
        if not self.valid_hub_token(token): raise AppServiceError(401, "Sign in to Vela")
        return token

    def login(self, request, password):
        if not self.remote: raise AppServiceError(400, "This engine uses local access")
        peer = request.client.host
        now = time.monotonic()
        with self.lock:
            self.attempts = {key: stamps for key, stamps in self.attempts.items() if stamps and stamps[-1] > now - 600}
            if peer not in self.attempts and len(self.attempts) >= 1024:
                raise AppServiceError(429, "Sign-in temporarily busy; try again later")
            attempts = [stamp for stamp in self.attempts.get(peer, []) if stamp > now - 600]
            if len(attempts) >= 5: raise AppServiceError(429, "Too many sign-in attempts; try again in 10 minutes")
            self.attempts[peer] = attempts + [now]
        if not verify_password(self.password_file, password):
            raise AppServiceError(401, "Incorrect password")
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.attempts.pop(peer, None)
            self.hub_sessions = {key: expiry for key, expiry in self.hub_sessions.items() if expiry > now}
            self.hub_sessions[token] = now + 43200
        return token

    def logout(self, request):
        token = request.cookies.get("__Host-vela-session", "")
        with self.lock:
            self.hub_sessions.pop(token, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value.get("owner") != token}

    def issue(self, manifest, identity, owner=None):
        token = secrets.token_urlsafe(32)
        session = {"app_id": manifest.id, "installationId": identity, "owner": owner,
                   "capabilities": manifest.capabilities, "expires": time.monotonic() + 3600,
                   "schemaVersion": manifest.raw.get("data", {}).get("schemaVersion", 1),
                   "quota": manifest.raw.get("data", {}).get("quotaBytes", 1048576)}
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > time.monotonic()}
            self.sessions[token] = session
        return {"token": token, "installationId": identity, "protocol": 1,
                "capabilities": session["capabilities"], "unavailableCapabilities": manifest.unavailable_capabilities, "expiresIn": 3600}

    def resolve(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] <= time.monotonic() or (self.remote and not self.valid_hub_token(session.get("owner", ""))):
                self.sessions.pop(token, None)
                raise AppServiceError(401, "App session expired or invalid")
            return dict(session)

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def revoke_app(self, app_id):
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["app_id"] != app_id}

    @staticmethod
    def local_request(request):
        # Validate Host as well as peer IP to reject DNS rebinding. Vite's
        # same-origin proxy is allowed only on its explicit local dev port.
        host = request.url.hostname
        peer = request.client.host if request.client else ""
        if host not in ("localhost", "127.0.0.1", "::1", "testserver") or peer not in ("127.0.0.1", "::1", "testclient"):
            return False
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return False
            if origin != str(request.base_url).rstrip("/") and origin not in ("http://localhost:5173", "http://127.0.0.1:5173"):
                return False
            if parsed.scheme not in ("http", "https"):
                return False
        return request.headers.get("sec-fetch-site") not in ("cross-site",)

    async def middleware(self, request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            public = path in ("/api/health", "/api/session", "/api/login", "/api/logout") or (path.startswith("/api/apps/") and path.endswith("/icon"))
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if path in ("/api/session", "/api/login", "/api/logout"):
                if not self.allowed_origin(request) or request.headers.get("x-vela-bootstrap") != "1":
                    return JSONResponse({"detail": "Local same-origin bootstrap required"}, status_code=403)
            elif path.startswith("/api/app/"):
                try:
                    request.state.app_session = self.resolve(token)
                except AppServiceError as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status)
            elif not public and not self.valid_hub_token(token):
                return JSONResponse({"detail": "Hub authentication required"}, status_code=401)
            request.state.hub_token = token
            # Proxies probe readiness over the private HTTP connection. This
            # endpoint returns only status/version; all other APIs require HTTPS.
            if self.remote and request.url.scheme != "https" and path != "/api/health":
                return JSONResponse({"detail": "HTTPS is required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.remote:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if not path.startswith("/apps/"):
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        if path.startswith("/api/apps/") and path.endswith("/icon"):
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        return response
