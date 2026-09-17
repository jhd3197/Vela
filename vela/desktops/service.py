"""Desktops as the rest of Vela uses them.

The store knows how to write rows. This knows the rules: that a board is
validated by `vela/desk.py` before it is stored and repaired when it is read,
that a wallpaper file is addressed by its content so two desktops can share one
picture, and that deleting a desktop removes a workspace and never an installed
app's data.

There is one source of truth for the desk. `/api/desk` reads and writes the
first desktop's boards through this service; there is no second store holding a
second copy with its own revision.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ..desk import BOARD_COLS, BOARD_VERSION, DeskError, default_boards, repair_widgets, validate_widgets
from ..wallpaper import MAX_WALLPAPER_BYTES, WALLPAPER_TYPES, WallpaperError
from .migration import migrate
from .models import (
    DEFAULT_WALLPAPER,
    DesktopError,
    default_appearance,
    default_name,
    validate_appearance,
    validate_id,
    validate_kind,
    validate_name,
    validate_revision,
)
from .store import DesktopStore

#: The first bytes each accepted format really starts with, from
#: `vela/wallpaper.py`. A content type is a claim; this is a check.
_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


class Desktops:
    """The desktop service.

    `known_types` is a callable rather than a set because the widget types that
    exist change as apps are installed and removed, and a board is read against
    the types that exist at the moment it is read.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        known_types: Callable[[], set[str]],
        settings: Any = None,
        wallpaper: Any = None,
    ):
        self.data_dir = data_dir
        self.assets_dir = data_dir / "desktop-assets"
        self.store = DesktopStore(data_dir / "desktops.sqlite")
        self._known_types = known_types
        self._settings = settings
        self._wallpaper = wallpaper

    # -------------------------------------------------------- start-up --

    def prepare(self) -> dict[str, Any]:
        """Import the existing desk if needed, and make sure a desktop exists.

        Called once on the way up. A fresh installation has no `desk.json`, and
        gets a Desktop 1 with the same seeded widgets a fresh desk has always
        shown — the migration path and the first-run path produce the same thing.
        """
        result = migrate(
            self.store,
            desk_path=self.data_dir / "desk.json",
            settings=self._settings,
            wallpaper_path=self._wallpaper.path() if self._wallpaper else None,
            assets_dir=self.assets_dir,
        )
        if self.store.default_id() is None:
            # The marker was set but every desktop was later deleted. The store
            # refuses to delete the last one, so this is only reachable after an
            # external edit; recover rather than serve a dashboard with none.
            seeded = default_boards()
            self.store.create(
                "Desktop 1",
                boards={key: seeded[key]["widgets"] for key in BOARD_COLS},
                appearance=default_appearance(),
            )
            result = {**result, "notes": [*result["notes"], "A missing Desktop 1 was recreated."]}
        self.cleanup_assets()
        return result

    def default_id(self) -> str:
        desktop_id = self.store.default_id()
        if desktop_id is None:
            raise DesktopError(503, "Desktops are not ready yet.")
        return desktop_id

    # -------------------------------------------------------- desktops --

    def list(self) -> dict[str, Any]:
        desktops = self.store.list()
        return {"desktops": desktops, "defaultId": desktops[0]["id"] if desktops else None}

    def create(self, name: Any = None, *, kind: str = "personal") -> dict[str, Any]:
        """A new desktop, empty by default.

        A new desktop does not inherit the first one's widgets or wallpaper. It
        is a new workspace, and copying someone's arrangement — including
        whatever personal information those widgets are configured with — is not
        what "create" means.
        """
        existing = [desktop["name"] for desktop in self.store.list()]
        desktop_id = self.store.create(
            validate_name(name) if name is not None else default_name(existing),
            kind=validate_kind(kind),
            boards={key: [] for key in BOARD_COLS},
            appearance=default_appearance(),
        )
        return self.get(desktop_id)

    def get(self, desktop_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        desktop = self.store.get(desktop_id)
        return {
            **desktop,
            "boards": self.boards(desktop_id)["boards"],
            "appearance": self.appearance(desktop_id),
        }

    def rename(self, desktop_id: str, name: Any, expected_revision: Any) -> dict[str, Any]:
        return self.store.rename(
            validate_id(desktop_id),
            validate_name(name),
            validate_revision(expected_revision, what="desktop"),
        )

    def delete(self, desktop_id: str) -> dict[str, Any]:
        """Remove a desktop's own records, then reconcile its files.

        Installed apps and their data are untouched. A desktop is where windows
        and widgets live, not where an app keeps its documents.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        self.store.delete(desktop_id)
        removed = self.cleanup_assets()
        return {"ok": True, "removedAssets": removed}

    # ---------------------------------------------------------- boards --

    def boards(self, desktop_id: str) -> dict[str, Any]:
        """The desk shape the dashboard already knows, repaired on the way out.

        Repair rather than refusal is deliberate and predates desktops: a widget
        whose app was uninstalled is dropped, one hanging off the right edge is
        pulled back. Losing a whole arrangement over one bad entry is worse.
        """
        desktop_id = validate_id(desktop_id)
        stored = self.store.boards(desktop_id)
        known = self._known_types()
        boards: dict[str, Any] = {"version": BOARD_VERSION}
        seeded = default_boards()
        for key, cols in BOARD_COLS.items():
            widgets = stored["widgets"].get(key)
            if widgets is None:
                boards[key] = seeded[key]
                continue
            boards[key] = {"cols": cols, "widgets": repair_widgets(widgets, cols, known)}
        return {"revision": stored["revision"], "boards": boards}

    def save_boards(self, desktop_id: str, boards: Any, revision: Any) -> dict[str, Any]:
        """Validate both boards and store them together.

        Raises `DeskError` for a board that would not draw again and
        `DesktopConflict` when someone saved first, which is what lets the API
        answer 422 and 409 the way `/api/desk` always has.
        """
        desktop_id = validate_id(desktop_id)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise DeskError("revision must be a whole number")
        if not isinstance(boards, dict):
            raise DeskError("boards must be an object")
        known = self._known_types()
        checked: dict[str, list[dict[str, Any]]] = {}
        for key, cols in BOARD_COLS.items():
            board = boards.get(key)
            if not isinstance(board, dict):
                raise DeskError(f"the {key} board is missing")
            checked[key] = validate_widgets(board.get("widgets"), cols, known)
        saved = self.store.save_boards(desktop_id, checked, revision)
        return {
            "revision": saved["revision"],
            "boards": {
                "version": BOARD_VERSION,
                **{
                    key: {"cols": cols, "widgets": checked[key]}
                    for key, cols in BOARD_COLS.items()
                },
            },
        }

    # ------------------------------------------------------ appearance --

    def appearance(self, desktop_id: str) -> dict[str, Any]:
        return self.store.appearance(validate_id(desktop_id))

    def save_appearance(
        self, desktop_id: str, patch: Any, expected_revision: Any = None
    ) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        checked = validate_appearance(patch)
        revision = (
            None
            if expected_revision is None
            else validate_revision(expected_revision, what="appearance")
        )
        return self.store.save_appearance(desktop_id, checked, revision)

    # ---------------------------------------------- wallpaper as assets --

    def extension_for(self, content_type: str) -> str:
        extension = WALLPAPER_TYPES.get((content_type or "").split(";")[0].strip().lower())
        if extension is None:
            raise WallpaperError(415, "A wallpaper must be a JPEG, PNG or WebP image")
        return extension

    def save_wallpaper(self, desktop_id: str, content: bytes, extension: str) -> dict[str, Any]:
        """Store an uploaded image for one desktop and select it.

        The file is named after its own SHA-256, so uploading the same picture
        to two desktops stores it once and changing one of them cannot delete
        the image the other is still drawing.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        if len(content) > MAX_WALLPAPER_BYTES:
            raise WallpaperError(413, "A wallpaper is at most 8 MB")
        if not content:
            raise WallpaperError(422, "The image was empty")
        if not any(content.startswith(signature) for signature in _SIGNATURES[extension]):
            raise WallpaperError(422, "That file is not the image type it claims to be")

        digest = hashlib.sha256(content).hexdigest()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        target = self.assets_dir / f"{digest}{extension}"
        if not target.is_file():
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
        media_type = next(
            (key for key, value in WALLPAPER_TYPES.items() if value == extension),
            "application/octet-stream",
        )
        self.store.record_asset(digest, media_type, extension, len(content))
        self.store.save_appearance(
            desktop_id, {"wallpaper": "custom"}, None, wallpaper_asset=digest
        )
        self.cleanup_assets()
        return {"ok": True, "bytes": len(content), "digest": digest}

    def wallpaper_file(self, desktop_id: str) -> tuple[Path, str]:
        """The image one desktop draws, or a 404 when it has none of its own."""
        desktop_id = validate_id(desktop_id)
        look = self.store.appearance(desktop_id)
        digest = look.get("wallpaperAsset")
        if not digest:
            raise DesktopError(404, "No wallpaper is set")
        asset = self.store.asset(digest)
        path = self.assets_dir / f"{digest}{asset['extension']}" if asset else None
        if path is None or not path.is_file():
            raise DesktopError(404, "No wallpaper is set")
        return path, asset["media_type"]

    def remove_wallpaper(self, desktop_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        look = self.store.appearance(desktop_id)
        had = bool(look.get("wallpaperAsset"))
        patch = {"wallpaper": DEFAULT_WALLPAPER} if look.get("wallpaper") == "custom" else {}
        self.store.save_appearance(desktop_id, patch, None, clear_asset=True)
        removed = self.cleanup_assets()
        return {"ok": True, "removed": had, "removedAssets": removed}

    def cleanup_assets(self) -> int:
        """Delete stored images nothing points at, and rows whose file is gone.

        Runs after any change that can drop a reference, and once at start-up so
        a crash between writing a file and committing the row that referenced it
        does not leave the file behind forever.
        """
        removed = 0
        for asset in self.store.unreferenced_assets():
            path = self.assets_dir / f"{asset['digest']}{asset['extension']}"
            path.unlink(missing_ok=True)
            self.store.forget_asset(asset["digest"])
            removed += 1
        if self.assets_dir.is_dir():
            referenced = self.store.referenced_assets()
            for path in self.assets_dir.iterdir():
                if not path.is_file():
                    continue
                if path.suffix == ".tmp" or path.stem not in referenced:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed
