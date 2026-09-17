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

import re
from typing import Any

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


class DesktopError(Exception):
    """A desktop operation that cannot proceed, with the reason to show."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class DesktopConflict(DesktopError):
    """Someone else saved first. Carries the revision the caller should reload."""

    def __init__(self, detail: str, revision: int):
        super().__init__(409, detail)
        self.revision = revision


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
