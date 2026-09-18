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

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from ..desk import BOARD_COLS, BOARD_VERSION, DeskError, default_boards, repair_widgets, validate_widgets
from ..wallpaper import MAX_WALLPAPER_BYTES, WALLPAPER_TYPES, WallpaperError
from ..app_storage import AppServiceError
from .effects import EFFECTFUL
from .grants import Grants
from .principals import agent_of
from .migration import migrate
from .policy import empty_policy, granted_action, site_rule, validate_policy
from . import site_policy
from .runtime import BrowserRuntime, RuntimeUnavailable, availability as runtime_availability
from .store import new_id as new_runtime_session_id
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
        log=None,
        origin: str = "http://127.0.0.1:7700",
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
        # Changes an agent has asked about and nobody has answered yet. In
        # memory: a pending approval that survived a restart would be authority
        # for an effect whose run, view and browser are all gone.
        self._approvals = None
        #: view id -> what that window's SDK announced it understands. Volatile,
        #: and only ever used to say in advance that something will not work.
        self._view_features: dict[str, tuple[str, ...]] = {}
        self._auth = auth
        self._registry = registry
        # Named actions, attached after construction because the action service
        # is built from services that are built from this one. An agent invoking
        # an action goes through the same service a person's click does.
        self.actions = None
        # The tool surface a run acts through. Lazily built so a server with no
        # agent desktops never assembles one.
        self._tools = None
        # The managed browser. Created now, started only when a desktop needs
        # one: a server with no agent desktops should cost nothing.
        self._log = log or (lambda message: None)
        # Files a task was given and files it came back with. Built here because
        # the browser needs the staging directory before it starts.
        from ..agent_runs.artifacts import Artifacts

        self.artifacts = Artifacts(data_dir)
        self.runtime = BrowserRuntime(
            data_dir / "agent-frames",
            downloads_dir=self.artifacts.downloads_dir(),
            log=self._log,
        )
        # Deciding about a website request needs the policy, the grants and the
        # run. The runtime knows about a pipe; this knows about authority.
        self.runtime.on_effect_request = self.decide_site_effect
        self.runtime.on_view_notice = self.note_view_notice
        #: Requests that went out and whose answer never arrived, by desktop.
        #: An entry here is not a failure — it is the reason the same submission
        #: is never quietly sent a second time.
        self._uncertain: dict[str, dict[str, Any]] = {}
        #: Tasks, attached after construction the way actions are. Deciding
        #: about a site request has to name the run it belongs to.
        self.runs = None
        #: Notices already recorded. The worker both pushes one and keeps it for
        #: the next result, so both arrive and only the first counts.
        self._seen_notices: set[str] = set()
        #: desktop id -> the browser lifetime a site grant is bound to.
        self._runtime_sessions: dict[str, str] = {}
        # The one origin the managed browser may talk to. Everything else on
        # this computer, including the rest of Vela, is off the list.
        self._origin = origin.rstrip("/")

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
        # Staged transfers and a remembered sign-in go with the workspace. An
        # installed app's own documents are not here and are not touched.
        files = self.artifacts.forget_desktop(desktop_id)
        self.drop_session_file(desktop_id)
        self._uncertain.pop(desktop_id, None)
        self.store.delete(desktop_id)
        removed = self.cleanup_assets()
        return {"ok": True, "removedAssets": removed, "removedFiles": files}

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

    # --------------------------------------------------------- runtime --

    def runtime_status(self) -> dict[str, Any]:
        """Whether agent desktops can run here, and which ones are running.

        Answered before any work is accepted rather than discovered halfway
        through a task, and each unavailable reason says what to do about it.
        """
        state = runtime_availability()
        return {
            **state,
            "running": self.runtime.running,
            "desktops": sorted(self.runtime.desktops),
            "info": self.runtime.info.as_dict() if self.runtime.info else None,
        }

    async def stop_runtime(self) -> None:
        """Close the browser deliberately. Called when Vela stops."""
        await self.runtime.stop()

    def gateway_policy(self, desktop_id: str) -> dict[str, Any]:
        """What the browser for this desktop may reach, as the worker wants it.

        Derived from the owner's policy rather than stored separately, so there
        is one answer to "what is allowed" and the browser's copy cannot drift
        from the one the effect boundary checks.

        The prefixes are as narrow as the paths allow: the app host page, the
        assets it is built from, the bridge routes, and each allowed app's own
        content — by id, not `/apps/`, so an allowed page cannot pull a
        disallowed app's files.
        """
        policy = self.store.policy(desktop_id)
        prefixes = ["/agent-host/", "/assets/", "/api/app/"]
        prefixes += [f"/apps/{app_id}/" for app_id in policy.get("apps") or []]
        return {
            "gatewayOrigin": self._origin,
            "gatewayPathPrefixes": prefixes,
            "sites": [
                {**rule, "effects": site_policy.effects_mode(rule)}
                for rule in policy.get("sites") or []
            ],
            # Off unless this server was started to run the site fixtures. It
            # lets an *approved* origin be on this machine and does nothing
            # else; the rest of loopback and the whole local network stay
            # refused. `docs/TESTING.md` says when to set it.
            "allowPrivateSites": bool(os.environ.get("VELA_BROWSER_ALLOW_PRIVATE_SITES")),
        }

    async def enable_agent(self, desktop_id: str) -> dict[str, Any]:
        """Turn this desktop into one an agent runs in.

        Deliberately in this order: check first, start second, convert third,
        and mark the desktop only once all three have happened. A conversion
        that reported success with nothing behind it would be worse than one
        that refused, because everything after it would be built on the report.
        """
        desktop_id = validate_id(desktop_id)
        desktop = self.store.get(desktop_id)
        policy = self.store.policy(desktop_id)
        if not (policy.get("apps") or policy.get("sites")):
            raise DesktopError(
                422, "Choose what this desktop may use before turning on its agent."
            )
        state = runtime_availability()
        if not state["available"]:
            raise DesktopError(503, state["detail"])
        if desktop["kind"] == "agent" and desktop_id in self.runtime.desktops:
            return {"desktop": self.store.get(desktop_id), "views": self.views(desktop_id)["views"]}

        started = False
        try:
            await self.runtime.start()
            session_id = new_runtime_session_id()
            await self.runtime.open_desktop(
                desktop_id,
                session_id,
                self.gateway_policy(desktop_id),
                storage_state=self.remembered_session(desktop_id),
            )
            self._runtime_sessions[desktop_id] = session_id
            started = True
            moved, notes = await self._convert_views(desktop_id)
            self.store.set_kind(desktop_id, "agent")
        except (RuntimeUnavailable, OSError) as exc:
            # Rolling back matters more than the error message: a desktop that
            # is half converted is one the person cannot use and cannot fix.
            if started:
                await self.runtime.close_desktop(desktop_id)
            raise DesktopError(503, str(exc)) from exc
        return {
            "desktop": self.store.get(desktop_id),
            "views": self.views(desktop_id)["views"],
            "moved": moved,
            "notes": notes,
        }

    async def disable_agent(self, desktop_id: str) -> dict[str, Any]:
        """Give the desktop back, and take the agent's authority with it."""
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        self.revoke_desktop(desktop_id, reason="the agent was turned off")
        self._runtime_sessions.pop(desktop_id, None)
        self._uncertain.pop(desktop_id, None)
        try:
            await self.runtime.close_desktop(desktop_id)
        except RuntimeUnavailable:
            # Already gone. Nothing to close is the state this was aiming for.
            pass
        self.store.set_kind(desktop_id, "personal")
        # Its configuration and its windows stay: turning the agent off is not
        # throwing the workspace away.
        return {"desktop": self.store.get(desktop_id)}

    async def _convert_views(self, desktop_id: str) -> tuple[list[str], list[str]]:
        """Reopen the supported windows inside the managed browser.

        A view that cannot move says so rather than being dropped. The person
        chose to have it open, and "it is not there any more" is not an
        acceptable way to find out that it could not come along.
        """
        policy = self.store.policy(desktop_id)
        allowed = set(policy.get("apps") or [])
        moved: list[str] = []
        notes: list[str] = []
        for view in self.store.views(desktop_id):
            described = self._describe(view)
            if view["kind"] != "app":
                notes.append(f"{view['title'] or view['kind']} stays on your side of the window.")
                continue
            if not described["available"]:
                notes.append(f"{view['title'] or view['appId']} needs reopening first.")
                continue
            if view["appId"] not in allowed:
                notes.append(f"{view['appId']} is not one of this desktop's allowed apps.")
                continue
            try:
                await self.open_in_browser(desktop_id, view)
                moved.append(view["id"])
            except RuntimeUnavailable as exc:
                notes.append(f"{view['appId']} could not be opened there: {exc}")
        return moved, notes

    async def open_in_browser(self, desktop_id: str, view: dict[str, Any]) -> dict[str, Any]:
        """Give one stored view a page in this desktop's managed browser.

        The single place a view becomes something the agent can look at, used by
        the conversion and by the open tools alike. One path means one set of
        rules about what a page is given before it loads.
        """
        if view["kind"] == "app":
            return await self.runtime.command(
                "view.open",
                desktopId=desktop_id,
                viewId=view["id"],
                url=f"{self._origin}/agent-host/{view['id']}",
                bootstrap=self._bootstrap(desktop_id, view),
                timeout=45.0,
            )
        if view["kind"] == "web":
            # No bootstrap: a website gets no Vela session, no token and nothing
            # about the desktop it is being looked at from.
            return await self.runtime.command(
                "view.open",
                desktopId=desktop_id,
                viewId=view["id"],
                url=view["url"],
                timeout=45.0,
            )
        raise DesktopError(422, "That kind of window does not open in the agent's browser.")

    @property
    def approvals(self):
        """Pending changes waiting for the owner's answer."""
        if self._approvals is None:
            if self.grants is None:
                raise DesktopError(503, "Approvals are not available yet.")
            from ..agent_runs.approvals import Approvals

            self._approvals = Approvals(self.grants, log=self._log)
        return self._approvals

    @property
    def tools(self):
        """The typed tools a run uses to perceive and operate this desktop."""
        if self._tools is None:
            from ..agent_runs.tools import AgentTools

            self._tools = AgentTools(self)
        return self._tools

    def agent_runtime_for(self, desktop_id: str) -> None:
        """Refuse early when this desktop has no browser behind it.

        A desktop marked as an agent's whose browser died — a crash, a restart,
        a machine that slept — is not a desktop an agent can work in, and saying
        so is better than a command that fails somewhere less obvious.
        """
        desktop = self.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        if not self.runtime.running or desktop_id not in self.runtime.desktops:
            raise DesktopError(
                503,
                "This desktop's agent browser is not running. Turn the agent off and on "
                "again to start a new one.",
            )

    def _bootstrap(self, desktop_id: str, view: dict[str, Any]) -> dict[str, Any]:
        """What the app host page is given before it loads.

        Its session, and the address of the app it is hosting. Nothing about the
        owner, the run's instructions or any other desktop: the page an agent
        looks at should contain what it needs to show one app and no more.
        """
        manifest = self._registry.get(view["appId"])
        issued = self._auth.issue_agent(
            manifest,
            view["installationId"],
            {
                "desktopId": desktop_id,
                "runId": f"conversion:{desktop_id}",
                "viewId": view["id"],
                "actorId": "conversion",
                "policyRevision": self.store.policy(desktop_id)["revision"],
            },
        )
        return {
            "token": issued["token"],
            "installationId": issued["installationId"],
            "capabilities": issued["capabilities"],
            "unavailableCapabilities": issued["unavailableCapabilities"],
            "appId": view["appId"],
            "appName": manifest.name,
            "appUrl": f"{self._origin}/apps/{view['appId']}/",
        }

    # ------------------------------------------------- website effects --

    async def decide_site_effect(self, desktop_id: Any, request: Any) -> dict[str, Any]:
        """Whether a request an approved website would receive may be sent.

        Called by the browser with the request held. Three answers, and every
        path reaches one: send it, a question is open, or this one needs a
        person. Nothing has been sent while this is deciding, which is what
        makes "no" and "not yet" both safe.

        The narrowest honest claim is the one made here. Vela does not decide
        that a site is harmless because the method is GET; it decides that it
        will not *cause* what it cannot describe, describes what it can, and
        binds the owner's answer to that exact description.
        """
        try:
            desktop_id = validate_id(desktop_id)
            desktop = self.store.get(desktop_id)
        except DesktopError:
            return {"decision": "person", "detail": "That desktop is gone."}
        if desktop["kind"] != "agent":
            return {"decision": "person", "detail": "This desktop is not running an agent."}

        policy = self.store.policy(desktop_id)
        effect = site_policy.classify(request)
        rule = site_rule(policy, effect.origin)
        if rule is None:
            # The network boundary should already have refused this. Saying no
            # twice costs nothing; saying yes because the first check was
            # assumed to have happened is how a boundary stops being one.
            return {"decision": "person", "detail": "That site is not approved on this desktop."}

        if effect.kind == "read":
            return {"decision": "allow"}

        pending = self._uncertain.get(desktop_id, {}).get(effect.digest)
        if pending is not None:
            # It went out once and nobody can say what became of it. Sending it
            # again is how one order becomes two, so this stops here until a
            # person says what happened.
            return {
                "decision": "person",
                "detail": (
                    "This was already sent once and Vela never saw the answer. Check "
                    "whether it went through before sending it again."
                ),
            }

        verdict = site_policy.decide(rule, effect)
        if verdict == "person":
            return {"decision": "person", "detail": effect.reason}

        run = self._active_run(desktop_id)
        binding = {
            "desktop_id": desktop_id,
            "effect": "submit",
            "app_id": site_policy.principal(rule["origin"]),
            # This browser lifetime. A new browser is a new session on the site,
            # so an answer given for the old one does not carry over.
            "installation_id": self._runtime_session_id(desktop_id),
            "contract": site_policy.contract(rule, policy.get("revision") or 0),
            "run_id": run["id"] if run else None,
            "request_digest": effect.digest,
            "scope": {"site": rule["origin"], "method": effect.method, "path": effect.path},
        }
        try:
            authorize = self.require_or_ask(
                binding,
                policy=policy,
                app_name=rule["origin"],
                view_id=request.get("viewId") if isinstance(request, dict) else None,
                scope=binding["scope"],
                summary=site_policy.summarize(effect),
            )
            with self.grants.storage.connection() as db:
                authorize(db)
        except Exception as exc:  # noqa: BLE001 - every outcome is an answer
            from ..agent_runs.approvals import ApprovalPending

            if isinstance(exc, ApprovalPending):
                return {
                    "decision": "ask",
                    "requestId": exc.record["requestId"],
                    "detail": "Waiting for you to say whether this may be sent.",
                }
            self._log(f"could not decide about a site request: {exc}")
            return {"decision": "person", "detail": getattr(exc, "detail", str(exc))}
        return {"decision": "allow"}

    def _active_run(self, desktop_id: str):
        if self.runs is None:
            return None
        try:
            return self.runs.store.active(desktop_id)
        except Exception:  # noqa: BLE001 - a missing run is not this decision's problem
            return None

    def _runtime_session_id(self, desktop_id: str) -> str:
        """This browser lifetime, as the thing a site grant is bound to.

        The browser is the closest thing a website has to an installation: close
        it and the cookies, the storage and the half-finished form are gone, and
        an answer given about that session should go with it.
        """
        return self._runtime_sessions.get(desktop_id) or "no-session"

    # --------------------------------------------------- what happened --

    def note_view_notice(self, desktop_id: Any, notice: Any) -> None:
        """Something that happened to a view without an action causing it.

        Pushed by the worker as it happens, and also collected with the next
        result. Both paths land here, and this is idempotent, because a download
        recorded twice would be a file counted twice against a quota.
        """
        try:
            self.collect_notices(validate_id(desktop_id), [notice])
        except Exception as exc:  # noqa: BLE001 - never the caller's problem
            self._log(f"could not record what happened on a desktop: {exc}")

    def collect_notices(
        self, desktop_id: str, notices: Any, *, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Turn what the browser reported into records, once each.

        Returns what a run should be told: the artifacts a download produced and
        the attention states that need a person. What it does not return is the
        staged path, which is Vela's and never leaves it.
        """
        out: list[dict[str, Any]] = []
        for notice in notices if isinstance(notices, list) else []:
            if not isinstance(notice, dict):
                continue
            key = self._notice_key(desktop_id, notice)
            if key in self._seen_notices:
                continue
            self._seen_notices.add(key)
            if len(self._seen_notices) > 2000:
                self._seen_notices.clear()
                self._seen_notices.add(key)
            kind = notice.get("type")
            if kind == "download":
                out.append(self._ingest_download(desktop_id, notice, run_id=run_id))
            elif kind == "effect_uncertain":
                self._uncertain.setdefault(desktop_id, {})[notice.get("digest") or key] = {
                    "url": notice.get("url"),
                    "method": notice.get("method"),
                    "at": time.time(),
                }
                out.append(
                    {
                        "attention": "outcome_unknown",
                        "detail": (
                            f"{notice.get('method')} {notice.get('url')} was sent and no "
                            "answer arrived. Vela cannot say whether it went through, and "
                            "will not send it again on its own."
                        ),
                    }
                )
            elif kind in ("effect_needs_person", "file_chooser_cancelled", "download_refused",
                          "download_failed", "dialog", "effect_pending"):
                out.append({"attention": kind, "detail": _notice_detail(notice)})
        return out

    def _notice_key(self, desktop_id: str, notice: dict[str, Any]) -> str:
        parts = [
            desktop_id,
            str(notice.get("type")),
            str(notice.get("path") or notice.get("digest") or notice.get("url") or ""),
            str(notice.get("at") or ""),
            str(notice.get("message") or "")[:80],
        ]
        return "|".join(parts)

    def _ingest_download(
        self, desktop_id: str, notice: dict[str, Any], *, run_id: str | None
    ) -> dict[str, Any]:
        from ..agent_runs.artifacts import ArtifactError, envelope

        try:
            record = self.artifacts.ingest_download(
                desktop_id,
                Path(str(notice.get("path") or "")),
                name=notice.get("name"),
                run_id=run_id or (self._active_run(desktop_id) or {}).get("id"),
                origin=_origin_of(notice.get("url")),
                view_id=notice.get("viewId"),
            )
        except ArtifactError as exc:
            return {"attention": "download_refused", "detail": exc.detail}
        except OSError as exc:
            return {"attention": "download_failed", "detail": str(exc)}
        return {"file": envelope(record)}

    def uncertain(self, desktop_id: str) -> list[dict[str, Any]]:
        """Submissions that went out and whose answer never arrived."""
        return [
            {"digest": digest, **record}
            for digest, record in (self._uncertain.get(validate_id(desktop_id)) or {}).items()
        ]

    def resolve_uncertain(self, desktop_id: str, digest: Any = None) -> dict[str, Any]:
        """The owner saying what became of one, or of all of them.

        Deliberately an owner action and never an automatic one. The only thing
        that can establish what happened on somebody else's server is somebody
        looking, and a timer is not somebody looking.
        """
        desktop_id = validate_id(desktop_id)
        held = self._uncertain.get(desktop_id) or {}
        if digest is None:
            self._uncertain.pop(desktop_id, None)
            return {"cleared": len(held)}
        if str(digest) not in held:
            raise DesktopError(404, "There is nothing waiting to be checked under that name.")
        held.pop(str(digest))
        return {"cleared": 1}

    # ------------------------------------------------- website sessions --

    def sessions_dir(self) -> Path:
        path = self.data_dir / "desktop-sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_file(self, desktop_id: str) -> Path:
        return self.sessions_dir() / f"{desktop_id}.json"

    def remembered_session(self, desktop_id: str) -> dict[str, Any] | None:
        """A signed-in state saved for this desktop, if the owner kept one."""
        if not (self.store.policy(desktop_id).get("rememberSessions")):
            return None
        try:
            return json.loads(self._session_file(desktop_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def session_state(self, desktop_id: str) -> dict[str, Any]:
        """What is being kept for this desktop, in numbers rather than contents.

        A count of cookies and the sites they belong to. Never the cookies: a
        route that could read them back would be a route that turns a browser
        session into something anything with the owner's token could take away.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        policy = self.store.policy(desktop_id)
        path = self._session_file(desktop_id)
        saved = None
        cookies = 0
        origins: list[str] = []
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            saved = path.stat().st_mtime
            cookies = len(document.get("cookies") or [])
            origins = sorted(
                {str(entry.get("origin")) for entry in (document.get("origins") or []) if entry}
            )[:32]
        except (OSError, ValueError):
            pass
        return {
            "desktopId": desktop_id,
            "allowed": bool(policy.get("rememberSessions")),
            "remembered": saved is not None,
            "savedAt": saved,
            "cookies": cookies,
            "origins": origins,
        }

    async def remember_session(self, desktop_id: str) -> dict[str, Any]:
        """Keep this desktop's signed-in websites for the next browser.

        Only when the desktop's own settings allow it, only for this desktop,
        and never by importing anything from the person's own browser. What is
        saved is what this managed browser has, and erasing it is one action.
        """
        desktop_id = validate_id(desktop_id)
        self.agent_runtime_for(desktop_id)
        if not self.store.policy(desktop_id).get("rememberSessions"):
            raise DesktopError(
                409,
                "This desktop is set to forget website sign-ins. Turn that on in its "
                "settings first.",
            )
        try:
            result = await self.runtime.command(
                "session.storage", desktopId=desktop_id, timeout=30.0
            )
        except RuntimeUnavailable as exc:
            raise DesktopError(503, str(exc)) from exc
        document = result.get("storageState")
        if not isinstance(document, dict):
            raise DesktopError(502, "The browser did not return anything to keep.")
        path = self._session_file(desktop_id)
        path.write_text(
            json.dumps(document, separators=(",", ":")), encoding="utf-8"
        )
        _owner_only(path)
        return self.session_state(desktop_id)

    async def forget_session(self, desktop_id: str) -> dict[str, Any]:
        """Erase what was kept, and clear it out of the browser that is open.

        Both halves, and both awaited. Either on its own leaves somebody signed
        in: a file with no browser comes back next time, and a browser with no
        file stays signed in until it closes. Reporting "erased" while a live
        context still holds the cookies would be the worse of the two.
        """
        desktop_id = validate_id(desktop_id)
        self.store.get(desktop_id)
        self.drop_session_file(desktop_id)
        cleared = False
        if self.runtime.running and desktop_id in self.runtime.desktops:
            with contextlib.suppress(RuntimeUnavailable):
                await self.runtime.command("session.forget", desktopId=desktop_id, timeout=20.0)
                cleared = True
        return {"desktopId": desktop_id, "remembered": False, "browserCleared": cleared}

    def drop_session_file(self, desktop_id: str) -> None:
        """Remove the stored sign-in. Used on its own when there is no browser
        left to clear — deleting the desktop, for one."""
        self._session_file(validate_id(desktop_id)).unlink(missing_ok=True)

    # ------------------------------------------------------- staged files --

    def resolve_artifacts(self, desktop_id: str, artifact_ids: Any) -> list[tuple[str, dict]]:
        """Turn artifact ids into the files Vela stored them as.

        The one place an id becomes a path, inside Vela, against a record Vela
        wrote. Nothing the model or a page said is ever treated as a location on
        this computer, and the path never travels back to either of them.
        """
        desktop_id = validate_id(desktop_id)
        ids = [str(value) for value in (artifact_ids or [])][:5]
        if not ids:
            raise DesktopError(422, "Say which file to attach.")
        resolved = []
        for artifact_id in ids:
            path, record = self.artifacts.file(desktop_id, artifact_id)
            resolved.append((str(path), record))
        return resolved

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
        self.revoke_desktop(desktop_id, reason="this desktop's permissions changed")
        return saved

    # ---------------------------------------------------------- grants --

    def revoke_desktop(self, desktop_id: str, *, reason: str = "this desktop's permissions changed") -> dict[str, Any]:
        """Drop every grant, agent session and pending question this desktop holds.

        The pending ones matter as much as the grants. A prompt left on screen
        after the policy behind it changed is a question whose answer would
        authorize something nobody is asking for any more.
        """
        removed = self.grants.revoke_desktop(desktop_id) if self.grants else 0
        sessions = self._auth.revoke_agent(desktop_id=desktop_id) if self._auth else 0
        asked = self._cancel_approvals(desktop_id=desktop_id, reason=reason)
        return {"grants": removed, "sessions": sessions, "approvals": asked}

    def app_changed(self, app_id: str, *, reason: str = "that app changed") -> dict[str, Any]:
        """An app was removed, replaced or upgraded underneath a desktop.

        Every grant naming it goes, and every question waiting on it is
        cancelled. A grant already names an installation and a manifest
        fingerprint, so a reinstalled app would not match one anyway — but
        "would not match" leaves a row that means nothing sitting in the table
        until it expires, and a question on screen whose answer could no longer
        apply to anything. Removing both is the honest version.

        Windows stay open and say they need reopening. The person chose to have
        them; closing them to tidy up would be Vela deciding that for them.
        """
        removed = self.grants.revoke_app(app_id) if self.grants else 0
        sessions = self._auth.revoke_app(app_id) if self._auth else 0
        asked = self._cancel_approvals(app_id=app_id, reason=reason)
        if removed or asked:
            self._log(f"{app_id} changed: {removed} grant(s) and {asked} question(s) dropped")
        return {"grants": removed, "sessions": sessions, "approvals": asked}

    def revoke_run(self, desktop_id: str, run_id: str, *, reason: str = "the task stopped") -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        removed = self.grants.revoke_run(desktop_id, run_id) if self.grants else 0
        sessions = (
            self._auth.revoke_agent(desktop_id=desktop_id, run_id=run_id) if self._auth else 0
        )
        asked = self._cancel_approvals(desktop_id=desktop_id, run_id=run_id, reason=reason)
        return {"grants": removed, "sessions": sessions, "approvals": asked}

    def note_view_features(self, session: Any, features: Any) -> dict[str, Any]:
        """Record what the SDK in one window said it can do.

        Only ever consulted to warn. An app claiming a feature it does not have
        gets no authority from saying so — the worst it can do is make Vela stop
        warning about a limitation it really has, which is the app's own problem
        and never anybody else's data.
        """
        principal = agent_of(session)
        if principal is None or not principal.view_id:
            return {"ok": True}
        names = tuple(
            name for name in (features or []) if isinstance(name, str) and 0 < len(name) < 40
        )[:8]
        self._view_features[principal.view_id] = names
        return {"ok": True, "features": list(names)}

    def view_features(self, view_id: str) -> tuple[str, ...] | None:
        return self._view_features.get(view_id)

    def approval_for_session(self, session: Any, request_id: str) -> dict[str, Any]:
        """One pending request, as the app waiting on it may see it.

        Scoped to the session that asked: same desktop, same app, same
        installation. An app cannot look at another app's question, and reading
        one resolves nothing.
        """
        principal = agent_of(session)
        if principal is None:
            raise AppServiceError(403, "Only an agent's app session waits for approval.")
        record = self.approvals.get(request_id, desktop_id=principal.desktop_id)
        if (
            record["appId"] != session["app_id"]
            or record["installationId"] != session["installationId"]
        ):
            raise AppServiceError(404, "That request is not this app's.")
        return record

    def extend_approval_for_session(self, session: Any, request_id: str) -> dict[str, Any]:
        self.approval_for_session(session, request_id)
        principal = agent_of(session)
        return self.approvals.extend(request_id, desktop_id=principal.desktop_id)

    def abandon_approval_for_session(self, session: Any, request_id: str) -> dict[str, Any]:
        """The app that asked has stopped waiting. Withdraw its question."""
        self.approval_for_session(session, request_id)
        principal = agent_of(session)
        cancelled = self.approvals.cancel(
            desktop_id=principal.desktop_id,
            request_id=request_id,
            reason="the app stopped waiting for an answer",
        )
        return {"cancelled": bool(cancelled)}

    def _cancel_approvals(self, **kwargs) -> int:
        """Cancel pending questions, without making a missing registry fatal."""
        if self.grants is None:
            return 0
        return self.approvals.cancel(**kwargs)

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

    def effect_guard(
        self,
        session: Any,
        effect: str,
        *,
        request_digest=None,
        scope=None,
        proposal=None,
        note=None,
    ):
        """What has to be true, inside the transaction, for this effect to land.

        Returns None when a person is asking — the owner using their own
        computer needs no grant from anyone — and otherwise a callable the
        effect's own transaction runs before it writes. That placement is the
        whole point: the answer cannot go stale between being given and being
        used, because giving it and using it are the same transaction.

        When an agent holds no grant for this effect, the answer is not
        automatically no. A pending request is opened, described in plain
        language from `proposal`, and the effect's transaction raises
        `ApprovalPending` instead of writing. Nothing is written while one is
        open; approving issues the grant and the effect commits by this same
        path, through this same check.
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

        return self.require_or_ask(
            binding,
            policy=policy,
            app_name=manifest.name,
            view_id=principal.view_id,
            proposal=proposal,
            scope=scope,
            note=note,
            current=self._current_document(session) if effect == "write" else None,
        )

    def require_or_ask(
        self,
        binding: dict[str, Any],
        *,
        policy: dict[str, Any],
        app_name: str,
        view_id: str | None,
        proposal: Any = None,
        scope: dict[str, Any] | None = None,
        note: str | None = None,
        current: Any = None,
        summary: dict[str, Any] | None = None,
    ):
        """One rule for "may this happen", wherever the effect came from.

        Returns the callable an effect's own transaction runs before it commits.
        Used by `effect_guard` for anything reaching the bridge, and by the tool
        surface for a named action a run invokes directly — two doors into the
        same decision, which is exactly why the decision lives in one place.
        """
        # Read once, outside the transaction, only to decide whether this needs
        # asking. It is never what authorizes anything: the check that counts
        # runs below, inside the write's own transaction.
        with self.grants.storage.connection() as db:
            already = self.grants.find(db, **binding)

        if already is not None:
            def authorize(db):
                self.grants.require(db, **binding)

            return authorize

        # The owner's standing answer, from the setup screen: in `granted` mode
        # the named actions they listed do not ask again. Only those — the mode
        # is not "allow everything", and nothing else consults this list.
        if binding["effect"] == "action" and granted_action(
            policy, (scope or {}).get("app"), (scope or {}).get("action")
        ):
            return lambda db: None

        # No grant. Open the question rather than closing it, and describe it
        # from the request itself — never from anything the agent said about it.
        from ..agent_runs.approvals import ApprovalPending, summarize

        # A caller that already built the sentence passes it. A website request
        # is described from its method, its address and its field names, which
        # is not a shape `summarize` knows or should learn.
        if summary is None:
            summary = summarize(
                binding["effect"],
                app_name=app_name,
                current=current,
                proposal=proposal,
                scope=scope,
                note=note,
            )
        record = self.approvals.request(
            desktop_id=binding["desktop_id"],
            run_id=binding["run_id"],
            view_id=view_id,
            effect=binding["effect"],
            app_id=binding["app_id"],
            app_name=app_name,
            installation_id=binding["installation_id"],
            contract=binding["contract"],
            request_digest=binding["request_digest"] or "",
            scope=scope,
            summary=summary,
        )

        def authorize(db):
            # Asked again inside the transaction, because the owner may have
            # answered in the meantime — and because a grant issued a moment ago
            # and a write landing now must contend for one lock, not two.
            if self.grants.find(db, **binding) is not None:
                return
            raise ApprovalPending(record)

        return authorize

    def _current_document(self, session: Any):
        """What this app has saved now, for describing what would change.

        A read that fails is not a reason to refuse the change; it means the
        prompt says less about it. Refusing a save because its *description*
        could not be built would be the wrong way round.
        """
        if self._storage is None:
            return None
        try:
            return self._storage.read(session["installationId"], session["schemaVersion"])["value"]
        except (AppServiceError, KeyError, TypeError):
            return None

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
        self._view_features.pop(view_id, None)
        # A question asked by a window that is gone has nobody left to answer
        # for. Approving it now would commit a change into a closed app.
        self._cancel_approvals(
            desktop_id=desktop_id, view_id=view_id, reason="its window was closed"
        )
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
            # Whether this window's app can wait while its owner decides about a
            # change. None until its SDK has said, because "we have not heard
            # yet" and "it cannot" are different things and only one of them is
            # worth warning somebody about.
            "canWaitForApproval": (
                None
                if self._view_features.get(view["id"]) is None
                else "approvals" in self._view_features[view["id"]]
            ),
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


def _origin_of(url: Any) -> str | None:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None


def _notice_detail(notice: dict[str, Any]) -> str:
    """One sentence about something that happened, in words a person reads."""
    kind = notice.get("type")
    if kind == "effect_needs_person":
        return (
            f"{notice.get('method')} {notice.get('url')} was not sent: "
            + (notice.get("detail") or "this one needs a person at the keyboard.")
        )
    if kind == "effect_pending":
        return f"{notice.get('method')} {notice.get('url')} is waiting for you to allow it."
    if kind == "dialog":
        return f"The page opened a {notice.get('kind')} box: {notice.get('message')}"
    if kind == "file_chooser_cancelled":
        return "The page asked for a file that nobody had chosen, so nothing was given to it."
    if kind == "download_refused":
        return f"A download was refused: {notice.get('detail') or 'it is outside what Vela accepts'}."
    if kind == "download_failed":
        return f"A download did not finish: {notice.get('detail') or 'it was interrupted'}."
    return str(notice.get("detail") or kind or "something happened")


def _owner_only(path: Path) -> None:
    """Take the group and world bits off, where a platform has them.

    A saved sign-in is the nearest thing in this directory to a password. On
    Windows the data directory's own permissions are what protects it, and
    `chmod` there is a no-op rather than a false reassurance.
    """
    try:
        path.chmod(0o600)
    except OSError:
        pass
