"""Bindings from an app to a service it talks to. Never owns its lifecycle.

Two providers. ``ollama`` reads model information from a server on this
computer or the LAN, at an address the owner binds. ``http`` reaches the one
public HTTPS origin a manifest declares, with an optional secret (an API key or
token) the owner pastes into the app's Vela settings. The engine adds the
secret as a request header, so it never reaches the app's frame.
"""
import asyncio
import hashlib
import ipaddress
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .app_storage import AppServiceError


def _connection_digest(operation, payload):
    """What an approval for this call is bound to.

    The operation and the payload, together. Approving "ask the model this"
    is not approving "ask it that", and a digest over the operation alone
    would make those the same decision.
    """
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

_LAN = [ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]


def validate_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        if url.scheme not in ("http", "https") or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError()
        host = "127.0.0.1" if url.hostname == "localhost" else url.hostname
        address = ipaddress.ip_address(host)
        if not address.is_loopback and not any(address in network for network in _LAN):
            raise ValueError()
        if address.is_multicast or address.is_unspecified or address.is_link_local:
            raise ValueError()
        port = url.port or (443 if url.scheme == "https" else 80)
        if not 0 < port < 65536: raise ValueError()
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"{url.scheme}://{host}:{port}"
    except (ValueError, TypeError):
        raise AppServiceError(422, "Use a loopback or private LAN IP URL with a port, without credentials, paths or query strings")


# The http provider: one public HTTPS origin per app, only the methods the
# manifest declared, and bounded both ways.
_HTTP_TIMEOUT = 15
_HTTP_RESPONSE_CAP = 2 * 1048576
_HTTP_BODY_CAP = 262144
_HTTP_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,1023}")
_HTTP_SECRET = re.compile(r"[\x21-\x7e]{1,4096}")
_HTTP_QUERY_KEY = re.compile(r"[A-Za-z0-9._\[\]-]{1,64}")


def _http_path(path):
    if not isinstance(path, str) or not _HTTP_PATH.fullmatch(path) or "//" in path:
        raise AppServiceError(422, "path must be an absolute path such as /user, without a query string")
    lowered = path.lower()
    if {".", ".."} & set(path.split("/")) or any(code in lowered for code in ("%2e", "%2f", "%5c")):
        raise AppServiceError(422, "path cannot contain dot segments or encoded separators")
    return path


def _http_query(query):
    if query is None:
        return {}
    if not isinstance(query, dict) or len(query) > 32:
        raise AppServiceError(422, "query must be an object of at most 32 values")
    params = {}
    for key, value in query.items():
        if not _HTTP_QUERY_KEY.fullmatch(key):
            raise AppServiceError(422, "Invalid query parameter name")
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (int, float)) and value == value:
            value = str(value)
        if not isinstance(value, str) or len(value) > 1000:
            raise AppServiceError(422, "Query values must be text, numbers or booleans")
        params[key] = value
    return params


class Connections:
    def __init__(self, registry, storage, transport=None, guard=None):
        self.registry, self.storage, self.transport = registry, storage, transport
        # What has to be true for this caller to reach out. None for a person.
        self.guard = guard or (lambda *args, **kwargs: None)
        with storage.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS connections (identity TEXT PRIMARY KEY, provider TEXT NOT NULL, endpoint TEXT NOT NULL, checked_at TEXT NOT NULL, secret TEXT)")
            if "secret" not in {row[1] for row in db.execute("PRAGMA table_info(connections)")}:
                db.execute("ALTER TABLE connections ADD COLUMN secret TEXT")

    def context(self, app_id):
        manifest = self.registry.get(app_id)
        if not manifest or not self.registry.is_installed(app_id):
            raise AppServiceError(404, "App is not installed")
        if "connections" not in manifest.capabilities or not manifest.raw.get("connection"):
            raise AppServiceError(403, "App has no connection grant")
        return manifest, self.storage.activate(app_id)

    def _binding(self, identity):
        with self.storage.connection() as db:
            self.storage._app_id(db, identity)
            row = db.execute("SELECT * FROM connections WHERE identity=?", (identity,)).fetchone()
            return dict(row) if row else None

    def _http_secret(self, connection, binding):
        """The saved secret, but only for the origin it was saved for.

        An update that moves the app to another origin must not carry the
        owner's token there; they save it again for the new one.
        """
        if binding and binding["provider"] == "http" and binding["endpoint"] == connection["baseUrl"]:
            return binding["secret"]
        return None

    def status(self, app_id):
        manifest, identity = self.context(app_id)
        binding = self._binding(identity)
        connection = manifest.raw["connection"]
        if connection["provider"] == "http":
            spec = connection.get("secret")
            configured = bool(self._http_secret(connection, binding))
            return {"connected": configured or not (spec and spec.get("required")), "provider": "http",
                    "ownership": "connected", "endpoint": connection["baseUrl"],
                    "checked_at": binding["checked_at"] if configured else None,
                    "operations": connection["operations"],
                    "methods": connection.get("methods", ["GET"]),
                    "secret": {"label": spec["label"], "description": spec.get("description", ""),
                               "placeholder": spec.get("placeholder", ""), "required": bool(spec.get("required")),
                               "configured": configured} if spec else None}
        return {"connected": bool(binding), "provider": "ollama", "ownership": "connected",
                "endpoint": binding["endpoint"] if binding else None,
                "checked_at": binding["checked_at"] if binding else None,
                "operations": manifest.raw["connection"]["operations"]}

    async def _request(self, endpoint, operation, payload):
        paths = {"models.list": ("GET", "/api/tags"), "server.version": ("GET", "/api/version"), "models.show": ("POST", "/api/show")}
        if operation not in paths:
            raise AppServiceError(403, "Operation is not granted")
        if not isinstance(payload, dict): raise AppServiceError(422, "Operation input must be an object")
        if operation == "models.show":
            if set(payload) != {"model"} or not isinstance(payload["model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", payload["model"]):
                raise AppServiceError(422, "Supply one valid model name")
        elif payload:
            raise AppServiceError(422, "This operation takes no arguments")
        method, path = paths[operation]
        try:
            async with asyncio.timeout(5), httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async with client.stream(method, validate_endpoint(endpoint) + path, json=payload if method == "POST" else None) as response:
                    if 300 <= response.status_code < 400:
                        raise AppServiceError(502, "Ollama redirects are not followed")
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1048576:
                            raise AppServiceError(502, "Ollama response exceeds 1 MiB")
                    data = json.loads(body)
            if not isinstance(data, dict): raise ValueError()
            if operation == "models.list":
                if not isinstance(data.get("models"), list): raise ValueError()
                models = []
                for model in data["models"]:
                    if not isinstance(model, dict) or not isinstance(model.get("name"), str) or type(model.get("size", 0)) is not int: raise ValueError()
                    models.append({"name": model["name"], "size": model.get("size", 0), "digest": str(model.get("digest", "")), "details": model.get("details", {})})
                return {"models": models}
            if operation == "server.version":
                if not isinstance(data.get("version"), str): raise ValueError()
                return {"version": data["version"]}
            return {"details": data.get("details", {}), "model_info": data.get("model_info", {})}
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise AppServiceError(504, "Ollama did not respond within 5 seconds") from exc
        except httpx.HTTPError as exc:
            raise AppServiceError(502, "Ollama is unreachable or rejected the request") from exc
        except (ValueError, TypeError) as exc:
            raise AppServiceError(502, "Ollama returned an invalid response") from exc

    async def _http_request(self, connection, secret, payload):
        if not isinstance(payload, dict) or set(payload) - {"method", "path", "query", "body"}:
            raise AppServiceError(422, "A request takes method, path, query and body")
        method = payload.get("method", "GET")
        if method not in connection.get("methods", ["GET"]):
            raise AppServiceError(403, "That method is not granted")
        path = _http_path(payload.get("path"))
        params = _http_query(payload.get("query"))
        headers = {"User-Agent": "Vela", **connection.get("headers", {})}
        content = None
        if "body" in payload:
            if method == "GET":
                raise AppServiceError(422, "A GET request has no body")
            try:
                content = json.dumps(payload["body"], separators=(",", ":"), allow_nan=False).encode("utf-8")
            except ValueError as exc:
                raise AppServiceError(422, "body must be JSON") from exc
            if len(content) > _HTTP_BODY_CAP:
                raise AppServiceError(413, "Request body exceeds 256 KiB")
            headers["Content-Type"] = "application/json"
        spec = connection.get("secret")
        if spec and secret:
            headers[spec["header"]] = spec.get("prefix", "") + secret
        elif spec and spec.get("required"):
            raise AppServiceError(409, f"Add the {spec['label']} in the app's Vela settings")
        host = urlsplit(connection["baseUrl"]).hostname
        try:
            async with asyncio.timeout(_HTTP_TIMEOUT), httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async with client.stream(method, connection["baseUrl"] + path, params=params, headers=headers, content=content) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _HTTP_RESPONSE_CAP:
                            raise AppServiceError(502, f"{host} sent more than 2 MiB")
                    exposed = {name: response.headers[name] for name in connection.get("exposeHeaders", []) if name in response.headers}
                    kind = response.headers.get("content-type", "")
                    status = response.status_code
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise AppServiceError(504, f"{host} did not respond within {_HTTP_TIMEOUT} seconds") from exc
        except httpx.HTTPError as exc:
            raise AppServiceError(502, f"{host} is unreachable") from exc
        data = text = body.decode("utf-8", errors="replace")
        if "json" in kind and text:
            try:
                data = json.loads(text)
            except ValueError:
                pass
        # Every status is an answer rather than an error: a 404 or a rate
        # limit is something the app explains in its own words.
        return {"status": status, "headers": exposed, "body": data}

    def save_secret(self, app_id, secret):
        manifest, identity = self.context(app_id)
        connection = manifest.raw["connection"]
        if connection["provider"] != "http" or not connection.get("secret"):
            raise AppServiceError(422, "This app does not use a secret")
        if not isinstance(secret, str) or not _HTTP_SECRET.fullmatch(secret):
            raise AppServiceError(422, "Paste the value without spaces or line breaks, up to 4096 characters")
        with self.storage.connection() as db:
            self.storage._app_id(db, identity)
            db.execute("INSERT OR REPLACE INTO connections (identity, provider, endpoint, checked_at, secret) VALUES (?, 'http', ?, ?, ?)",
                       (identity, connection["baseUrl"], datetime.now(timezone.utc).isoformat(), secret))
        return self.status(app_id)

    async def bind(self, app_id, endpoint=None, secret=None):
        manifest, identity = self.context(app_id)
        if manifest.raw["connection"]["provider"] == "http":
            if endpoint is not None:
                raise AppServiceError(422, "This app's address is fixed by its manifest")
            return self.save_secret(app_id, secret)
        if secret is not None or endpoint is None:
            raise AppServiceError(422, "Supply the server address only")
        endpoint = validate_endpoint(endpoint)
        await self._request(endpoint, "server.version", {})
        await self._request(endpoint, "models.list", {})
        with self.storage.connection() as db:
            self.storage._app_id(db, identity)
            db.execute("INSERT OR REPLACE INTO connections (identity, provider, endpoint, checked_at) VALUES (?, 'ollama', ?, ?)", (identity, endpoint, datetime.now(timezone.utc).isoformat()))
        return self.status(app_id)

    def disconnect(self, app_id):
        _, identity = self.context(app_id)
        with self.storage.connection() as db:
            db.execute("DELETE FROM connections WHERE identity=?", (identity,))
        return {"connected": False}

    @staticmethod
    def _note(connection, operation, payload):
        if connection["provider"] == "http":
            method = payload.get("method", "GET") if isinstance(payload, dict) else "GET"
            return f"It would send a {method} request to {urlsplit(connection['baseUrl']).hostname}."
        return f"It would send “{operation}” to the service this app is connected to."

    async def invoke(self, session, operation, payload):
        manifest = self.registry.get(session["app_id"])
        if "connections" not in session["capabilities"] or not manifest or operation not in manifest.raw.get("connection", {}).get("operations", []):
            raise AppServiceError(403, "Operation is not granted")
        # An agent needs a grant for this, and the check happens before the
        # request leaves rather than around it. A call to another service cannot
        # be rolled back, so "checked, then dispatched" is the honest shape here
        # and the outcome of a lost response stays unknown rather than failed.
        authorize = self.guard(
            session,
            "connection",
            scope={"operation": operation},
            request_digest=_connection_digest(operation, payload),
            note=self._note(manifest.raw["connection"], operation, payload),
        )
        if authorize is not None:
            with self.storage.connection() as db:
                authorize(db)
        binding = self._binding(session["installationId"])
        connection = manifest.raw["connection"]
        if connection["provider"] == "http":
            return await self._http_request(connection, self._http_secret(connection, binding), payload)
        if not binding:
            raise AppServiceError(409, "Connect an existing Ollama server from the app's Vela settings")
        return await self._request(binding["endpoint"], operation, payload)
