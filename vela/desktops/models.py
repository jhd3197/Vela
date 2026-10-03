"""What a desktop is, and what may be stored as one.

A desktop is a persistent workspace: a name, an appearance, and the two boards
the desk already has. The `desktop` and `phone` board keys are responsive
layouts of one workspace, not two workspaces — that distinction is the reason
this module sits above `vela/desk.py` rather than replacing it.

Validation lives here so the store, the service and the HTTP layer all refuse
the same things. Board geometry is not re-implemented: `vela/desk.py` is still
the authority on what a board may contain, and this module calls it.
"""

from __future__ import annotations

import json
import re
from typing import Any
from ..app_storage import AppServiceError
from ..connections import _http_path
from ..errors_http import VelaError

#: The schema version of `desktops.sqlite`. An older Vela cannot read a newer
#: one; the upgrade notes say to restore the pre-upgrade backup rather than
#: attempt a reverse migration.
SCHEMA_VERSION = 1

#: Plenty for a person, few enough that a broken writer cannot grow the file
#: without bound. Section 2.6 of the plan proposes this and expects tuning.
MAX_DESKTOPS = 16

#: Long enough for a real name, short enough for the switcher to stay one line.
MAX_NAME = 60

#: A desktop the person uses, or one an agent runs in. The second kind is
#: accepted by the schema now and only becomes selectable when a runtime exists.
DESKTOP_KINDS = ("personal", "agent")

#: The appearance a desktop owns. Theme stays global: it is a property of the
#: person's eyes, not of the workspace.
#:
#: A wallpaper reference is a short name the dashboard understands — one of the
#: painted set in `web/src/desk/wallpaper.js`, `daily`, or `custom` meaning the
#: image uploaded for this desktop. The server checks the shape rather than the
#: list: which pictures ship is the dashboard's business, and a server that
#: refused a name it had not been told about would make adding one a two-repository
#: change for no safety gained.
WALLPAPER_REFERENCE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

#: Wallpapers that no longer ship, and what a desk still set to one now draws.
#: Normalised on the way in rather than refused, so migrating an older desk is
#: lossless; `vela/settings.py` does the same thing on read.
RETIRED_WALLPAPERS = {"lake": "choroni"}

#: What a desk with no stored choice draws, matching `DEFAULT_WALLPAPER` in
#: `web/src/desk/wallpaper.js`.
DEFAULT_WALLPAPER = "choroni"


class DesktopError(VelaError):
    """A desktop operation that cannot proceed, with the reason to show."""

    code = "desktop.refused"

    def __init__(self, status: int, detail: str, *, code: str | None = None):
        super().__init__(detail, code=code, status=status)


class DesktopConflict(DesktopError):
    """Someone else saved first. Carries the revision the caller should reload.

    The revision rides back on `X-Vela-Desk-Revision`, which `/api/desk` has
    always sent; the header is carried on the error so the one error handler
    renders it rather than each route remembering to.
    """

    code = "desktop.conflict"

    def __init__(self, detail: str, revision: int):
        super().__init__(409, detail)
        self.revision = revision
        self.headers = {"X-Vela-Desk-Revision": str(revision)}


_ID = re.compile(r"^[0-9a-f]{32}$")


def validate_id(value: Any) -> str:
    """A desktop id as this server issues them.

    Checked before it reaches SQL or a filename. An id arrives from a client on
    every scoped route, and one that could name a path is how a workspace id
    turns into a way to read the rest of the disk.
    """
    if not isinstance(value, str) or not _ID.match(value):
        raise DesktopError(404, "That desktop no longer exists.")
    return value


def validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise DesktopError(422, "A desktop needs a name.")
    name = " ".join(value.split())
    if not name:
        raise DesktopError(422, "A desktop needs a name.")
    if len(name) > MAX_NAME:
        raise DesktopError(422, f"A desktop name is at most {MAX_NAME} characters.")
    return name


def validate_kind(value: Any) -> str:
    if value not in DESKTOP_KINDS:
        raise DesktopError(422, "A desktop is either personal or agent.")
    return value


def validate_revision(value: Any, *, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DesktopError(422, f"The {what} revision must be a whole number.")
    return value


def default_name(existing: list[str]) -> str:
    """The next free "Desktop N", so creating one needs no typing."""
    taken = {name.strip().lower() for name in existing}
    index = 1
    while f"desktop {index}" in taken:
        index += 1
    return f"Desktop {index}"


def validate_appearance(value: Any) -> dict[str, Any]:
    """An appearance patch: which wallpaper, dimmed, and whether icons are labelled.

    `wallpaper` is either one of the pictures the dashboard ships or `custom`,
    which means "the image uploaded for this desktop". The uploaded bytes are
    validated by `vela/wallpaper.py`; this only checks the reference.
    """
    if not isinstance(value, dict):
        raise DesktopError(422, "An appearance is an object.")
    out: dict[str, Any] = {}
    if "wallpaper" in value:
        wallpaper = RETIRED_WALLPAPERS.get(value["wallpaper"], value["wallpaper"])
        if not isinstance(wallpaper, str) or not WALLPAPER_REFERENCE.match(wallpaper):
            raise DesktopError(422, "That is not a wallpaper this server can store.")
        out["wallpaper"] = wallpaper
    for flag in ("dim", "labels"):
        if flag in value:
            if not isinstance(value[flag], bool):
                raise DesktopError(422, f"{flag} is true or false.")
            out[flag] = value[flag]
    if not out:
        raise DesktopError(422, "That appearance change is empty.")
    return out


def default_appearance() -> dict[str, Any]:
    return {"wallpaper": DEFAULT_WALLPAPER, "dim": True, "labels": True}


# ------------------------------------------------------------------ views --

#: What a view can be. `app` is an installed Vela app, `web` an approved site,
#: `host` one of the owner surfaces below, `agent` the task window, and
#: `surface` a document an app serves over its http connection that the hub
#: draws itself. The last three are never handed to an agent as something to
#: look at or click, which is why they are separate kinds rather than flags.
VIEW_KINDS = ("app", "web", "host", "agent", "surface")

#: The owner surfaces a `host` view may be. A closed list on purpose — a `host`
#: view that could name any path would be a way to put the owner's dashboard,
#: with its credentials, inside something that is not the owner's dashboard.
HOST_SURFACES = ("library", "ask", "settings")

#: Kinds an agent may be pointed at. The rest are the owner's own controls.
AGENT_VIEWABLE_KINDS = ("app", "web")

#: Open views per desktop. Section 2.6 of the plan proposes this and expects
#: tuning; it exists so one desktop cannot exhaust the server's memory.
MAX_VIEWS_PER_DESKTOP = 8

#: Who opened a view. Kept apart so automated activity does not quietly rewrite
#: the person's own most-used ranking.
VIEW_ACTORS = ("human", "agent")

#: How a desktop's views are arranged. `split` is two panes; either may be empty
#: while the user decides what goes in it.
ARRANGEMENTS = ("floating", "maximized", "split")
#: The narrowest either pane may be dragged, as a fraction of the work area.
MIN_DIVIDER_RATIO = 0.2
MAX_DIVIDER_RATIO = 0.8

#: A window cannot be smaller than this or larger than this, in CSS pixels.
#: Geometry arrives from a browser that may have been resized, zoomed or moved
#: to another screen since, so it is bounded on the way in and clamped on the
#: way out rather than trusted. The minimums are per axis and match what the
#: viewer itself enforces (window-state.js): a window may be shorter than it is
#: narrow, and refusing that here only meant every move of a short window was
#: rejected while the viewer was told nothing.
MIN_WINDOW_WIDTH = 240
MIN_WINDOW_HEIGHT = 160
MAX_WINDOW = 20000


def validate_view_kind(value: Any) -> str:
    if value not in VIEW_KINDS:
        raise DesktopError(422, "That is not a kind of view.")
    return value


def validate_actor(value: Any) -> str:
    if value not in VIEW_ACTORS:
        raise DesktopError(422, "A view is opened by a person or by an agent.")
    return value


_APP_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_VIEW_ID = re.compile(r"^[0-9a-f]{32}$")


def validate_view_id(value: Any) -> str:
    if not isinstance(value, str) or not _VIEW_ID.match(value):
        raise DesktopError(404, "That view is no longer open.")
    return value


def validate_view_target(kind: str, target: Any) -> dict[str, Any]:
    """What a view of this kind points at, checked before it is stored.

    Returns the columns the store needs. Each kind is checked for its own thing
    and nothing else: an `app` view names an installed app, a `host` view names
    one of the allowlisted owner surfaces, and neither can be talked into being
    the other by sending both fields.
    """
    target = target if isinstance(target, dict) else {}
    if kind == "app":
        app_id = target.get("appId")
        if not isinstance(app_id, str) or not _APP_ID.match(app_id):
            raise DesktopError(422, "An app view names an installed app.")
        return {"app_id": app_id, "surface_key": None, "url": None, "source": None}
    if kind == "host":
        surface = target.get("surface")
        if surface not in HOST_SURFACES:
            raise DesktopError(422, "That is not a surface this server can open.")
        return {"app_id": None, "surface_key": surface, "url": None, "source": None}
    if kind == "web":
        url = target.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise DesktopError(422, "A web view needs an http or https address.")
        if len(url) > 2000:
            raise DesktopError(422, "That address is too long to store.")
        return {"app_id": None, "surface_key": None, "url": url, "source": None}
    if kind == "surface":
        app_id = target.get("appId")
        if not isinstance(app_id, str) or not _APP_ID.match(app_id):
            raise DesktopError(422, "A surface view names an installed app.")
        # The path under the app's http connection the document is fetched
        # from. The same rule the connection itself applies to every path it
        # sends: one absolute path, no dot segments, no encoded separators —
        # so a stored view cannot be aimed outside the connection's origin.
        try:
            source = _http_path(target.get("source"))
        except AppServiceError as exc:
            raise DesktopError(422, str(exc)) from exc
        return {"app_id": app_id, "surface_key": None, "url": None, "source": source}
    # `agent`: the task window for this desktop. It points at the desktop itself.
    return {"app_id": None, "surface_key": None, "url": None, "source": None}


def validate_bounds(value: Any) -> dict[str, int] | None:
    """A window's place in the workspace, or None for "wherever it lands".

    Coordinates may be negative — a window can hang off the left edge while it
    is being dragged — but a size cannot be absurd, and the numbers have to be
    numbers. The viewer clamps them to the work area it actually has; the server
    only refuses what could never be drawn.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DesktopError(422, "Window bounds are an object.")
    out: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise DesktopError(422, f"Window bounds need a numeric {key}.")
        number = int(number)
        if key in ("width", "height"):
            smallest = MIN_WINDOW_WIDTH if key == "width" else MIN_WINDOW_HEIGHT
            if not smallest <= number <= MAX_WINDOW:
                raise DesktopError(422, f"A window's {key} is out of range.")
        elif abs(number) > MAX_WINDOW:
            raise DesktopError(422, f"A window's {key} is out of range.")
        out[key] = number
    return out


def validate_divider(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopError(422, "The divider is a fraction of the work area.")
    ratio = float(value)
    if not MIN_DIVIDER_RATIO <= ratio <= MAX_DIVIDER_RATIO:
        raise DesktopError(422, "The divider leaves too little of one pane.")
    return round(ratio, 4)


def validate_arrangement(value: Any) -> str:
    if value not in ARRANGEMENTS:
        raise DesktopError(422, "That is not an arrangement this server stores.")
    return value


def validate_view_state(value: Any) -> dict[str, Any]:
    """The navigation state a view is allowed to remember.

    Deliberately small and deliberately not "whatever the page had in it". A
    view is restored by opening the same app at the same documented place, not
    by replaying a dump of its DOM, which is how a saved layout would end up
    holding somebody's half-typed message or a token.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DesktopError(422, "View state is an object.")
    out: dict[str, Any] = {}
    route = value.get("route")
    if route is not None:
        if not isinstance(route, str) or len(route) > 400:
            raise DesktopError(422, "A view's route is a short string.")
        out["route"] = route
    scroll = value.get("scroll")
    if scroll is not None:
        if isinstance(scroll, bool) or not isinstance(scroll, (int, float)):
            raise DesktopError(422, "A view's scroll position is a number.")
        out["scroll"] = int(scroll)
    if len(json.dumps(out)) > 1000:
        raise DesktopError(422, "That view state is too large to store.")
    return out


# --------------------------------------------------------------- surfaces --

#: The limits the surface contract puts on one document, enforced when the hub
#: fetches it. A document over any of them is refused whole — the renderer
#: never sees the half that did validate.
MAX_SURFACE_BYTES = 1048576
MAX_SURFACE_NODES = 1000
MAX_SURFACE_DEPTH = 8


def validate_surface_document(document: Any) -> dict[str, Any]:
    """A surface document the hub may draw, checked whole before it is served.

    The hub draws the document itself and nothing app-supplied executes, so
    the checks are about size and shape, not content: a tree too big to walk
    cheaply is refused rather than trimmed.
    """
    if not isinstance(document, dict) or document.get("surface") != 1:
        raise DesktopError(422, "That surface is in a format this hub cannot draw.")
    try:
        size = len(json.dumps(document).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise DesktopError(422, "That surface document could not be read.") from exc
    if size > MAX_SURFACE_BYTES:
        raise DesktopError(422, "That surface document is too large to draw.")
    nodes, depth = _measure(document.get("root"), 1)
    if nodes > MAX_SURFACE_NODES:
        raise DesktopError(422, "That surface has more parts than this hub can draw.")
    if depth > MAX_SURFACE_DEPTH:
        raise DesktopError(422, "That surface is nested deeper than this hub can draw.")
    return document


def _measure(value: Any, depth: int = 0) -> tuple[int, int]:
    """(nodes, deepest level) of a surface tree. Objects are nodes; lists are
    transparent, so a row in a list nests exactly as deep as the list does."""
    if isinstance(value, dict):
        nodes, deepest = 1, depth
        for child in value.values():
            child_nodes, child_depth = _measure(child, depth + 1)
            nodes += child_nodes
            deepest = max(deepest, child_depth)
        return nodes, deepest
    if isinstance(value, list):
        nodes, deepest = 0, depth
        for child in value:
            child_nodes, child_depth = _measure(child, depth)
            nodes += child_nodes
            deepest = max(deepest, child_depth)
        return nodes, deepest
    return 0, depth
