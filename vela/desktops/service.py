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
from ..app_storage import AppServiceError
from .effects import EFFECTFUL
from .grants import Grants
from .principals import agent_of
from .migration import migrate
from .policy import empty_policy, validate_policy
from .models import (
    AGENT_VIEWABLE_KINDS,
    DEFAULT_WALLPAPER,
    DesktopError,
    default_appearance,
    default_name,
    validate_actor,
    validate_appearance,
    validate_arrangement,
    validate_bounds,
    validate_divider,
    validate_id,
    validate_kind,
    validate_name,
    validate_revision,
    validate_view_id,
    validate_view_kind,
    validate_view_state,
    validate_view_target,
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
        storage: Any = None,
        auth: Any = None,
        registry: Any = None,
    ):
        self.data_dir = data_dir
        self.assets_dir = data_dir / "desktop-assets"
        self.store = DesktopStore(data_dir / "desktops.sqlite")
        self._known_types = known_types
        self._settings = settings
        self._wallpaper = wallpaper
        # App storage knows which installation of an app is the current one. A
        # view binds to that identity when it opens, so a reinstall does not
        # hand the new installation a window the old one had open.
        self._storage = storage
        # Grants live beside the app data they authorize changes to, so a
        # revocation and an effect contend for one write lock instead of racing
        # across two databases.
        self.grants = Grants(storage) if storage is not None else None
        self._auth = auth
        self._registry = registry

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
        # Authority first: after the rows are gone there is nothing left to say
        # which grants belonged to this desktop.
        self.revoke_desktop(desktop_id)
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

    # ---------------------------------------------------------- policy --

    def policy(self, desktop_id: str) -> dict[str, Any]:
        return self.store.policy(validate_id(desktop_id))

    def save_policy(self, desktop_id: str, document: Any, revision: Any) -> dict[str, Any]:
        """Change what this desktop may touch.

        Every grant issued under the previous answer goes, and so does every
        agent session holding it. Narrowing what an agent may do has to take
        effect now rather than when something happens to be re-checked, and the
        only way to mean that is to remove the authority rather than mark it
        stale.
        """
        desktop_id = validate_id(desktop_id)
        checked = validate_policy(document)
        saved = self.store.save_policy(
            desktop_id, checked, validate_revision(revision, what="policy")
        )
        self.revoke_desktop(desktop_id)
        return saved

    # ---------------------------------------------------------- grants --

    def revoke_desktop(self, desktop_id: str) -> dict[str, Any]:
        """Drop every grant and every agent session this desktop holds."""
        removed = self.grants.revoke_desktop(desktop_id) if self.grants else 0
        sessions = self._auth.revoke_agent(desktop_id=desktop_id) if self._auth else 0
        return {"grants": removed, "sessions": sessions}

    def revoke_run(self, desktop_id: str, run_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        removed = self.grants.revoke_run(desktop_id, run_id) if self.grants else 0
        sessions = (
            self._auth.revoke_agent(desktop_id=desktop_id, run_id=run_id) if self._auth else 0
        )
        return {"grants": removed, "sessions": sessions}

    def list_grants(self, desktop_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        return {"grants": self.grants.list(desktop_id) if self.grants else []}

    def grant(self, desktop_id: str, request: Any) -> dict[str, Any]:
        """Allow one effect, bound to the app it names as it is right now.

        The manifest fingerprint and the installation identity go onto the
        grant, so an app that is updated or reinstalled afterwards does not
        inherit a decision made about the version somebody actually read.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        if self.grants is None or self._registry is None:
            raise DesktopError(503, "Grants are not available yet.")
        request = request if isinstance(request, dict) else {}
        effect = request.get("effect")
        if effect not in EFFECTFUL:
            raise DesktopError(422, "That is not something a grant can cover.")
        app_id = request.get("appId")
        policy = self.store.policy(desktop_id)
        if app_id not in (policy.get("apps") or []):
            raise DesktopError(403, "That app is not allowed on this desktop.")
        manifest = self._registry.get(app_id)
        if not manifest or not self._registry.is_installed(app_id):
            raise DesktopError(404, "That app is not installed.")
        installation = self._storage.installation(app_id)
        if installation is None:
            raise DesktopError(404, "That app is not installed.")
        from ..actions import fingerprint

        return self.grants.issue(
            desktop_id=desktop_id,
            effect=effect,
            app_id=app_id,
            installation_id=installation,
            contract=fingerprint(manifest),
            run_id=request.get("runId"),
            request_digest=request.get("requestDigest"),
            scope=request.get("scope") if isinstance(request.get("scope"), dict) else None,
            seconds=int(request.get("seconds") or 3600),
        )

    def revoke_grant(self, desktop_id: str, grant_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        if self.grants is None:
            raise DesktopError(503, "Grants are not available yet.")
        # Scoped to the desktop so one desktop cannot drop another's grant by
        # guessing an id.
        for grant in self.grants.list(desktop_id):
            if grant["id"] == grant_id:
                return {"ok": self.grants.revoke(grant_id)}
        raise DesktopError(404, "That grant no longer exists.")

    # -------------------------------------------------- agent sessions --

    def open_agent_session(self, desktop_id: str, request: Any) -> dict[str, Any]:
        """A short-lived app session bound to one run on one desktop.

        This is how the supervisor gets a token for the app a run is working in.
        It is deliberately less than an ordinary app session: it expires in
        minutes, it names the run it belongs to, and everything it can do is
        decided by this desktop's policy and the grants issued against it.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        if self._auth is None or self._registry is None:
            raise DesktopError(503, "Agent sessions are not available yet.")
        request = request if isinstance(request, dict) else {}
        view = self.store.view(validate_view_id(request.get("viewId")))
        if view["desktopId"] != desktop_id:
            raise DesktopError(404, "That view is not open on this desktop.")
        if view["kind"] != "app":
            raise DesktopError(422, "Only an app view has an app session.")
        described = self._describe(view)
        if not described["available"]:
            raise DesktopError(409, "That window needs reopening before it can be used.")

        policy = self.store.policy(desktop_id)
        if view["appId"] not in (policy.get("apps") or []):
            raise DesktopError(403, "This desktop is not allowed to use that app.")

        run_id = request.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise DesktopError(422, "An agent session belongs to a run.")

        manifest = self._registry.get(view["appId"])
        if not manifest or not self._registry.is_installed(view["appId"]):
            raise DesktopError(404, "That app is not installed.")
        return self._auth.issue_agent(
            manifest,
            view["installationId"],
            {
                "desktopId": desktop_id,
                "runId": run_id,
                "viewId": view["id"],
                "actorId": request.get("actorId") or run_id,
                "policyRevision": policy["revision"],
            },
        )

    def effect_guard(self, session: Any, effect: str, *, request_digest=None, scope=None):
        """What has to be true, inside the transaction, for this effect to land.

        Returns None when a person is asking — the owner using their own
        computer needs no grant from anyone — and otherwise a callable the
        effect's own transaction runs before it writes. That placement is the
        whole point: the answer cannot go stale between being given and being
        used, because giving it and using it are the same transaction.
        """
        principal = agent_of(session)
        if principal is None:
            return None
        if principal.expired:
            raise AppServiceError(401, "This agent session has expired.")
        if effect not in EFFECTFUL:
            return lambda db: None
        if self.grants is None or self._registry is None:
            raise AppServiceError(503, "Grants are not available yet.")

        app_id = session["app_id"]
        policy = self.store.policy(principal.desktop_id)
        if app_id not in (policy.get("apps") or []):
            raise AppServiceError(403, f"This desktop is not allowed to use {app_id}.")

        manifest = self._registry.get(app_id)
        if not manifest or not self._registry.is_installed(app_id):
            raise AppServiceError(404, "App is not installed")
        from ..actions import fingerprint

        binding = {
            "desktop_id": principal.desktop_id,
            "effect": effect,
            "app_id": app_id,
            "installation_id": session["installationId"],
            "contract": fingerprint(manifest),
            "run_id": principal.run_id,
            "request_digest": request_digest,
            "scope": scope,
        }

        def authorize(db):
            self.grants.require(db, **binding)

        return authorize

    # ----------------------------------------------------------- views --

    def views(self, desktop_id: str) -> dict[str, Any]:
        """What is open on this desktop, and how it is arranged.

        Each view carries `available`: an app whose installation has been
        replaced or removed is still the user's open window, and saying so is
        more use than deleting it behind their back.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        views = [self._describe(view) for view in self.store.views(desktop_id)]
        return {"views": views, "layout": self.store.layout(desktop_id)}

    def open_view(
        self,
        desktop_id: str,
        kind: Any,
        target: Any = None,
        *,
        opened_by: str = "human",
        title: str = "",
        state: Any = None,
        bounds: Any = None,
        reuse: bool = True,
    ) -> dict[str, Any]:
        """Open a view, or bring forward the one that is already open.

        `reuse` is the default because opening Notes when Notes is already open
        means "show me Notes", not "give me a second copy of it". A caller that
        really wants another window says so.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        kind = validate_view_kind(kind)
        opened_by = validate_actor(opened_by)
        checked = validate_view_target(kind, target)
        state = validate_view_state(state)
        bounds = validate_bounds(bounds)

        installation = None
        if kind == "app":
            if self._storage is None:
                raise DesktopError(503, "App installations are not available yet.")
            installation = self._storage.installation(checked["app_id"])
            if installation is None:
                raise DesktopError(404, "That app is not installed.")

        if reuse:
            existing = self._match(desktop_id, kind, checked, installation)
            if existing is not None:
                # Bringing it forward is a presentation change, so a minimized
                # window comes back rather than staying hidden behind its icon.
                self.store.update_view(existing["id"], minimized=False, raise_to_front=True)
                self.select_view(desktop_id, existing["id"])
                return self._describe(self.store.view(existing["id"]))

        view_id = self.store.open_view(
            desktop_id,
            kind=kind,
            target=checked,
            installation_id=installation,
            title=str(title or "")[:120],
            opened_by=opened_by,
            state=state,
            bounds=bounds,
        )
        return self._describe(self.store.view(view_id))

    def update_view(self, desktop_id: str, view_id: str, patch: Any) -> dict[str, Any]:
        """Change where a window sits, or what its view remembers.

        Nothing here ends a session. Minimizing is presentation: the app keeps
        running, the bridge stays open and whatever was typed into it is still
        there when it comes back.
        """
        desktop_id = validate_id(desktop_id)
        view_id = validate_view_id(view_id)
        view = self.store.view(view_id)
        if view["desktopId"] != desktop_id:
            raise DesktopError(404, "That view is not open on this desktop.")
        patch = patch if isinstance(patch, dict) else {}
        updated = self.store.update_view(
            view_id,
            title=str(patch["title"])[:120] if "title" in patch else None,
            state=validate_view_state(patch["state"]) if "state" in patch else None,
            bounds=validate_bounds(patch["bounds"]) if "bounds" in patch else None,
            restore_bounds=(
                validate_bounds(patch["restoreBounds"]) if "restoreBounds" in patch else None
            ),
            minimized=bool(patch["minimized"]) if "minimized" in patch else None,
            raise_to_front=bool(patch.get("raise")),
        )
        return self._describe(updated)

    def close_view(self, desktop_id: str, view_id: str) -> dict[str, Any]:
        """Close a view. The app process is not this record's to stop."""
        desktop_id = validate_id(desktop_id)
        view_id = validate_view_id(view_id)
        view = self.store.view(view_id)
        if view["desktopId"] != desktop_id:
            raise DesktopError(404, "That view is not open on this desktop.")
        self.store.close_view(view_id)
        return {"ok": True, "layout": self.store.layout(desktop_id)}

    def select_view(self, desktop_id: str, view_id: str | None) -> dict[str, Any]:
        """Change which view has the desktop's attention.

        Not a layout save: selecting is what happens every time someone clicks a
        window, and charging that against the layout revision would make it
        conflict with a drag somebody else was finishing.
        """
        desktop_id = validate_id(desktop_id)
        if view_id is not None:
            view_id = validate_view_id(view_id)
            view = self.store.view(view_id)
            if view["desktopId"] != desktop_id:
                raise DesktopError(404, "That view is not open on this desktop.")
        return self.store.select_view(desktop_id, view_id)

    def layout(self, desktop_id: str) -> dict[str, Any]:
        return self.store.layout(validate_id(desktop_id))

    def save_layout(self, desktop_id: str, patch: Any, revision: Any) -> dict[str, Any]:
        """Store one coherent arrangement against the revision it was built on."""
        desktop_id = validate_id(desktop_id)
        revision = validate_revision(revision, what="layout")
        patch = patch if isinstance(patch, dict) else {}
        checked: dict[str, Any] = {}
        if "arrangement" in patch:
            checked["arrangement"] = validate_arrangement(patch["arrangement"])
        if "dividerRatio" in patch:
            checked["dividerRatio"] = validate_divider(patch["dividerRatio"])
        for key in ("maximizedView", "primaryView", "secondaryView", "selectedView"):
            if key in patch:
                value = patch[key]
                checked[key] = None if value is None else validate_view_id(value)
        return self.store.save_layout(desktop_id, checked, revision)

    # ---- helpers

    def _match(
        self, desktop_id: str, kind: str, target: dict[str, Any], installation: str | None
    ) -> dict[str, Any] | None:
        """An open view of the same thing, from the same installation."""
        for view in self.store.views(desktop_id):
            if view["kind"] != kind:
                continue
            if kind == "app" and view["appId"] == target["app_id"]:
                if view["installationId"] == installation:
                    return view
                continue
            if kind == "host" and view["surface"] == target["surface_key"]:
                return view
            if kind == "web" and view["url"] == target["url"]:
                return view
            if kind == "agent":
                return view
        return None

    def _describe(self, view: dict[str, Any]) -> dict[str, Any]:
        """A view as the dashboard reads it, with what is true about it now."""
        available = True
        reason = None
        if view["kind"] == "app":
            # Compared on every read rather than invalidated by a hook at
            # uninstall time: a hook is something a future code path can forget
            # to call, and a window pointing at a replaced installation is
            # exactly what must never be treated as still bound to it.
            current = self._storage.installation(view["appId"]) if self._storage else None
            if current is None:
                available, reason = False, "uninstalled"
            elif current != view["installationId"]:
                available, reason = False, "reinstalled"
        return {
            **view,
            "available": available,
            "unavailableReason": reason,
            # Owner surfaces are the person's own controls. An agent is never
            # pointed at one, and saying so here keeps that decision in one
            # place rather than in every caller that iterates views.
            "agentViewable": view["kind"] in AGENT_VIEWABLE_KINDS,
        }

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
