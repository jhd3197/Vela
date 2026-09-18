"""Read-only bindings to existing Ollama servers. Never owns their lifecycle."""
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


class Connections:
    def __init__(self, registry, storage, transport=None, guard=None):
        self.registry, self.storage, self.transport = registry, storage, transport
        # What has to be true for this caller to reach out. None for a person.
        self.guard = guard or (lambda *args, **kwargs: None)
        with storage.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS connections (identity TEXT PRIMARY KEY, provider TEXT NOT NULL, endpoint TEXT NOT NULL, checked_at TEXT NOT NULL)")

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

    def status(self, app_id):
        manifest, identity = self.context(app_id)
        binding = self._binding(identity)
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
                    import json
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

    async def bind(self, app_id, endpoint):
        _, identity = self.context(app_id)
        endpoint = validate_endpoint(endpoint)
        await self._request(endpoint, "server.version", {})
        await self._request(endpoint, "models.list", {})
        with self.storage.connection() as db:
            self.storage._app_id(db, identity)
            db.execute("INSERT OR REPLACE INTO connections VALUES (?, 'ollama', ?, ?)", (identity, endpoint, datetime.now(timezone.utc).isoformat()))
        return self.status(app_id)

    def disconnect(self, app_id):
        _, identity = self.context(app_id)
        with self.storage.connection() as db:
            db.execute("DELETE FROM connections WHERE identity=?", (identity,))
        return {"connected": False}

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
            note=f"It would send “{operation}” to the service this app is connected to.",
        )
        if authorize is not None:
            with self.storage.connection() as db:
                authorize(db)
        binding = self._binding(session["installationId"])
        if not binding:
            raise AppServiceError(409, "Connect an existing Ollama server from the app's Vela settings")
        return await self._request(binding["endpoint"], operation, payload)
