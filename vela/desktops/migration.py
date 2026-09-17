"""Turning the existing desk into Desktop 1.

The desk someone arranged is not test data. This runs once, on the way up,
before anything reads a desktop, and it has to be lossless: both boards, every
widget id and position, the wallpaper they chose and the image they uploaded.

`desk.json` and the wallpaper file are left exactly where they are. They are the
only copy of an arrangement that predates this feature, and keeping them costs a
few kilobytes against the one case where something here turns out to be wrong.

Crash safety is the reason the order is what it is. The wallpaper is copied and
verified first, outside any transaction, into a file named after its own
content. Then one transaction writes the desktop, its boards, its appearance and
the marker together. A crash before the commit leaves an orphan file that the
next run either reuses — same content, same name — or sweeps; it never leaves
two Desktop 1s.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..desk import BOARD_COLS, default_boards
from .models import DEFAULT_WALLPAPER, RETIRED_WALLPAPERS, WALLPAPER_REFERENCE
from .store import DesktopStore, now

#: The `meta` key that says the desk has been imported. Its presence is the
#: whole test; it is never removed.
MARKER = "desk_migrated"

#: Widgets copied per board. The same bound `vela/desk.py` enforces, applied
#: here so a hand-edited `desk.json` cannot make one desktop unbounded.
MAX_WIDGETS_PER_BOARD = 40


def migrate(
    store: DesktopStore,
    *,
    desk_path: Path,
    settings: Any,
    wallpaper_path: Path | None,
    assets_dir: Path,
    name: str = "Desktop 1",
) -> dict[str, Any]:
    """Import the desk if it has not been imported, and report what happened.

    Safe to call on every start. Returns `{"migrated": bool, "desktopId": str,
    "notes": [...]}` — the notes say what could not be carried over, so a repair
    is visible rather than silent.
    """
    if store.marker(MARKER):
        return {"migrated": False, "desktopId": store.default_id(), "notes": []}

    notes: list[str] = []
    boards, board_notes = _read_boards(desk_path)
    notes.extend(board_notes)

    desk_settings = settings.get("desk") if settings is not None else None
    appearance, appearance_notes = _read_appearance(desk_settings)
    notes.extend(appearance_notes)

    asset = None
    if appearance["wallpaper"] == "custom":
        asset, asset_notes = _import_wallpaper(wallpaper_path, assets_dir)
        notes.extend(asset_notes)
        if asset is None:
            # The choice said "the picture I uploaded" and the picture is gone.
            # Falling back is better than a desktop that draws nothing.
            appearance["wallpaper"] = DEFAULT_WALLPAPER

    try:
        desktop_id = store.create(
            name,
            boards=boards,
            appearance=appearance,
            asset=asset,
            marker=(MARKER, now()),
        )
    except Exception:
        # Another process committed the marker between the check and here. Its
        # Desktop 1 is as good as this one would have been.
        if store.marker(MARKER):
            return {"migrated": False, "desktopId": store.default_id(), "notes": []}
        raise
    return {"migrated": True, "desktopId": desktop_id, "notes": notes}


def _read_boards(desk_path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Both boards out of `desk.json`, or the seeded defaults if there is none.

    Widgets are copied with their ids and geometry intact and are *not* filtered
    by the current widget types. A widget whose app happens to be stopped during
    startup is still the user's widget; `vela/desk.py` already drops unknown
    types on read, which is where that decision belongs.
    """
    notes: list[str] = []
    try:
        stored = json.loads(desk_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {key: board["widgets"] for key, board in _defaults().items()}, [
            "No saved desk was found, so Desktop 1 starts with the default widgets."
        ]
    except (OSError, json.JSONDecodeError):
        return {key: board["widgets"] for key, board in _defaults().items()}, [
            "The saved desk could not be read, so Desktop 1 starts with the default widgets. "
            "The original file was left in place."
        ]

    raw = stored.get("boards") if isinstance(stored, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    seeded = _defaults()
    boards: dict[str, list[dict[str, Any]]] = {}
    for key, cols in BOARD_COLS.items():
        board = raw.get(key)
        if not isinstance(board, dict) or not isinstance(board.get("widgets"), list):
            boards[key] = seeded[key]["widgets"]
            if raw:
                notes.append(f"The {key} board was missing, so its default widgets were used.")
            continue
        widgets, dropped = _copy_widgets(board["widgets"], cols)
        boards[key] = widgets
        if dropped:
            notes.append(
                f"{dropped} entr{'y' if dropped == 1 else 'ies'} on the {key} board "
                "could not be read and were left out."
            )
    return boards, notes


def _copy_widgets(widgets: list[Any], cols: int) -> tuple[list[dict[str, Any]], int]:
    """Copy what is recognisably a widget, counting what is not."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for entry in widgets:
        if len(kept) >= MAX_WIDGETS_PER_BOARD:
            dropped += 1
            continue
        if not isinstance(entry, dict):
            dropped += 1
            continue
        i, kind = entry.get("i"), entry.get("type")
        if not isinstance(i, str) or not i or i in seen or not isinstance(kind, str) or not kind:
            dropped += 1
            continue
        numbers = [entry.get(key) for key in ("x", "y", "w", "h")]
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in numbers):
            dropped += 1
            continue
        x, y, w, h = numbers
        if w < 1 or h < 1:
            dropped += 1
            continue
        seen.add(i)
        cfg = entry.get("cfg")
        kept.append(
            {
                "i": i,
                "type": kind,
                "x": x,
                "y": y,
                "w": min(w, cols),
                "h": h,
                "cfg": cfg if isinstance(cfg, dict) else {},
            }
        )
    return kept, dropped


def _defaults() -> dict[str, Any]:
    seeded = default_boards()
    return {key: seeded[key] for key in BOARD_COLS}


def _read_appearance(desk_settings: Any) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    look = {"wallpaper": DEFAULT_WALLPAPER, "dim": True, "labels": True}
    if not isinstance(desk_settings, dict):
        return look, notes
    chosen = desk_settings.get("wallpaper")
    chosen = RETIRED_WALLPAPERS.get(chosen, chosen)
    if isinstance(chosen, str) and WALLPAPER_REFERENCE.match(chosen):
        look["wallpaper"] = chosen
    elif chosen is not None:
        notes.append(
            f"The wallpaper {chosen!r} is not a name this server can store, so the default was used."
        )
    for flag in ("dim", "labels"):
        if isinstance(desk_settings.get(flag), bool):
            look[flag] = desk_settings[flag]
    return look, notes


def _import_wallpaper(
    source: Path | None, assets_dir: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    """Copy the uploaded wallpaper into the desktop asset store, by content.

    The copy lands at `<digest><ext>` through a temporary file and `replace`, so
    a reader never sees a half-written image, and a second run with the same
    picture finds the file already there and verified rather than writing it
    twice.
    """
    if source is None or not source.is_file():
        return None, ["The desk was set to a custom wallpaper but no image was stored."]
    try:
        content = source.read_bytes()
    except OSError:
        return None, ["The custom wallpaper could not be read and was not imported."]

    digest = hashlib.sha256(content).hexdigest()
    extension = source.suffix
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = assets_dir / f"{digest}{extension}"
    if not (target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest):
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)

    media_types = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return {
        "digest": digest,
        "extension": extension,
        "mediaType": media_types.get(extension, "application/octet-stream"),
        "bytes": len(content),
    }, []
