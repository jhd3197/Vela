"""Loopback bootstrap, password-authenticated HTTPS, and scoped app sessions.

Trusted legacy apps/native code remain inside the local-user trust boundary.
"""

import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from .app_storage import AppServiceError
from .access import build_verifier, verify_password, verify_secret


class Auth:
    def __init__(self, config=None):
        self.hub_token = secrets.token_urlsafe(32)
        self.sessions = {}
        self.lock = threading.RLock()
        self.remote = bool(config and config.remote_access)
        self.phone_origin = None
        self.origin = config.public_origin if config else None
        self.password_file = config.data_dir / "access.json" if config else None
        self.hub_sessions = {}
        self.attempts = {}
        # Quick-unlock enrollment, in memory only, keyed by hub session token.
        # A restart, a sign-out or a new access password drops it, and the
        # person signs in with the Vela password again.
        self.quick = {}
        # Set by `create_app` once desktops exist. An agent-bound app session
        # passes through here before any handler sees it, so there is one place
        # that decides what a run may reach rather than a check per route.
        self.gateway = None
        if self.remote and (not self.origin or not self.origin.startswith("https://") or not self.password_file.is_file()):
            raise ValueError("Remote access requires an HTTPS public origin and a configured access password")

    def valid_hub_token(self, token):
        with self.lock:
            if not self.remote and secrets.compare_digest(token.encode(), self.hub_token.encode()): return True
            return self.hub_sessions.get(token, 0) > time.monotonic()

    def allowed_origin(self, request):
        if self.is_remote_request(request):
            origin = self.origin if self.remote else self.phone_origin
            return (origin and request.url.scheme == "https" and str(request.base_url).rstrip("/") == origin
                    and request.headers.get("origin", origin) == origin
                    and request.headers.get("sec-fetch-site") != "cross-site")
        return self.local_request(request)

    def is_remote_request(self, request):
        return (self.remote or (bool(self.phone_origin) and str(request.base_url).rstrip('/') == self.phone_origin)
                or not self.local_request(request))

    def disable_phone_access(self):
        if self.remote: return
        with self.lock:
            self.phone_origin = None
            self.hub_sessions.clear()
            self.quick.clear()
            self.sessions = {key: value for key, value in self.sessions.items()
                             if value.get('owner') == self.hub_token}

    def bootstrap(self, request):
        if not self.is_remote_request(request): return self.hub_token
        token = request.cookies.get("__Host-vela-session", "")
        if not self.remote and token == self.hub_token:
            raise AppServiceError(401, 'Sign in to Vela')
        if not self.valid_hub_token(token): raise AppServiceError(401, "Sign in to Vela")
        return token

    def login(self, request, password):
        if not self.is_remote_request(request): raise AppServiceError(400, "This engine uses local access")
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
            self.quick = {key: value for key, value in self.quick.items() if key in self.hub_sessions}
        return token

    # ------------------------------------------------------------ quick unlock
    #
    # Reauthentication for a session that is already signed in. It never starts
    # a session: a fresh or expired one still needs the Vela password, and the
    # computer's bootstrap token is a separate trust boundary that is left alone.

    TIMEOUTS = (60, 300, 900)
    MAX_FAILURES = 5
    UNLOCK_PATHS = ("/api/security", "/api/security/unlock", "/api/security/lock")

    def quick_session(self, request):
        """The hub token quick unlock applies to, or None where it does not."""
        if not self.is_remote_request(request):
            return None
        token = (getattr(request.state, "hub_token", None)
                 or request.headers.get("authorization", "").removeprefix("Bearer "))
        return token if token and self.valid_hub_token(token) else None

    def _locked(self, record):
        return bool(record["locked"] or time.monotonic() - record["activity"] > record["timeout"])

    def quick_status(self, request):
        token = self.quick_session(request)
        with self.lock:
            record = self.quick.get(token or "")
            if not record:
                return {"available": bool(token), "enrolled": False, "method": None,
                        "timeout": 300, "locked": False, "passwordRequired": False,
                        "attemptsRemaining": self.MAX_FAILURES}
            return {"available": True, "enrolled": True, "method": record["method"],
                    "timeout": record["timeout"], "locked": self._locked(record),
                    "passwordRequired": record["password"],
                    "attemptsRemaining": max(0, self.MAX_FAILURES - record["failures"])}

    def require_quick_session(self, request):
        token = self.quick_session(request)
        if not token:
            raise AppServiceError(403, "App lock is available on a signed-in phone session")
        return token

    def check_password(self, password, token=None):
        """Verify the Vela password for a security route, with its own throttle.

        The lock screen offers the password as the recovery path, so it is
        reachable while a session is locked. Counting those attempts on the
        session — and making each one cost a scrypt hash — keeps that path from
        becoming an unmetered guess at the account password.
        """
        now = time.monotonic()
        with self.lock:
            record = self.quick.get(token or "")
            if record and record.get("cooldown", 0) > now:
                wait = int(record["cooldown"] - now) // 60 + 1
                raise AppServiceError(429, f"Too many attempts. Try again in {wait} minutes.")
        if not isinstance(password, str) or not verify_password(self.password_file, password):
            with self.lock:
                record = self.quick.get(token or "")
                if record:
                    record["password_failures"] = record.get("password_failures", 0) + 1
                    if record["password_failures"] >= self.MAX_FAILURES:
                        record["cooldown"] = now + 600
                        record["password_failures"] = 0
            raise AppServiceError(401, "Incorrect Vela password")
        with self.lock:
            record = self.quick.get(token or "")
            if record: record["password_failures"] = 0

    def enroll_quick(self, request, password, method, secret):
        token = self.require_quick_session(request)
        self.check_password(password, token)
        built = build_verifier(method, secret)
        if built is None:
            raise AppServiceError(422, "That unlock code cannot be used")
        salt, digest = built
        with self.lock:
            previous = self.quick.get(token, {})
            self.quick[token] = {"method": method, "salt": salt, "verifier": digest,
                                 "timeout": previous.get("timeout", 300), "locked": False,
                                 "activity": time.monotonic(), "failures": 0, "password": False,
                                 "password_failures": 0, "cooldown": 0}
        return self.quick_status(request)

    def update_quick(self, request, password, timeout):
        token = self.require_quick_session(request)
        self.check_password(password, token)
        if timeout not in self.TIMEOUTS:
            raise AppServiceError(422, "Choose 1, 5 or 15 minutes")
        with self.lock:
            record = self.quick.get(token)
            if not record: raise AppServiceError(409, "App lock is not set up on this device")
            record["timeout"] = timeout
            record["activity"] = time.monotonic()
        return self.quick_status(request)

    def disable_quick(self, request, password):
        token = self.require_quick_session(request)
        self.check_password(password, token)
        with self.lock:
            self.quick.pop(token, None)
        return self.quick_status(request)

    def lock_now(self, request):
        token = self.require_quick_session(request)
        with self.lock:
            record = self.quick.get(token)
            if not record: raise AppServiceError(409, "App lock is not set up on this device")
            record["locked"] = True
            # Ends the bridge and any open stream this session started.
            self.sessions = {key: value for key, value in self.sessions.items()
                             if value.get("owner") != token}
        return self.quick_status(request)

    def unlock(self, request, secret=None, password=None):
        token = self.require_quick_session(request)
        with self.lock:
            record = self.quick.get(token)
            if not record: raise AppServiceError(409, "App lock is not set up on this device")
            needs_password = record["password"]
            method, salt, digest = record["method"], record["salt"], record["verifier"]
        if password is not None:
            self.check_password(password, token)
        elif needs_password:
            raise AppServiceError(403, "Too many attempts. Use your Vela password.")
        elif not verify_secret(method, secret, salt, digest):
            with self.lock:
                record = self.quick.get(token)
                if record:
                    record["failures"] += 1
                    if record["failures"] >= self.MAX_FAILURES: record["password"] = True
            raise AppServiceError(401, "That does not match. Try again.")
        with self.lock:
            record = self.quick.get(token)
            if record:
                record.update(locked=False, failures=0, password=False, activity=time.monotonic())
        return self.quick_status(request)

    def _touch(self, token):
        with self.lock:
            record = self.quick.get(token or "")
            if record and not self._locked(record): record["activity"] = time.monotonic()

    def record_activity(self, request):
        self._touch(self.quick_session(request))
        return self.quick_status(request)

    def reset_quick_unlock(self):
        """A new access password invalidates every enrollment made under the old one."""
        with self.lock:
            self.quick.clear()

    def _lock_response(self, path, token):
        """423 for a locked session, or None when the request may continue."""
        with self.lock:
            record = self.quick.get(token or "")
            if not record or not self._locked(record): return None
            record["locked"] = True
        if path in self.UNLOCK_PATHS or path in ("/api/health", "/api/session", "/api/login", "/api/logout"):
            return None
        if path.startswith("/api/apps/") and path.endswith("/icon"): return None
        return JSONResponse({"detail": "Vela is locked"}, status_code=423)

    def logout(self, request):
        token = request.cookies.get("__Host-vela-session", "")
        with self.lock:
            self.hub_sessions.pop(token, None)
            self.quick.pop(token, None)
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

    def issue_agent(self, manifest, identity, agent):
        """An app session an agent run holds, rather than a person.

        Same shape as an ordinary one so every route and service that already
        takes a session keeps working — which is the point: the agent path must
        not be a second, parallel way into the same effects. What differs is
        that it names the run it belongs to, expires in minutes rather than an
        hour, and carries no owner token, so it cannot be mistaken for the
        person who started it.
        """
        from .desktops.principals import AGENT_SESSION_SECONDS

        token = secrets.token_urlsafe(32)
        expires = time.monotonic() + AGENT_SESSION_SECONDS
        session = {"app_id": manifest.id, "installationId": identity, "owner": None,
                   "capabilities": manifest.capabilities, "expires": expires,
                   "schemaVersion": manifest.raw.get("data", {}).get("schemaVersion", 1),
                   "quota": manifest.raw.get("data", {}).get("quotaBytes", 1048576),
                   "agent": {**agent, "expires": expires}}
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > time.monotonic()}
            self.sessions[token] = session
        return {"token": token, "installationId": identity, "protocol": 1,
                "capabilities": session["capabilities"],
                "unavailableCapabilities": manifest.unavailable_capabilities,
                "expiresIn": AGENT_SESSION_SECONDS, "agent": agent}

    def revoke_agent(self, *, desktop_id=None, run_id=None):
        """Drop agent sessions for a desktop, or for one run on it.

        Used by Stop, by a policy change and by deleting a desktop. Removing the
        session is what makes those immediate: a session marked stale would still
        be a session somebody has to remember to re-check.
        """
        with self.lock:
            removed = 0
            kept = {}
            for token, session in self.sessions.items():
                agent = session.get("agent")
                matches = (
                    isinstance(agent, dict)
                    and (desktop_id is None or agent.get("desktopId") == desktop_id)
                    and (run_id is None or agent.get("runId") == run_id)
                )
                if matches:
                    removed += 1
                else:
                    kept[token] = session
            self.sessions = kept
            return removed

    def resolve(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] <= time.monotonic() or ((self.remote or (session.get('owner') and session['owner'] != self.hub_token)) and not self.valid_hub_token(session.get("owner") or "")):
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
        remote_request = self.is_remote_request(request)
        if path.startswith("/api/"):
            if remote_request and not self.remote and path != '/api/health' and (
                    not self.phone_origin or request.url.scheme != 'https' or
                    str(request.base_url).rstrip('/') != self.phone_origin):
                return JSONResponse({'detail': 'Use the configured Wi-Fi address'}, status_code=403)
            # Automation webhooks carry their own per-workflow secret. They are
            # exempt from the hub token, not from anything else: the bind, TLS
            # and origin rules below still apply, and the secret starts one
            # workflow rather than granting any hub or app access.
            public = (path in ("/api/health", "/api/session", "/api/login", "/api/logout")
                      or path.startswith("/api/automations/hooks/")
                      or (path.startswith("/api/apps/") and path.endswith("/icon")))
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            # The computer's bootstrap token must never authenticate a network
            # request, including on the supplemental Wi-Fi listener.
            if remote_request and not self.remote and token == self.hub_token:
                return JSONResponse({"detail": "Phone sign-in required"}, status_code=401)
            if path in ("/api/session", "/api/login", "/api/logout"):
                if not self.allowed_origin(request) or request.headers.get("x-vela-bootstrap") != "1":
                    return JSONResponse({"detail": "Local same-origin bootstrap required"}, status_code=403)
            elif path.startswith("/api/app/"):
                try:
                    request.state.app_session = self.resolve(token)
                    if remote_request and not self.remote and request.state.app_session.get('owner') == self.hub_token:
                        return JSONResponse({'detail': 'Open the app from your phone session'}, status_code=401)
                    if self.gateway is not None:
                        request.state.effect = self.gateway(
                            request.state.app_session, request.method, path)
                except AppServiceError as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status)
            elif not public and not self.valid_hub_token(token):
                return JSONResponse({"detail": "Hub authentication required"}, status_code=401)
            request.state.hub_token = token
            # Quick unlock. A locked session stops here rather than at one page
            # component, so app tokens issued before the lock, their open
            # streams and every other protected route are covered together.
            if remote_request:
                owner = (getattr(request.state, "app_session", {}).get("owner")
                         if path.startswith("/api/app/") else token)
                locked = self._lock_response(path, owner)
                if locked is not None: return locked
                # Only a deliberate user action keeps a session awake. Polling
                # does not send this header, so a phone on a table still locks.
                if request.headers.get("x-vela-activity") == "1": self._touch(owner)
            # Proxies probe readiness over the private HTTP connection. This
            # endpoint returns only status/version; all other APIs require HTTPS.
            if remote_request and request.url.scheme != "https" and path != "/api/health":
                return JSONResponse({"detail": "HTTPS is required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if remote_request:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if not path.startswith("/apps/"):
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        if path.startswith("/api/apps/") and path.endswith("/icon"):
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        return response
