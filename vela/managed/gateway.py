"""The web address a managed app is published on, and everything that guards it.

An application like Memos expects to own its origin: `/api`, `/file`, its own
cookies, its own service paths. Serving it under `/apps/{id}/` the way a static
v2 document is served would change every one of those assumptions, so a managed
app gets a host of its own -- `memos.apps.localhost` -- and is served at `/`.

That choice is what makes the rest of this file necessary, and what makes it
possible:

**One host, one app, one upstream.** A host label maps to an installation, and
an installation maps to the loopback port its own supervisor recorded. A caller
never names an upstream; there is nothing to point somewhere else.

**The hub is not reachable here.** This is an ASGI middleware in front of
everything, so a request on an app host is answered here and never reaches
Vela's routers, its auth middleware or its API. `memos.apps.localhost/api/apps`
is the app's path, not Vela's.

**Vela's credentials do not go upstream, and the app's do not come back.** The
gateway's own cookie uses the `__Host-` prefix, which browsers refuse to set
with a `Domain` attribute -- so a sibling app's script cannot toss one onto the
shared parent domain -- and it is removed from the request before it is
forwarded. The upstream's `Set-Cookie` headers are passed through intact except
that `Domain` is dropped, making every app cookie host-only, and a reserved name
is refused outright.

**Getting in is a short-lived exchange, not a token in a URL.** The dashboard
asks for a ticket, the browser redeems it once on the app's own origin within
thirty seconds, and the gateway sets its session cookie. The ticket is bound to
the app, the installation generation and the Vela session that asked for it, so
a replay, a different origin or a signed-out session gets nothing.

**Access ends when Vela says it ends.** Sign-out, app lock, a stop, an update
and removal all revoke sessions. Open streams are not exempt: a watchdog closes
the upstream response within a second of the session going away.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

LOG = logging.getLogger(__name__)

#: The gateway's own cookie. `__Host-` is load-bearing: a browser refuses to set
#: one with a `Domain` attribute or from a non-secure origin, which is what stops
#: a sibling app on the same parent domain from writing one.
SESSION_COOKIE = "__Host-vela-app"

#: Names the gateway owns. An upstream that tries to set one is refused rather
#: than allowed to overwrite the session that authorises it.
RESERVED_COOKIE_PREFIXES = ("__Host-vela", "__Secure-vela", "vela-gateway")

#: How long a launch ticket is good for, and how often it may be used.
TICKET_SECONDS = 30
#: How long a gateway session lives without use, and at most.
SESSION_IDLE_SECONDS = 8 * 3600
SESSION_MAX_SECONDS = 24 * 3600
#: How quickly a revoked session tears down a connection that is already open.
REVOCATION_CHECK_SECONDS = 1.0

#: Header names that belong to one hop and must not be forwarded.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})
#: Removed from what reaches the application. `content-length` deliberately
#: stays: the body is forwarded as a stream, and `httpx` frames a stream as
#: chunked unless a length is already set. An upstream that reads
#: `Content-Length` -- which is most of them -- would otherwise see an empty
#: body on every form post.
REQUEST_STRIP = HOP_BY_HOP | {"host", "cookie", "x-vela-activity",
                              "x-vela-bootstrap", "x-vela-app-session"}
#: Removed from what reaches the browser. `set-cookie` is handled separately,
#: because there can be several and each one is rewritten rather than dropped.
RESPONSE_STRIP = HOP_BY_HOP | {"content-length", "set-cookie"}

_LABEL_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_BODYLESS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE", "TRACE"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class GatewaySessions:
    """Launch tickets and gateway sessions, in memory and nowhere else.

    Deliberately not persisted. A session that survived a restart would be a
    session issued against a process that no longer exists, and the cost of not
    persisting is that someone opens the app again from the dashboard.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- tickets --

    def issue_ticket(self, *, app_id: str, generation: int, owner: str | None,
                     host: str, path: str = "/") -> str:
        self._expire()
        token = secrets.token_urlsafe(32)
        self._tickets[token] = {
            "app_id": app_id,
            "generation": generation,
            "owner": owner,
            "host": host.lower(),
            "path": path,
            "expires": time.monotonic() + TICKET_SECONDS,
        }
        return token

    def redeem(self, token: str, *, host: str) -> dict[str, Any] | None:
        """Spend a ticket. Single use: it is removed whether or not it fits."""
        ticket = self._tickets.pop(token, None)
        if ticket is None or ticket["expires"] < time.monotonic():
            return None
        if ticket["host"] != host.lower():
            return None
        return ticket

    # ------------------------------------------------------------ sessions --

    def open_session(self, ticket: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        moment = time.monotonic()
        self._sessions[token] = {
            "app_id": ticket["app_id"],
            "generation": ticket["generation"],
            "owner": ticket["owner"],
            "created": moment,
            "seen": moment,
        }
        return token

    def session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        record = self._sessions.get(token)
        if record is None:
            return None
        moment = time.monotonic()
        if moment - record["seen"] > SESSION_IDLE_SECONDS or moment - record["created"] > SESSION_MAX_SECONDS:
            self._sessions.pop(token, None)
            return None
        record["seen"] = moment
        return record

    def valid(self, token: str | None) -> bool:
        return self._sessions.get(token or "") is not None

    def close(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)

    def revoke_app(self, app_id: str, *, generation: int | None = None) -> int:
        """End access to one app, optionally only for older code than `generation`."""
        removed = 0
        for token, record in list(self._sessions.items()):
            if record["app_id"] != app_id:
                continue
            if generation is not None and record["generation"] >= generation:
                continue
            self._sessions.pop(token, None)
            removed += 1
        for token, ticket in list(self._tickets.items()):
            if ticket["app_id"] == app_id:
                self._tickets.pop(token, None)
        return removed

    def revoke_owner(self, owner: str | None) -> int:
        """End every gateway session a Vela session opened. Sign-out and lock."""
        removed = 0
        for token, record in list(self._sessions.items()):
            if owner is None or record["owner"] == owner:
                self._sessions.pop(token, None)
                removed += 1
        for token, ticket in list(self._tickets.items()):
            if owner is None or ticket["owner"] == owner:
                self._tickets.pop(token, None)
        return removed

    def count(self, app_id: str | None = None) -> int:
        return sum(1 for record in self._sessions.values()
                   if app_id is None or record["app_id"] == app_id)

    def _expire(self) -> None:
        moment = time.monotonic()
        for token, ticket in list(self._tickets.items()):
            if ticket["expires"] < moment:
                self._tickets.pop(token, None)


class ManagedGateway:
    """ASGI middleware serving managed apps on their own hostnames."""

    def __init__(
        self,
        app,
        *,
        resolve: Callable[[str], dict[str, Any] | None],
        sessions: GatewaySessions,
        domains: Callable[[], Iterable[str]],
        hub_origin: Callable[[], str],
        owner_valid: Callable[[str | None], bool],
        attach: Callable[["ManagedGateway"], None] | None = None,
    ) -> None:
        self.app = app
        #: app id -> {"manifest": ManagedManifest, "generation": int,
        #:            "endpoint": (host, port) | None, "state": str}
        self._resolve = resolve
        self.sessions = sessions
        self._domains = domains
        self._hub_origin = hub_origin
        self._owner_valid = owner_valid
        #: One transport, no client, and therefore no cookie jar. An
        #: `httpx.Client` keeps cookies across requests, which between two
        #: browsers signed into the same app is a way for one person's session
        #: to be sent with another person's request. A transport moves bytes and
        #: remembers nothing.
        self._transport = httpx.AsyncHTTPTransport(retries=0)
        # Starlette builds middleware lazily, so this is how the factory gets
        # hold of the instance it needs to close at shutdown.
        if attach is not None:
            attach(self)

    async def aclose(self) -> None:
        await self._transport.aclose()

    # --------------------------------------------------------------- routing --

    def app_for_host(self, host: str | None) -> tuple[bool, str | None]:
        """`(ours, app_id)` for a `Host` header.

        The gateway claims the whole app domain, not only the names it has apps
        for. `evil.memos.apps.localhost` and `apps.localhost` itself are answered
        here with "no app is published on this address" rather than falling
        through to Vela's dashboard, because a name under the app domain must
        never reach the hub -- that is the isolation the layout is built on.

        A name outside every app domain is not ours at all and passes through
        untouched, which is what keeps this middleware invisible to the hub.
        """
        if not host:
            return False, None
        name = host.split(",")[0].strip().lower()
        if name.startswith("["):
            return False, None
        name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name
        for domain in self._domains():
            domain = domain.lower()
            if name == domain:
                return True, None
            suffix = "." + domain
            if name.endswith(suffix):
                label = name[: -len(suffix)]
                return True, label if (_LABEL_RE.match(label) and "." not in label) else None
        return False, None

    def origin_for(self, app_id: str, domain: str, *, scheme: str, port: int | None) -> str:
        host = f"{app_id}.{domain}"
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        return f"{scheme}://{host}"

    # ------------------------------------------------------------------ ASGI --

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        ours, app_id = self.app_for_host(_header(scope, b"host"))
        if not ours:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await self._refuse_websocket(scope, receive, send, app_id)
            return
        if app_id is None:
            await _page(
                404, "No app here",
                "Vela publishes each managed app on a name of its own, and there "
                "is none on this address.",
            )(scope, receive, send)
            return
        request = Request(scope, receive)
        response = await self._handle(request, app_id)
        if response is not None:
            await response(scope, receive, send)

    async def _refuse_websocket(self, scope, receive, send, app_id: str) -> None:
        """Close an upgrade with a reason instead of leaving it hanging.

        This host revision proxies HTTP, including server-sent events, and does
        not proxy WebSocket. Saying so immediately is the whole point: a socket
        that never opens and never closes is the failure that costs an afternoon.
        """
        await receive()
        record = self._resolve(app_id) if app_id else None
        name = record["manifest"].name if record else (app_id or "this app")
        LOG.info("managed gateway: refused a WebSocket upgrade for %s", app_id)
        await send({"type": "websocket.close", "code": 1008,
                    "reason": f"Vela does not proxy WebSocket connections to {name} yet."})

    # -------------------------------------------------------------- handling --

    async def _handle(self, request: Request, app_id: str) -> Response | None:
        path = request.url.path
        record = self._resolve(app_id)
        if record is None:
            return _page(404, "This app is not installed",
                         "Vela has no managed app published on this address.")
        if path == "/_vela/enter":
            return self._enter(request, app_id, record)
        token = self._cookie(request)
        if path == "/_vela/leave":
            if request.method != "POST":
                return _page(405, "Use the Sign out button", "That address takes a POST.")
            self.sessions.close(token)
            response = JSONResponse({"signedOut": True})
            _clear_cookie(response)
            return response
        session = self._authorised(token, record)
        if path == "/_vela/status":
            return JSONResponse({
                "app": app_id,
                "name": record["manifest"].name,
                "connected": session is not None,
                "state": record["state"],
            }, status_code=200 if session else 401)
        if path.startswith("/_vela/"):
            return _page(404, "Not found", "That address belongs to Vela, not to this app.")
        if session is None:
            return self._not_connected(request, record)
        refusal = self._cross_origin_refusal(request, app_id)
        if refusal is not None:
            return refusal
        endpoint = record["endpoint"]
        if endpoint is None:
            return _page(
                503,
                f"{record['manifest'].name} is not running",
                "Start it from Vela and then reload this page.",
                retry=True,
            )
        return await self._proxy(request, record, endpoint, token)

    def _cookie(self, request: Request) -> str | None:
        """Read the gateway cookie from the raw header, refusing ambiguity.

        Starlette's parsed cookies keep one value per name. A request carrying
        two `__Host-vela-app` cookies is not a request to guess about, so it is
        treated as carrying none.
        """
        found: list[str] = []
        for raw in request.headers.getlist("cookie"):
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:  # noqa: BLE001 - a malformed header carries nothing
                return None
            found.extend(morsel.value for name, morsel in jar.items() if name == SESSION_COOKIE)
        if len(found) != 1:
            return None
        return found[0]

    def _authorised(self, token: str | None, record: dict[str, Any]) -> dict[str, Any] | None:
        session = self.sessions.session(token)
        if session is None:
            return None
        if session["app_id"] != record["app_id"] or session["generation"] != record["generation"]:
            # The code was replaced since this session was opened. Authority was
            # reviewed against what is no longer installed.
            self.sessions.close(token)
            return None
        if not self._owner_valid(session["owner"]):
            self.sessions.close(token)
            return None
        return session

    def _enter(self, request: Request, app_id: str, record: dict[str, Any]) -> Response:
        """Redeem a launch ticket on the app's own origin."""
        token = request.query_params.get("t", "")
        dest = request.headers.get("sec-fetch-dest")
        mode = request.headers.get("sec-fetch-mode")
        if request.method not in ("GET", "HEAD"):
            return _page(405, "Open this app from Vela", "That address takes a visit, not a form.")
        # A launch link is somewhere a browser *goes*, in a tab or in Vela's own
        # window frame. Nothing else is a legitimate way to spend one, and the
        # alternatives are all worse: a ticket in an `<img src>` is not a person
        # choosing to open an app, and a `fetch` from a page on a sibling app's
        # hostname -- same-site, so `SameSite` does not stop it -- would let one
        # app burn the ticket another was about to use. Fetch Metadata is sent
        # by every browser Vela supports; a request without it falls through to
        # the ticket's own single-use and expiry checks.
        if dest is not None and dest not in ("document", "iframe"):
            return _page(400, "Open this app from Vela",
                         "A launch link is a page to visit, not a resource to fetch.")
        if mode is not None and mode != "navigate":
            return _page(400, "Open this app from Vela", "That launch link cannot be used this way.")
        ticket = self.sessions.redeem(token, host=request.headers.get("host", ""))
        if ticket is None:
            return _page(
                403, "That link has been used",
                "A launch link works once and lasts thirty seconds. "
                "Open the app from Vela again.",
            )
        if ticket["app_id"] != app_id or ticket["generation"] != record["generation"]:
            return _page(403, "That link is out of date",
                         "This app changed since the link was made. Open it from Vela again.")
        if not self._owner_valid(ticket["owner"]):
            return _page(401, "Sign in to Vela first",
                         "The Vela session that made this link is no longer signed in.")
        session = self.sessions.open_session(ticket)
        target = ticket["path"] or "/"
        if not target.startswith("/"):
            target = "/"
        response = RedirectResponse(target, status_code=303)
        _set_cookie(response, session, secure=request.url.scheme == "https" or _local(request))
        return response

    def _not_connected(self, request: Request, record: dict[str, Any]) -> Response:
        """No gateway session. A page for a person, a 401 for a script."""
        hub = self._hub_origin()
        name = record["manifest"].name
        accepts = request.headers.get("accept", "")
        if "text/html" not in accepts:
            return JSONResponse(
                {"detail": "Open this app from Vela.", "code": "managed.not_connected",
                 "status": 401, "hub": hub},
                status_code=401,
            )
        if request.headers.get("sec-fetch-dest") == "iframe":
            # Landing here inside a Vela window usually means one thing: the
            # browser would not keep the cookie Vela set, because the window is
            # a cross-site frame and this browser blocks cookies in one. That is
            # the browser's setting to make, not Vela's to work around, so the
            # page says what it is and offers the way that does work.
            return _page(
                401,
                f"{name} cannot open in this window",
                "This browser is not keeping cookies for a site shown inside another "
                "page, so Vela cannot let this window in. Open the app in a tab instead.",
                link=hub,
                target="_blank",
            )
        return _page(
            401,
            f"Open {name} from Vela",
            "This address only answers a browser that Vela let in. "
            "Open the app from your Vela dashboard.",
            link=hub,
        )

    def _cross_origin_refusal(self, request: Request, app_id: str) -> Response | None:
        """Only the app's own pages may use the app. Everyone else may arrive.

        This is the gateway's CSRF boundary, and it is here rather than on the
        cookie for a reason. `SameSite=Lax` would be the obvious answer and it
        fails twice: `memos.apps.localhost` and `photos.apps.localhost` share a
        registrable domain, so `Lax` never separated two apps from each other,
        and a browser refuses to *set* a `Lax` cookie at all when the app is
        opened inside a Vela window -- which is a cross-site frame. So the
        cookie says `SameSite=None` and this check does the work, which it does
        better: it covers a cross-site `GET` subresource too, where `Lax` would
        only have covered the unsafe methods.

        What is allowed from elsewhere is arriving: a top-level visit or the
        Vela window's frame loading the app. Everything a page *does* has to
        come from the app's own pages.

        A browser that sends no Fetch Metadata -- older Safari -- falls back to
        the `Origin` header on unsafe methods, which is what was checkable
        before those headers existed.
        """
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if site is None:
            if request.method not in _SAFE_METHODS and origin is not None and origin != expected:
                return _refuse_origin("That request came from another origin.")
            return None
        if site in ("same-origin", "none"):
            return None
        # Cross-site, or a sibling app under the same parent name.
        navigating = (request.headers.get("sec-fetch-mode") == "navigate"
                      and request.headers.get("sec-fetch-dest") in ("document", "iframe")
                      and request.method in _SAFE_METHODS)
        if navigating:
            return None
        return _refuse_origin("That request came from another site.")

    # --------------------------------------------------------------- proxying --

    async def _proxy(self, request: Request, record, endpoint, token) -> Response:
        host, port = endpoint
        manifest = record["manifest"]
        base = manifest.endpoint["basePath"].rstrip("/")
        url = httpx.URL(
            scheme="http",
            host=host,
            port=port,
            raw_path=(base + request.url.path).encode("utf-8") or b"/",
        )
        if request.url.query:
            url = url.copy_with(query=request.url.query.encode("utf-8"))
        headers = self._request_headers(request)
        upstream_request = httpx.Request(
            request.method,
            url,
            headers=headers,
            content=None if request.method in _BODYLESS else request.stream(),
        )
        try:
            upstream = await self._transport.handle_async_request(upstream_request)
        except httpx.HTTPError as exc:
            LOG.info("managed gateway: %s is unreachable: %s", record["app_id"], exc)
            return _page(
                502, f"{manifest.name} did not answer",
                "The app is installed and its address is right, but it did not respond. "
                "Check its log in Vela.",
                retry=True,
            )
        headers, cookies = self._response_headers(upstream, record["app_id"])
        response = StreamingResponse(
            self._body(upstream, token), status_code=upstream.status_code
        )
        # Set after construction rather than through `headers=`: a dict would
        # collapse several `Set-Cookie` values into one, and an application that
        # sets a session and a refresh cookie in the same response would arrive
        # having set only the last of them.
        response.raw_headers = list(headers) + [
            (b"set-cookie", cookie.encode("latin-1")) for cookie in cookies
        ]
        return response

    def _request_headers(self, request: Request) -> list[tuple[bytes, bytes]]:
        """What reaches the application: its own credentials, not Vela's.

        The app's `Authorization` and its own cookies go through untouched --
        Memos signs in with a bearer token and refreshes with a cookie, and a
        gateway that dropped either would look like a broken application. What
        does not go through is anything of Vela's: the gateway session cookie is
        removed by name, and Vela never adds a credential of its own.
        """
        headers: list[tuple[bytes, bytes]] = []
        for name, value in request.headers.raw:
            lowered = name.decode("latin-1").lower()
            if lowered in REQUEST_STRIP:
                continue
            if lowered == "authorization" and self._is_vela_credential(value):
                # An app's own bearer token goes through -- Memos signs in with
                # one. A *Vela* token presented on an app host does not: no
                # dashboard sends it here, so its presence is either a mistake
                # or an attempt, and either way the application has no business
                # receiving it.
                LOG.warning("managed gateway: a Vela token was sent to an app host; dropped")
                continue
            headers.append((name, value))
        forwarded = _filter_cookies(request.headers.getlist("cookie"))
        if forwarded:
            headers.append((b"cookie", forwarded.encode("latin-1")))
        client = request.client.host if request.client else ""
        headers.append((b"x-forwarded-for", client.encode("latin-1")))
        headers.append((b"x-forwarded-proto", request.url.scheme.encode("latin-1")))
        headers.append((b"x-forwarded-host", request.headers.get("host", "").encode("latin-1")))
        return headers

    def _is_vela_credential(self, raw: bytes) -> bool:
        value = raw.decode("latin-1", errors="replace")
        if not value.lower().startswith("bearer "):
            return False
        return self._owner_valid(value[7:].strip())

    def _response_headers(self, upstream: httpx.Response, app_id: str):
        """Everything the app sent back, with its cookies made host-only.

        `Domain` is stripped rather than rewritten. A managed app is published on
        exactly one host, so a cookie it sets belongs to that host; leaving a
        `Domain` in place would let it reach every sibling app under the shared
        parent name. Upstream frame defences (`X-Frame-Options`,
        `Content-Security-Policy`) are passed through untouched: if an app says
        it will not be framed, Vela opens it in a tab instead of overruling it.
        """
        headers: list[tuple[bytes, bytes]] = []
        cookies: list[str] = []
        for name, value in upstream.headers.raw:
            lowered = name.decode("latin-1").lower()
            if lowered in RESPONSE_STRIP:
                continue
            headers.append((name, value))
        for raw in upstream.headers.get_list("set-cookie"):
            rewritten = _host_only_cookie(raw)
            if rewritten is None:
                LOG.warning(
                    "managed gateway: %s tried to set a reserved cookie; refused", app_id
                )
                continue
            cookies.append(rewritten)
        return headers, cookies

    async def _body(self, upstream: httpx.Response, token: str | None):
        """Stream the response, and stop streaming when the session ends.

        The watchdog is what makes revocation apply to a connection that is
        already open. An event stream can sit silent for minutes, so waiting for
        the next chunk to notice would mean a locked Vela still feeding an open
        page. Closing the upstream response from another task raises inside the
        iterator, which is the intended end of this generator.
        """

        async def watchdog() -> None:
            while True:
                await asyncio.sleep(REVOCATION_CHECK_SECONDS)
                if not self.sessions.valid(token):
                    await upstream.aclose()
                    return

        guard = asyncio.create_task(watchdog())
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        except (httpx.HTTPError, RuntimeError, StopAsyncIteration):
            # The watchdog closed it, or the app hung up mid-response. Either
            # way the body ends here; the status line already went out.
            return
        finally:
            guard.cancel()
            await upstream.aclose()


def _filter_cookies(raw_headers: list[str]) -> str:
    """The browser's cookies minus the ones the gateway owns."""
    kept: list[str] = []
    for raw in raw_headers:
        for part in raw.split(";"):
            piece = part.strip()
            if not piece:
                continue
            name = piece.split("=", 1)[0].strip()
            if any(name.startswith(prefix) for prefix in RESERVED_COOKIE_PREFIXES):
                continue
            kept.append(piece)
    return "; ".join(kept)


def _host_only_cookie(raw: str) -> str | None:
    """Drop `Domain` from an upstream cookie, or refuse a reserved name."""
    name = raw.split("=", 1)[0].strip()
    if any(name.startswith(prefix) for prefix in RESERVED_COOKIE_PREFIXES):
        return None
    parts = [part for part in raw.split(";")
             if part.strip().split("=", 1)[0].strip().lower() != "domain"]
    return ";".join(parts)


def _set_cookie(response: Response, value: str, *, secure: bool) -> None:
    attributes = [
        f"{SESSION_COOKIE}={value}",
        "Path=/",
        "HttpOnly",
        # `None` because a Vela window is a cross-site frame, and a browser will
        # not even set a `Lax` cookie from one. What `Lax` would have protected
        # is done by `_cross_origin_refusal`, which is a stronger boundary here:
        # it separates two apps that share a parent name, which `Lax` never did.
        "SameSite=None",
    ]
    if secure:
        # `__Host-` requires it, and a browser refuses the cookie without it.
        # Plain HTTP on a non-local address is a setup Vela does not publish
        # apps on, so this is a guard rather than a fallback.
        attributes.append("Secure")
    response.raw_headers.append((b"set-cookie", "; ".join(attributes).encode("latin-1")))


def _clear_cookie(response: Response) -> None:
    response.raw_headers.append((
        b"set-cookie",
        f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=None; Secure; Max-Age=0".encode("latin-1"),
    ))


def _header(scope, name: bytes) -> str | None:
    """One header out of a raw ASGI scope, before any framework has parsed it."""
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _refuse_origin(detail: str) -> Response:
    return JSONResponse(
        {"detail": detail, "code": "managed.cross_origin", "status": 403}, status_code=403
    )


def _local(request: Request) -> bool:
    """Whether this origin is one browsers treat as trustworthy over plain HTTP."""
    host = (request.headers.get("host", "") or "").rsplit(":", 1)[0].lower()
    return host == "localhost" or host.endswith(".localhost") or host in ("127.0.0.1", "::1")


def _page(status: int, title: str, message: str, *, link: str | None = None,
          retry: bool = False, target: str | None = None) -> Response:
    """A small, self-contained page for the person who landed here.

    No script, no stylesheet, no asset: this is served on an app's origin, in
    situations where the app itself is not answering, and it must not depend on
    anything Vela serves elsewhere.
    """
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ margin:0; min-height:100vh; display:grid; place-items:center;
        font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; padding:24px; }}
 main {{ max-width:34rem; text-align:center; }}
 h1 {{ font-size:1.35rem; margin:0 0 .5rem; }}
 p {{ margin:0 0 1rem; opacity:.8; }}
 a {{ color:inherit; }}
</style></head>
<body><main><h1>{_escape(title)}</h1><p>{_escape(message)}</p>
{f'<p><a href="{_escape(link)}"{_target(target)}>Go to Vela</a></p>' if link else ''}
{'<p><a href="">Try again</a></p>' if retry else ''}
</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _target(value: str | None) -> str:
    """`target="_blank" rel="noopener"`, or nothing. Only ours, never a caller's."""
    if value not in ("_blank", "_top"):
        return ""
    return f' target="{value}" rel="noopener"'


def _escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def launch_path(path: str | None) -> str:
    """A deep link a launch ticket may carry, normalised to one safe form."""
    if not path or not isinstance(path, str):
        return "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return quote(path, safe="/?&=%#+,:@!$'()*~-._")
