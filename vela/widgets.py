"""Widget summaries apps publish for the desk.

An app never renders on the desk. It publishes a small, schema-checked JSON
summary of one of the widgets it declared in its manifest, and the host draws
that with its own components. That is the whole trust model: the payload is
data, it is capped, every string is plain text, and the desk always shows which
app a summary came from.

Summaries live beside app data in `app-data.sqlite` and are keyed by app id
rather than by installation identity, because the desk has to read them without
an app session and they survive an app being stopped. Uninstalling an app
removes them.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .app_storage import AppServiceError

#: The manifest side of the contract.
WIDGET_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_WIDGETS_PER_APP = 4
WIDGET_LAYOUTS = ("stat", "progress", "list", "actions")
WIDGET_SIZES = ("s", "m", "l")

#: The published payload's limits. 4 KB is generous for a stat and a handful of
#: rows, and small enough that a misbehaving app cannot fill the database.
MAX_SUMMARY_BYTES = 4096
MAX_STRING = 200
MAX_ROWS = 8
MAX_ACTIONS = 3

_SCALARS = (str, int, float, bool)


class WidgetError(AppServiceError):
    """A summary or declaration that cannot be stored, with the reason."""


def validate_declarations(widgets: Any, folder: str) -> list[dict[str, Any]]:
    """Check a manifest's `widgets[]`. Raises ValueError with the reason."""
    if not isinstance(widgets, list):
        raise ValueError(f"{folder}: widgets must be a list")
    if len(widgets) > MAX_WIDGETS_PER_APP:
        raise ValueError(f"{folder}: at most {MAX_WIDGETS_PER_APP} widgets per app")
    seen: set[str] = set()
    out = []
    for entry in widgets:
        if not isinstance(entry, dict):
            raise ValueError(f"{folder}: each widget is an object")
        widget_id = entry.get("id")
        if not isinstance(widget_id, str) or not WIDGET_ID_RE.match(widget_id):
            raise ValueError(f"{folder}: widget id must match [a-z][a-z0-9-]{{0,31}}")
        if widget_id in seen:
            raise ValueError(f"{folder}: duplicate widget id {widget_id!r}")
        seen.add(widget_id)
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ValueError(f"{folder}: widget {widget_id} needs a name of at most 40 characters")
        if entry.get("layout") not in WIDGET_LAYOUTS:
            raise ValueError(f"{folder}: widget {widget_id} layout must be one of "
                             f"{', '.join(WIDGET_LAYOUTS)}")
        if entry.get("size") not in WIDGET_SIZES:
            raise ValueError(f"{folder}: widget {widget_id} size must be s, m or l")
        unknown = set(entry) - {"id", "name", "layout", "size"}
        if unknown:
            raise ValueError(f"{folder}: widget {widget_id} has unknown fields: "
                             f"{', '.join(sorted(unknown))}")
        out.append({"id": widget_id, "name": name, "layout": entry["layout"], "size": entry["size"]})
    return out


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise WidgetError(422, f"{field} must be text")
    if len(value) > MAX_STRING:
        raise WidgetError(422, f"{field} is longer than {MAX_STRING} characters")
    return value


def validate_summary(payload: Any) -> dict[str, Any]:
    """Check one published summary and return the stored shape.

    Everything is optional: an app with nothing to say yet may publish `{}`,
    and the desk shows its "open the app to update" state. What is present is
    checked strictly — one level of nesting, plain strings, no surprises.
    """
    if not isinstance(payload, dict):
        raise WidgetError(422, "a summary is a JSON object")
    encoded = json.dumps(payload)
    if len(encoded.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise WidgetError(413, f"a summary is at most {MAX_SUMMARY_BYTES} bytes")

    allowed = {"value", "unit", "delta", "caption", "progress", "rows", "actions",
               "attention", "expiresAt"}
    unknown = set(payload) - allowed
    if unknown:
        raise WidgetError(422, f"unknown summary fields: {', '.join(sorted(unknown))}")

    out: dict[str, Any] = {}
    for field in ("value", "unit", "delta", "caption"):
        if field in payload:
            out[field] = _text(payload[field], field)

    if "progress" in payload:
        progress = payload["progress"]
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            raise WidgetError(422, "progress must be a number between 0 and 100")
        if not 0 <= progress <= 100:
            raise WidgetError(422, "progress must be between 0 and 100")
        out["progress"] = round(float(progress), 1)

    if "rows" in payload:
        rows = payload["rows"]
        if not isinstance(rows, list):
            raise WidgetError(422, "rows must be a list")
        if len(rows) > MAX_ROWS:
            raise WidgetError(422, f"at most {MAX_ROWS} rows")
        checked = []
        for row in rows:
            if not isinstance(row, dict):
                raise WidgetError(422, "each row is an object with a label")
            unknown_row = set(row) - {"label", "detail"}
            if unknown_row:
                raise WidgetError(422, f"unknown row fields: {', '.join(sorted(unknown_row))}")
            entry = {"label": _text(row.get("label", ""), "row label")}
            if "detail" in row:
                entry["detail"] = _text(row["detail"], "row detail")
            checked.append(entry)
        out["rows"] = checked

    if "actions" in payload:
        actions = payload["actions"]
        if not isinstance(actions, list):
            raise WidgetError(422, "actions must be a list")
        if len(actions) > MAX_ACTIONS:
            raise WidgetError(422, f"at most {MAX_ACTIONS} actions")
        checked = []
        for action in actions:
            if not isinstance(action, dict) or set(action) - {"action", "label"}:
                raise WidgetError(422, "each action is {action, label}")
            name = action.get("action")
            if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9-]*$", name):
                raise WidgetError(422, "an action names one of the app's own actions")
            checked.append({"action": name, "label": _text(action.get("label", ""), "action label")})
        out["actions"] = checked

    if "attention" in payload:
        if not isinstance(payload["attention"], bool):
            raise WidgetError(422, "attention is true or false")
        out["attention"] = payload["attention"]

    if "expiresAt" in payload:
        stamp = payload["expiresAt"]
        if not isinstance(stamp, str):
            raise WidgetError(422, "expiresAt must be an ISO 8601 timestamp")
        try:
            datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            raise WidgetError(422, "expiresAt must be an ISO 8601 timestamp") from None
        out["expiresAt"] = stamp

    for value in out.values():
        if isinstance(value, list):
            for item in value:
                if any(not isinstance(field, _SCALARS) for field in item.values()):
                    raise WidgetError(422, "summaries hold text, not nested objects")
    return out


class Widgets:
    """Published summaries, one row per (app, widget)."""

    def __init__(self, storage, registry, actions=None):
        self._storage = storage
        self._registry = registry
        self._actions = actions
        with storage.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS widget_summaries (
                    app_id TEXT NOT NULL,
                    widget_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (app_id, widget_id)
                );
                """
            )

    # --------------------------------------------------------------- writing

    def publish(self, session: dict, widget_id: str, payload: Any) -> dict[str, Any]:
        app_id = session["app_id"]
        if not self._registry.is_installed(app_id):
            raise WidgetError(401, "App is no longer installed")
        if "widgets" not in session["capabilities"]:
            raise WidgetError(403, "Widgets capability was not granted")
        declared = {entry["id"] for entry in self.declared(app_id)}
        if widget_id not in declared:
            raise WidgetError(422, f"{app_id} does not declare a widget called {widget_id!r}")
        summary = validate_summary(payload)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._storage.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO widget_summaries VALUES (?, ?, ?, ?)",
                (app_id, widget_id, json.dumps(summary), updated_at),
            )
        return {"ok": True, "widgetId": widget_id, "updatedAt": updated_at}

    def forget(self, app_id: str) -> None:
        """Drop an app's summaries. Called when it is uninstalled: a desk must
        not keep showing a line from an app that is no longer there."""
        with self._storage.connection() as db:
            db.execute("DELETE FROM widget_summaries WHERE app_id=?", (app_id,))

    # --------------------------------------------------------------- reading

    def declared(self, app_id: str) -> list[dict[str, Any]]:
        manifest = self._registry.get(app_id)
        return list(manifest.widgets) if manifest else []

    def _rows(self, app_id: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
        query = "SELECT * FROM widget_summaries"
        parameters: tuple = ()
        if app_id is not None:
            query += " WHERE app_id=?"
            parameters = (app_id,)
        with self._storage.connection() as db:
            rows = db.execute(query, parameters).fetchall()
        out = {}
        for row in rows:
            try:
                summary = json.loads(row["summary"])
            except json.JSONDecodeError:
                continue
            out[(row["app_id"], row["widget_id"])] = {
                "summary": summary,
                "updatedAt": row["updated_at"],
            }
        return out

    def _granted_actions(self, app_id: str) -> list[str]:
        """Which of this app's action requests the user has actually allowed.

        Only asked for when a summary names an action, because it is the one
        thing that makes the question worth the work.
        """
        if self._actions is None:
            return []
        try:
            status = self._actions.status(app_id)
        except Exception:
            return []
        return sorted(
            request["action"]
            for request in status.get("requests", [])
            if request.get("granted")
        )

    def for_app(self, app_id: str) -> dict[str, Any]:
        """Every widget this app declares, with its latest summary or none."""
        if self._registry.get(app_id) is None:
            raise WidgetError(404, f"unknown app: {app_id}")
        stored = self._rows(app_id)
        return {
            "appId": app_id,
            "widgets": [
                {
                    **declaration,
                    "appId": app_id,
                    **(stored.get((app_id, declaration["id"])) or {"summary": None,
                                                                  "updatedAt": None}),
                }
                for declaration in self.declared(app_id)
            ],
        }

    def all(self) -> dict[str, Any]:
        """Every declared widget of every installed app, for the desk and the
        rail's attention dot. One request, not one per app."""
        stored = self._rows()
        out = []
        granted: dict[str, list[str]] = {}
        for summary in self._registry.list_apps():
            if not summary.get("installed"):
                continue
            app_id = summary["id"]
            for declaration in self.declared(app_id):
                record = stored.get((app_id, declaration["id"])) or {
                    "summary": None,
                    "updatedAt": None,
                }
                entry = {**declaration, "appId": app_id, "appName": summary["name"], **record}
                if (record["summary"] or {}).get("actions"):
                    if app_id not in granted:
                        granted[app_id] = self._granted_actions(app_id)
                    entry["grantedActions"] = granted[app_id]
                out.append(entry)
        return {"widgets": out}
