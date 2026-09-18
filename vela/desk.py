"""What a desk's boards may contain.

Two boards: `desktop` (six columns) and `phone` (two). They are edited and
stored separately and are never reflowed into one another — an arrangement the
user chose for a 1440px screen is not the arrangement they want in their hand.

The boards themselves belong to a desktop and are stored by `vela/desktops/`.
This module is the authority on their geometry, and on nothing else: the
`validate` / `seed` / `repair-on-read` shape below is what both the PUT path and
the read path call.

The shape follows ServerKit's `dashboard_service.py` (MIT, same owner). The
geometry rules are the same ones `web/src/desk/grid/layout.js` implements in the
browser: a widget is a rectangle on a fixed column grid, it fits inside the
board, and no two overlap. The server is authoritative — a board that reaches
these functions by any other route than the dashboard still has to be
renderable.
"""

from __future__ import annotations

import json
from typing import Any
from .errors_http import Unprocessable

BOARD_VERSION = 1
#: Columns per board, and the only board names there are.
BOARD_COLS = {"desktop": 6, "phone": 2}
#: Enough for a busy desk, few enough that a broken writer cannot grow the file
#: without bound.
MAX_WIDGETS_PER_BOARD = 40

#: The widget types Vela itself provides. Kept in step with
#: `web/src/desk/types.jsx`; app-provided types are passed in per request.
CORE_WIDGET_TYPES = (
    "clock",
    "apps",
    "running",
    "ask",
    "needs-you",
    "system",
    "volume",
    "flows",
    "backups",
    "health",
)


class DeskError(Unprocessable):
    """A board that cannot be stored, with the reason to show the user."""

    code = "desk.invalid"


def default_boards() -> dict[str, Any]:
    """The desk before anyone arranges it.

    Mirrors `defaultBoards()` in `web/src/desk/boards.js`. Every seeded widget
    draws data Vela already has, so a first run shows a working desk rather than
    an empty grid with an invitation.
    """
    return {
        "version": BOARD_VERSION,
        "desktop": {
            "cols": 6,
            "widgets": [
                _widget("w1", "clock", 0, 0, 2, 1),
                _widget("w2", "apps", 2, 0, 4, 2),
                _widget("w3", "running", 0, 1, 2, 1),
                _widget("w4", "needs-you", 0, 2, 2, 2),
                _widget("w5", "ask", 2, 2, 4, 2),
            ],
        },
        "phone": {
            "cols": 2,
            "widgets": [
                _widget("w1", "clock", 0, 0, 2, 1),
                _widget("w2", "needs-you", 0, 1, 2, 1),
                _widget("w3", "apps", 0, 2, 2, 3),
                _widget("w4", "ask", 0, 5, 2, 1),
            ],
        },
    }


def _widget(i: str, kind: str, x: int, y: int, w: int, h: int) -> dict[str, Any]:
    return {"i": i, "type": kind, "x": x, "y": y, "w": w, "h": h, "cfg": {}}


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same rule as `overlaps` in `layout.js`; a widget never overlaps itself."""
    return (
        a["i"] != b["i"]
        and a["x"] < b["x"] + b["w"]
        and a["x"] + a["w"] > b["x"]
        and a["y"] < b["y"] + b["h"]
        and a["y"] + a["h"] > b["y"]
    )


def _is_index(value: Any) -> bool:
    # bool is an int in Python, and `True` as an x coordinate is a bug, not a 1.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_widgets(widgets: Any, cols: int, known_types: set[str]) -> list[dict[str, Any]]:
    """Check one board's widgets, raising DeskError with a usable reason.

    This is the PUT path: the dashboard sends what it drew, and anything that
    would not draw again is refused rather than stored. `repair_widgets` is the
    read path, which is allowed to be lenient because the alternative there is a
    blank desk.
    """
    if not isinstance(widgets, list):
        raise DeskError("widgets must be a list")
    if len(widgets) > MAX_WIDGETS_PER_BOARD:
        raise DeskError(f"a board holds at most {MAX_WIDGETS_PER_BOARD} widgets")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in widgets:
        if not isinstance(entry, dict):
            raise DeskError("each widget is an object")
        i = entry.get("i")
        kind = entry.get("type")
        if not isinstance(i, str) or not i:
            raise DeskError("each widget needs an id")
        if i in seen:
            raise DeskError(f"two widgets share the id {i}")
        seen.add(i)
        if not isinstance(kind, str) or kind not in known_types:
            raise DeskError(f"unknown widget type {kind!r}")
        x, y, w, h = entry.get("x"), entry.get("y"), entry.get("w"), entry.get("h")
        if not _is_index(x) or not _is_index(y):
            raise DeskError(f"{i} needs whole, non-negative coordinates")
        if not _is_index(w) or not _is_index(h) or w < 1 or h < 1:
            raise DeskError(f"{i} must be at least one cell")
        if x + w > cols:
            raise DeskError(f"{i} does not fit in {cols} columns")
        cfg = entry.get("cfg", {})
        if not isinstance(cfg, dict):
            raise DeskError(f"{i} has invalid options")
        if len(json.dumps(cfg)) > 2000:
            raise DeskError(f"{i} has too many options")
        out.append({"i": i, "type": kind, "x": x, "y": y, "w": w, "h": h, "cfg": cfg})
    for index, a in enumerate(out):
        for b in out[index + 1 :]:
            if overlaps(a, b):
                raise DeskError(f"{a['i']} and {b['i']} overlap")
    return out


def repair_widgets(widgets: Any, cols: int, known_types: set[str]) -> list[dict[str, Any]]:
    """Make a stored board renderable, dropping only what cannot be drawn.

    A widget whose type is gone (its app was uninstalled) is removed; one that
    hangs off the right edge is narrowed and pulled back; overlaps are resolved
    by pushing the later widget down. Refusing to load would cost the user their
    whole arrangement over one bad entry.
    """
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in widgets if isinstance(widgets, list) else []:
        if not isinstance(entry, dict):
            continue
        i, kind = entry.get("i"), entry.get("type")
        if not isinstance(i, str) or not i or i in seen:
            continue
        if not isinstance(kind, str) or kind not in known_types:
            continue
        x, y, w, h = entry.get("x"), entry.get("y"), entry.get("w"), entry.get("h")
        if not all(_is_index(value) for value in (x, y, w, h)) or w < 1 or h < 1:
            continue
        seen.add(i)
        w = min(w, cols)
        cfg = entry.get("cfg")
        kept.append(
            {
                "i": i,
                "type": kind,
                "x": max(0, min(x, cols - w)),
                "y": y,
                "w": w,
                "h": h,
                "cfg": cfg if isinstance(cfg, dict) else {},
            }
        )
        if len(kept) >= MAX_WIDGETS_PER_BOARD:
            break
    kept.sort(key=lambda entry: (entry["y"], entry["x"]))
    placed: list[dict[str, Any]] = []
    for entry in kept:
        while any(overlaps(other, entry) for other in placed):
            entry["y"] += 1
        placed.append(entry)
    return placed
