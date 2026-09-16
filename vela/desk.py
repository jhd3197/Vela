"""The desk's saved boards (`<data_dir>/desk.json`).

Two boards live here: `desktop` (six columns) and `phone` (two). They are
edited and stored separately and are never reflowed into one another — an
arrangement the user chose for a 1440px screen is not the arrangement they want
in their hand.

The validate / seed / repair-on-read shape follows ServerKit's
`dashboard_service.py` (MIT, same owner). The geometry rules are the same ones
`web/src/desk/grid/layout.js` implements in the browser: a widget is a
rectangle on a fixed column grid, it fits inside the board, and no two overlap.
The server is authoritative — a board that reaches this file by any other route
than the dashboard still has to be renderable.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

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
)


class DeskError(Exception):
    """A board that cannot be stored, with the reason to show the user."""


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
    would not draw again is refused rather than stored. `_repair` is the read
    path, which is allowed to be lenient because the alternative there is a
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


def _repair(widgets: Any, cols: int, known_types: set[str]) -> list[dict[str, Any]]:
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


class DeskStore:
    """desk.json: the two boards plus a revision for optimistic concurrency."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Replace is atomic on both platforms Vela ships for, so a crash
        # mid-write leaves the previous board rather than half of the new one.
        tmp.replace(self._path)

    def load(self, known_types: set[str]) -> dict[str, Any]:
        """The stored boards, repaired, seeding the defaults when absent."""
        stored = self._read()
        if not stored:
            return {"revision": 0, "boards": default_boards()}
        revision = stored.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            revision = 0
        boards: dict[str, Any] = {"version": BOARD_VERSION}
        raw = stored.get("boards")
        raw = raw if isinstance(raw, dict) else {}
        seeded = default_boards()
        for key, cols in BOARD_COLS.items():
            board = raw.get(key)
            if not isinstance(board, dict) or not isinstance(board.get("widgets"), list):
                # A board that is missing entirely is seeded; one that is merely
                # damaged is repaired. Only the absent case gets defaults back,
                # so "I removed every widget" stays removed.
                boards[key] = seeded[key]
                continue
            boards[key] = {
                "cols": cols,
                "widgets": _repair(board["widgets"], cols, known_types),
            }
        return {"revision": revision, "boards": boards}

    def save(self, boards: Any, revision: Any, known_types: set[str]) -> dict[str, Any]:
        """Validate and store both boards. Raises DeskError, or ValueError on a
        stale revision so the API can answer 409 rather than 422."""
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise DeskError("revision must be a whole number")
        if not isinstance(boards, dict):
            raise DeskError("boards must be an object")
        checked: dict[str, Any] = {"version": BOARD_VERSION}
        for key, cols in BOARD_COLS.items():
            board = boards.get(key)
            if not isinstance(board, dict):
                raise DeskError(f"the {key} board is missing")
            checked[key] = {
                "cols": cols,
                "widgets": validate_widgets(board.get("widgets"), cols, known_types),
            }
        with self._lock:
            current = self._read()
            stored_revision = current.get("revision")
            if not isinstance(stored_revision, int) or isinstance(stored_revision, bool):
                stored_revision = 0
            if revision != stored_revision:
                raise ValueError(stored_revision)
            payload = {"revision": stored_revision + 1, "boards": checked}
            self._write(payload)
        return payload
