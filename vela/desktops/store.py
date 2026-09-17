"""Durable desktops: their names, boards, appearance and wallpaper assets.

Its own SQLite database beside the app data, following the same conventions as
`vela/automations/store.py`. Nothing an installed app owns lives here: deleting
a desktop removes a workspace, never someone's notes.

Four revisions, not one. Metadata, each board and appearance are edited by
different parts of the dashboard at different moments, and a single counter
would make renaming a desktop conflict with dragging a widget. Every write
checks the revision it was given and refuses rather than overwriting an edit it
never saw.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    MAX_DESKTOPS,
    MAX_VIEWS_PER_DESKTOP,
    SCHEMA_VERSION,
    DesktopConflict,
    DesktopError,
    default_appearance,
)
from .policy import empty_policy

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS desktops (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  agent_profile_id TEXT,
  position INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  boards_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS desktops_by_position ON desktops (position);
-- One revision covers both boards, not one each: `desktop` and `phone` are two
-- responsive layouts of the same desk, saved together, exactly as `/api/desk`
-- has always done it. Splitting them would give the compatibility route two
-- numbers where it can only send one.
CREATE TABLE IF NOT EXISTS desktop_boards (
  desktop_id TEXT NOT NULL REFERENCES desktops (id) ON DELETE CASCADE,
  board TEXT NOT NULL,
  version INTEGER NOT NULL,
  widgets TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (desktop_id, board)
);
CREATE TABLE IF NOT EXISTS desktop_appearance (
  desktop_id TEXT PRIMARY KEY REFERENCES desktops (id) ON DELETE CASCADE,
  wallpaper TEXT NOT NULL,
  wallpaper_asset TEXT,
  dim INTEGER NOT NULL,
  labels INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
-- What is open on a desktop. A view is the identity that survives being
-- minimized, maximized, moved between panes and restored; the app process it
-- talks to has its own, shorter, life. `installation_id` is what makes a
-- reinstalled app a different app as far as an open view is concerned.
CREATE TABLE IF NOT EXISTS desktop_views (
  id TEXT PRIMARY KEY,
  desktop_id TEXT NOT NULL REFERENCES desktops (id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  app_id TEXT,
  installation_id TEXT,
  surface_key TEXT,
  url TEXT,
  title TEXT NOT NULL DEFAULT '',
  opened_by TEXT NOT NULL,
  position INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS views_by_desktop ON desktop_views (desktop_id, position);
-- Where a window is, kept apart from what it is. Minimizing changes only this
-- table, which is the whole point: it is presentation, not lifetime.
CREATE TABLE IF NOT EXISTS desktop_view_presentation (
  view_id TEXT PRIMARY KEY REFERENCES desktop_views (id) ON DELETE CASCADE,
  bounds TEXT,
  restore_bounds TEXT,
  minimized INTEGER NOT NULL DEFAULT 0,
  stack INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS desktop_layout (
  desktop_id TEXT PRIMARY KEY REFERENCES desktops (id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  arrangement TEXT NOT NULL,
  maximized_view TEXT,
  primary_view TEXT,
  secondary_view TEXT,
  divider_ratio REAL NOT NULL DEFAULT 0.5,
  selected_view TEXT,
  updated_at TEXT NOT NULL
);
-- What an agent desktop is allowed to touch. Configuration, so it lives with
-- the desktop; the grants issued against it live beside the app data they
-- authorize changes to, where they can be checked inside the same transaction.
CREATE TABLE IF NOT EXISTS desktop_policy (
  desktop_id TEXT PRIMARY KEY REFERENCES desktops (id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  document TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS desktop_assets (
  digest TEXT PRIMARY KEY,
  media_type TEXT NOT NULL,
  extension TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


class DesktopStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)
            stored = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if stored is None:
                db.execute(
                    "INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(stored["value"]) > SCHEMA_VERSION:
                # A newer Vela wrote this file. Reading it with older rules would
                # quietly drop whatever the newer schema added.
                raise DesktopError(
                    500,
                    "This server's desktop data was written by a newer version of Vela. "
                    "Restore the backup taken before that upgrade.",
                )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    # ------------------------------------------------------------- meta --

    def marker(self, key: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # --------------------------------------------------------- desktops --

    def create(
        self,
        name: str,
        *,
        kind: str = "personal",
        boards: dict[str, Any] | None = None,
        appearance: dict[str, Any] | None = None,
        asset: dict[str, Any] | None = None,
        desktop_id: str | None = None,
        marker: tuple[str, str] | None = None,
    ) -> str:
        """Add a desktop with its boards, appearance and wallpaper in one transaction.

        `marker` records a `meta` row in the same transaction and refuses if it
        already exists. That is what makes the desk migration run exactly once:
        either the desktop and the marker both land, or neither does.
        """
        desktop_id = desktop_id or new_id()
        stamp = now()
        look = {**default_appearance(), **(appearance or {})}
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if marker is not None:
                existing = db.execute(
                    "SELECT 1 FROM meta WHERE key=?", (marker[0],)
                ).fetchone()
                if existing:
                    raise DesktopError(409, "That migration has already run.")
                db.execute("INSERT INTO meta VALUES (?,?)", marker)
            count = db.execute("SELECT count(*) AS n FROM desktops").fetchone()["n"]
            if count >= MAX_DESKTOPS:
                raise DesktopError(
                    429, f"Vela keeps at most {MAX_DESKTOPS} desktops. Delete one first."
                )
            position = (
                db.execute("SELECT coalesce(max(position), -1) AS p FROM desktops").fetchone()["p"]
                + 1
            )
            db.execute(
                "INSERT INTO desktops VALUES (?,?,?,?,?,?,?,?,?)",
                (desktop_id, name, kind, None, position, 1, 0, stamp, stamp),
            )
            for board, widgets in (boards or {}).items():
                db.execute(
                    "INSERT INTO desktop_boards VALUES (?,?,?,?,?)",
                    (desktop_id, board, SCHEMA_VERSION, _dump(widgets), stamp),
                )
            if asset:
                db.execute(
                    "INSERT INTO desktop_assets VALUES (?,?,?,?,?) "
                    "ON CONFLICT (digest) DO NOTHING",
                    (asset["digest"], asset["mediaType"], asset["extension"], asset["bytes"], stamp),
                )
            db.execute(
                "INSERT INTO desktop_appearance VALUES (?,?,?,?,?,?,?)",
                (
                    desktop_id,
                    look["wallpaper"],
                    asset["digest"] if asset else None,
                    int(look["dim"]),
                    int(look["labels"]),
                    0,
                    stamp,
                ),
            )
        return desktop_id

    def list(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM desktops ORDER BY position, created_at").fetchall()
        return [_desktop(row) for row in rows]

    def get(self, desktop_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM desktops WHERE id=?", (desktop_id,)).fetchone()
        if not row:
            raise DesktopError(404, "That desktop no longer exists.")
        return _desktop(row)

    def default_id(self) -> str | None:
        """The first desktop, which is the one the migrated desk became."""
        with self.connection() as db:
            row = db.execute(
                "SELECT id FROM desktops ORDER BY position, created_at LIMIT 1"
            ).fetchone()
        return row["id"] if row else None

    def rename(self, desktop_id: str, name: str, expected_revision: int) -> dict[str, Any]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM desktops WHERE id=?", (desktop_id,)).fetchone()
            if not row:
                raise DesktopError(404, "That desktop no longer exists.")
            if row["revision"] != expected_revision:
                raise DesktopConflict(
                    "This desktop changed somewhere else.", int(row["revision"])
                )
            revision = int(row["revision"]) + 1
            db.execute(
                "UPDATE desktops SET name=?, revision=?, updated_at=? WHERE id=?",
                (name, revision, now(), desktop_id),
            )
            updated = db.execute("SELECT * FROM desktops WHERE id=?", (desktop_id,)).fetchone()
        return _desktop(updated)

    def delete(self, desktop_id: str) -> None:
        """Remove a desktop and everything desktop-local about it.

        The cascade covers boards and appearance. Asset files are reconciled
        separately by the service, after the transaction: a file left behind is
        recoverable, a row pointing at a file that is already gone is not.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) AS n FROM desktops").fetchone()["n"]
            if count <= 1:
                raise DesktopError(
                    409, "This is the only desktop. Create another one before deleting it."
                )
            removed = db.execute("DELETE FROM desktops WHERE id=?", (desktop_id,)).rowcount
        if not removed:
            raise DesktopError(404, "That desktop no longer exists.")

    def set_kind(self, desktop_id: str, kind: str) -> dict[str, Any]:
        """Change a desktop between personal and agent.

        Its own revision moves, because the kind is metadata somebody may be
        looking at; its boards, appearance and windows are untouched, because
        turning an agent on is not throwing the workspace away.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM desktops WHERE id=?", (desktop_id,)).fetchone()
            if not row:
                raise DesktopError(404, "That desktop no longer exists.")
            db.execute(
                "UPDATE desktops SET kind=?, revision=?, updated_at=? WHERE id=?",
                (kind, int(row["revision"]) + 1, now(), desktop_id),
            )
        return self.get(desktop_id)

    # ----------------------------------------------------------- boards --

    def boards(self, desktop_id: str) -> dict[str, Any]:
        """One desktop's boards: `{"revision": int, "widgets": {board: [...]}}`."""
        with self.connection() as db:
            desktop = db.execute(
                "SELECT boards_revision FROM desktops WHERE id=?", (desktop_id,)
            ).fetchone()
            if not desktop:
                raise DesktopError(404, "That desktop no longer exists.")
            rows = db.execute(
                "SELECT * FROM desktop_boards WHERE desktop_id=?", (desktop_id,)
            ).fetchall()
        return {
            "revision": int(desktop["boards_revision"]),
            "widgets": {row["board"]: json.loads(row["widgets"]) for row in rows},
        }

    def save_boards(
        self, desktop_id: str, widgets: dict[str, list[dict[str, Any]]], expected_revision: int
    ) -> dict[str, Any]:
        """Store both boards together, or refuse if someone saved in between."""
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            desktop = db.execute(
                "SELECT boards_revision FROM desktops WHERE id=?", (desktop_id,)
            ).fetchone()
            if not desktop:
                raise DesktopError(404, "That desktop no longer exists.")
            current = int(desktop["boards_revision"])
            if expected_revision != current:
                raise DesktopConflict("The desk changed somewhere else.", current)
            revision = current + 1
            for board, entries in widgets.items():
                db.execute(
                    "INSERT INTO desktop_boards VALUES (?,?,?,?,?) "
                    "ON CONFLICT (desktop_id, board) DO UPDATE SET "
                    "version=excluded.version, widgets=excluded.widgets, "
                    "updated_at=excluded.updated_at",
                    (desktop_id, board, SCHEMA_VERSION, _dump(entries), stamp),
                )
            db.execute(
                "UPDATE desktops SET boards_revision=?, updated_at=? WHERE id=?",
                (revision, stamp, desktop_id),
            )
        return {"revision": revision, "widgets": widgets}

    # ------------------------------------------------------- appearance --

    def appearance(self, desktop_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM desktop_appearance WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
        if not row:
            raise DesktopError(404, "That desktop no longer exists.")
        return {
            "wallpaper": row["wallpaper"],
            "wallpaperAsset": row["wallpaper_asset"],
            "dim": bool(row["dim"]),
            "labels": bool(row["labels"]),
            "revision": int(row["revision"]),
        }

    def save_appearance(
        self,
        desktop_id: str,
        patch: dict[str, Any],
        expected_revision: int | None = None,
        *,
        wallpaper_asset: str | None = None,
        clear_asset: bool = False,
    ) -> dict[str, Any]:
        """Apply an appearance patch, keeping the fields it does not mention.

        `expected_revision` is optional because the compatibility settings route
        has no revision to offer; a scoped API call supplies one and gets the
        conflict check.
        """
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM desktop_appearance WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
            if not row:
                raise DesktopError(404, "That desktop no longer exists.")
            current = int(row["revision"])
            if expected_revision is not None and expected_revision != current:
                raise DesktopConflict("This desktop changed somewhere else.", current)
            wallpaper = patch.get("wallpaper", row["wallpaper"])
            dim = patch.get("dim", bool(row["dim"]))
            labels = patch.get("labels", bool(row["labels"]))
            asset = row["wallpaper_asset"]
            if clear_asset:
                asset = None
            elif wallpaper_asset is not None:
                asset = wallpaper_asset
            db.execute(
                "UPDATE desktop_appearance SET wallpaper=?, wallpaper_asset=?, dim=?, labels=?, "
                "revision=?, updated_at=? WHERE desktop_id=?",
                (wallpaper, asset, int(dim), int(labels), current + 1, stamp, desktop_id),
            )
        return {
            "wallpaper": wallpaper,
            "wallpaperAsset": asset,
            "dim": bool(dim),
            "labels": bool(labels),
            "revision": current + 1,
        }

    # ------------------------------------------------------------ views --

    def views(self, desktop_id: str) -> list[dict[str, Any]]:
        """What is open on this desktop, with where each window sits."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT v.*, p.bounds, p.restore_bounds, p.minimized, p.stack "
                "FROM desktop_views v LEFT JOIN desktop_view_presentation p ON p.view_id = v.id "
                "WHERE v.desktop_id=? ORDER BY v.position",
                (desktop_id,),
            ).fetchall()
        return [_view(row) for row in rows]

    def view(self, view_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT v.*, p.bounds, p.restore_bounds, p.minimized, p.stack "
                "FROM desktop_views v LEFT JOIN desktop_view_presentation p ON p.view_id = v.id "
                "WHERE v.id=?",
                (view_id,),
            ).fetchone()
        if not row:
            raise DesktopError(404, "That view is no longer open.")
        return _view(row)

    def open_view(
        self,
        desktop_id: str,
        *,
        kind: str,
        target: dict[str, Any],
        installation_id: str | None = None,
        title: str = "",
        opened_by: str = "human",
        state: dict[str, Any] | None = None,
        bounds: dict[str, int] | None = None,
    ) -> str:
        """Add a view and select it, in one transaction with the layout."""
        view_id = new_id()
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            count = db.execute(
                "SELECT count(*) AS n FROM desktop_views WHERE desktop_id=?", (desktop_id,)
            ).fetchone()["n"]
            if count >= MAX_VIEWS_PER_DESKTOP:
                raise DesktopError(
                    429,
                    f"A desktop holds {MAX_VIEWS_PER_DESKTOP} open views. Close one first.",
                )
            position = (
                db.execute(
                    "SELECT coalesce(max(position), -1) AS p FROM desktop_views WHERE desktop_id=?",
                    (desktop_id,),
                ).fetchone()["p"]
                + 1
            )
            stack = (
                db.execute(
                    "SELECT coalesce(max(p.stack), -1) AS s FROM desktop_view_presentation p "
                    "JOIN desktop_views v ON v.id = p.view_id WHERE v.desktop_id=?",
                    (desktop_id,),
                ).fetchone()["s"]
                + 1
            )
            db.execute(
                "INSERT INTO desktop_views VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    view_id,
                    desktop_id,
                    kind,
                    target.get("app_id"),
                    installation_id,
                    target.get("surface_key"),
                    target.get("url"),
                    title,
                    opened_by,
                    position,
                    _dump(state or {}),
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO desktop_view_presentation VALUES (?,?,?,?,?)",
                (view_id, _dump(bounds) if bounds else None, None, 0, stack),
            )
            # Opening something is asking to look at it, so it becomes the
            # selected view in the same transaction rather than in a second one
            # that could fail on its own.
            _touch_layout(db, desktop_id, stamp, selected_view=view_id)
        return view_id

    def update_view(
        self,
        view_id: str,
        *,
        title: str | None = None,
        state: dict[str, Any] | None = None,
        bounds: dict[str, int] | None = None,
        restore_bounds: dict[str, int] | None = None,
        minimized: bool | None = None,
        raise_to_front: bool = False,
    ) -> dict[str, Any]:
        """Change what a view remembers, or where its window sits.

        Presentation and identity are written together here only because they
        arrive in one request. Nothing about minimizing touches the view's
        lifetime, which is the distinction this table split exists for.
        """
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM desktop_views WHERE id=?", (view_id,)).fetchone()
            if not row:
                raise DesktopError(404, "That view is no longer open.")
            if title is not None or state is not None:
                db.execute(
                    "UPDATE desktop_views SET title=coalesce(?, title), "
                    "state=coalesce(?, state), updated_at=? WHERE id=?",
                    (title, _dump(state) if state is not None else None, stamp, view_id),
                )
            current = db.execute(
                "SELECT * FROM desktop_view_presentation WHERE view_id=?", (view_id,)
            ).fetchone()
            stack = int(current["stack"]) if current else 0
            if raise_to_front:
                stack = (
                    db.execute(
                        "SELECT coalesce(max(p.stack), -1) AS s FROM desktop_view_presentation p "
                        "JOIN desktop_views v ON v.id = p.view_id WHERE v.desktop_id=?",
                        (row["desktop_id"],),
                    ).fetchone()["s"]
                    + 1
                )
            db.execute(
                "INSERT INTO desktop_view_presentation VALUES (?,?,?,?,?) "
                "ON CONFLICT (view_id) DO UPDATE SET bounds=excluded.bounds, "
                "restore_bounds=excluded.restore_bounds, minimized=excluded.minimized, "
                "stack=excluded.stack",
                (
                    view_id,
                    _dump(bounds)
                    if bounds is not None
                    else (current["bounds"] if current else None),
                    _dump(restore_bounds)
                    if restore_bounds is not None
                    else (current["restore_bounds"] if current else None),
                    int(minimized)
                    if minimized is not None
                    else (int(current["minimized"]) if current else 0),
                    stack,
                ),
            )
        return self.view(view_id)

    def close_view(self, view_id: str) -> str:
        """Remove a view and take it out of any layout that named it."""
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM desktop_views WHERE id=?", (view_id,)).fetchone()
            if not row:
                raise DesktopError(404, "That view is no longer open.")
            desktop_id = row["desktop_id"]
            db.execute("DELETE FROM desktop_views WHERE id=?", (view_id,))
            _detach_from_layout(db, desktop_id, view_id, stamp)
        return desktop_id

    def select_view(self, desktop_id: str, view_id: str | None) -> dict[str, Any]:
        """Move the desktop's attention without touching the layout revision.

        Clicking a window happens constantly. Charging it against the layout
        revision would make every click conflict with a drag somebody else was
        finishing.
        """
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            _touch_layout(db, desktop_id, stamp, selected_view=view_id)
            if view_id is None:
                db.execute(
                    "UPDATE desktop_layout SET selected_view=NULL, updated_at=? WHERE desktop_id=?",
                    (stamp, desktop_id),
                )
        return self.layout(desktop_id)

    # ----------------------------------------------------------- layout --

    def layout(self, desktop_id: str) -> dict[str, Any]:
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            row = db.execute(
                "SELECT * FROM desktop_layout WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
        return _layout(row)

    def save_layout(
        self, desktop_id: str, patch: dict[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        """Store one coherent arrangement, or refuse if someone saved first."""
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            row = db.execute(
                "SELECT * FROM desktop_layout WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision != current:
                raise DesktopConflict("This desktop's layout changed somewhere else.", current)
            merged = {**_layout(row), **patch}
            # Every pane has to name a view that is actually open on this
            # desktop. A layout pointing at a closed view is a blank pane the
            # user can neither fill nor dismiss.
            open_ids = {
                item["id"]
                for item in db.execute(
                    "SELECT id FROM desktop_views WHERE desktop_id=?", (desktop_id,)
                ).fetchall()
            }
            for key in ("maximizedView", "primaryView", "secondaryView", "selectedView"):
                if merged.get(key) is not None and merged[key] not in open_ids:
                    raise DesktopError(422, "That layout names a view that is not open.")
            if merged.get("primaryView") is not None and merged.get("primaryView") == merged.get(
                "secondaryView"
            ):
                raise DesktopError(422, "A split cannot show the same view on both sides.")
            db.execute(
                "INSERT INTO desktop_layout VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (desktop_id) DO UPDATE SET version=excluded.version, "
                "revision=excluded.revision, arrangement=excluded.arrangement, "
                "maximized_view=excluded.maximized_view, primary_view=excluded.primary_view, "
                "secondary_view=excluded.secondary_view, divider_ratio=excluded.divider_ratio, "
                "selected_view=excluded.selected_view, updated_at=excluded.updated_at",
                (
                    desktop_id,
                    SCHEMA_VERSION,
                    current + 1,
                    merged["arrangement"],
                    merged.get("maximizedView"),
                    merged.get("primaryView"),
                    merged.get("secondaryView"),
                    merged["dividerRatio"],
                    merged.get("selectedView"),
                    stamp,
                ),
            )
            saved = db.execute(
                "SELECT * FROM desktop_layout WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
        return _layout(saved)

    # ----------------------------------------------------------- policy --

    def policy(self, desktop_id: str) -> dict[str, Any]:
        """What this desktop allows. A desktop nobody configured allows nothing."""
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            row = db.execute(
                "SELECT * FROM desktop_policy WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
        if row is None:
            return empty_policy()
        return {**json.loads(row["document"]), "revision": int(row["revision"])}

    def save_policy(
        self, desktop_id: str, document: dict[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM desktops WHERE id=?", (desktop_id,)).fetchone():
                raise DesktopError(404, "That desktop no longer exists.")
            row = db.execute(
                "SELECT revision FROM desktop_policy WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision != current:
                raise DesktopConflict("This desktop's permissions changed somewhere else.", current)
            stored = {key: value for key, value in document.items() if key != "revision"}
            db.execute(
                "INSERT INTO desktop_policy VALUES (?,?,?,?) "
                "ON CONFLICT (desktop_id) DO UPDATE SET revision=excluded.revision, "
                "document=excluded.document, updated_at=excluded.updated_at",
                (desktop_id, current + 1, _dump(stored), stamp),
            )
        return {**stored, "revision": current + 1}

    # ----------------------------------------------------------- assets --

    def record_asset(self, digest: str, media_type: str, extension: str, size: int) -> None:
        """Note that an image with this content exists, once per digest.

        Two desktops choosing the same picture share one file. That is the whole
        reason assets are addressed by content: changing one desktop's wallpaper
        must not delete the image another desktop is still drawing.
        """
        with self.connection() as db:
            db.execute(
                "INSERT INTO desktop_assets VALUES (?,?,?,?,?) ON CONFLICT (digest) DO NOTHING",
                (digest, media_type, extension, size, now()),
            )

    def asset(self, digest: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM desktop_assets WHERE digest=?", (digest,)).fetchone()
        return dict(row) if row else None

    def unreferenced_assets(self) -> list[dict[str, Any]]:
        """Assets no desktop points at any more, for the service to clean up."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM desktop_assets WHERE digest NOT IN "
                "(SELECT wallpaper_asset FROM desktop_appearance WHERE wallpaper_asset IS NOT NULL)"
            ).fetchall()
        return [dict(row) for row in rows]

    def forget_asset(self, digest: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM desktop_assets WHERE digest=?", (digest,))

    def referenced_assets(self) -> set[str]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT DISTINCT wallpaper_asset AS digest FROM desktop_appearance "
                "WHERE wallpaper_asset IS NOT NULL"
            ).fetchall()
        return {row["digest"] for row in rows}


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _desktop(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "agentProfileId": row["agent_profile_id"],
        "position": int(row["position"]),
        "revision": int(row["revision"]),
        "boardsRevision": int(row["boards_revision"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _touch_layout(db, desktop_id: str, stamp: str, *, selected_view: str | None = None) -> None:
    """Make sure a layout row exists, optionally moving the selection.

    The revision is deliberately *not* bumped: opening a window is not the user
    saving an arrangement, and treating it as one would make every launch
    conflict with a drag somebody else was finishing.
    """
    row = db.execute("SELECT * FROM desktop_layout WHERE desktop_id=?", (desktop_id,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO desktop_layout VALUES (?,?,?,?,?,?,?,?,?,?)",
            (desktop_id, SCHEMA_VERSION, 0, "floating", None, None, None, 0.5, selected_view, stamp),
        )
        return
    if selected_view is not None:
        db.execute(
            "UPDATE desktop_layout SET selected_view=?, updated_at=? WHERE desktop_id=?",
            (selected_view, stamp, desktop_id),
        )


def _detach_from_layout(db, desktop_id: str, view_id: str, stamp: str) -> None:
    """Take a closed view out of the arrangement without disturbing the rest.

    A closed pane member leaves an explicit empty slot rather than collapsing the
    split: the user asked for two panes, and closing what was in one of them is
    not a request to undo that.
    """
    row = db.execute("SELECT * FROM desktop_layout WHERE desktop_id=?", (desktop_id,)).fetchone()
    if row is None:
        return
    arrangement = row["arrangement"]
    maximized = None if row["maximized_view"] == view_id else row["maximized_view"]
    if arrangement == "maximized" and maximized is None:
        arrangement = "floating"
    primary = None if row["primary_view"] == view_id else row["primary_view"]
    secondary = None if row["secondary_view"] == view_id else row["secondary_view"]
    selected = row["selected_view"]
    if selected == view_id:
        remaining = db.execute(
            "SELECT v.id FROM desktop_views v "
            "LEFT JOIN desktop_view_presentation p ON p.view_id = v.id "
            "WHERE v.desktop_id=? ORDER BY p.stack DESC LIMIT 1",
            (desktop_id,),
        ).fetchone()
        selected = remaining["id"] if remaining else None
    db.execute(
        "UPDATE desktop_layout SET arrangement=?, maximized_view=?, primary_view=?, "
        "secondary_view=?, selected_view=?, updated_at=? WHERE desktop_id=?",
        (arrangement, maximized, primary, secondary, selected, stamp, desktop_id),
    )


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "desktopId": row["desktop_id"],
        "kind": row["kind"],
        "appId": row["app_id"],
        "installationId": row["installation_id"],
        "surface": row["surface_key"],
        "url": row["url"],
        "title": row["title"],
        "openedBy": row["opened_by"],
        "position": int(row["position"]),
        "state": _loads(row["state"]) or {},
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "window": {
            "bounds": _loads(row["bounds"]),
            "restoreBounds": _loads(row["restore_bounds"]),
            "minimized": bool(row["minimized"]),
            "stack": int(row["stack"] or 0),
        },
    }


def _layout(row) -> dict[str, Any]:
    """A desktop with no saved arrangement shows one floating view."""
    if row is None:
        return {
            "revision": 0,
            "arrangement": "floating",
            "maximizedView": None,
            "primaryView": None,
            "secondaryView": None,
            "dividerRatio": 0.5,
            "selectedView": None,
        }
    return {
        "revision": int(row["revision"]),
        "arrangement": row["arrangement"],
        "maximizedView": row["maximized_view"],
        "primaryView": row["primary_view"],
        "secondaryView": row["secondary_view"],
        "dividerRatio": float(row["divider_ratio"]),
        "selectedView": row["selected_view"],
    }
