"""Watching the screen the agent is actually using, and taking it over.

Watching is passive. That is a design decision and not an implementation
detail: a click on the picture does nothing until somebody has explicitly taken
control, because a person leaning on a trackpad while reading should not type
into a form an agent is halfway through filling in.

A frame is one view's picture, never the desktop's. The whole-desktop capture
that would be easier to build is the one thing that must not exist: it would
contain the owner's approval prompt, and handing the agent's viewer a picture of
the control that authorizes the agent is the kind of mistake that is obvious
only afterwards. `session.captureFrame` is scoped to a page for that reason, and
this never widens it. The one exception lives in `desktop_frame.py` and is not
a capture at all: in controller mode, behind an explicit opt-in, it composes
the per-view frames this file already takes into one preview for the
controller token. What a per-view frame hides stays hidden there by
construction, and everywhere else the rule stands exactly as written above.

Frames are served through the authenticated owner API with `no-store`, and are
never static files. They are also never a credential: a frame carries the view,
the runtime session and the control epoch it was taken under, and input aimed at
one is checked against all three plus its age.

Two smaller things live here because watching is what they are about. A
**heartbeat** is how a watcher says it is still watching — a controller keeps
its bot's computer awake while somebody is looking, and a count is how it knows.
And the **activity line** is the one-sentence answer to "what is it doing",
set by the run loops from the step they are on and read back with the watcher
status.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from ..app_storage import AppServiceError
from ..desktops.models import DesktopError, validate_id, validate_view_id
from ..desktops.runtime import RuntimeUnavailable
from .activity import WORKING_STATES, Activity
from .control import Leases, check_button, check_frame_age, check_point, check_scroll

#: How long a captured file is kept before the sweep removes it. Short: a frame
#: is a picture of a moment, and a directory of them is a directory of somebody's
#: screen.
FRAME_TTL_SECONDS = 120

#: Most frames kept on disk at once, whatever their age.
MAX_FRAMES = 60

#: How long one heartbeat says "still watching" for. Long enough that a viewer
#: polling a picture a second never lapses between beats; short enough that a
#: closed laptop stops counting quickly.
WATCHER_TTL_SECONDS = 20

#: Watchers one desktop counts at once. Bounded because a heartbeat is a
#: request anybody with the token can repeat; past the cap the watcher closest
#: to expiring is dropped first, so the count stays an honest "still here".
MAX_WATCHERS_PER_DESKTOP = 16

#: Longest viewer id accepted. It is a name the watcher chose for itself — a
#: random id from its own tab or process — and nothing else is asked of it.
MAX_VIEWER_ID = 64

#: The kinds a watcher may report. `person` is somebody at a screen; `controller`
#: is the owning process itself (its own preview polling). Only a controller-mode
#: Vela has a controller to report, so `person` is the only kind anywhere else.
WATCHER_KINDS = ("person", "controller")


class Watchers:
    """Who is watching each desktop, from heartbeats, expiring on their own.

    A watcher is a name, a kind and an expiry — nothing about the person, and
    nothing kept once the beats stop. The count is what a controller acts on:
    somebody is watching, so the computer stays awake; the count reached zero,
    so it may sleep.
    """

    def __init__(self):
        self._lock = threading.Lock()
        #: (desktop id, viewer id) -> {"kind", "viewId", "expiresAt"}.
        self._all: dict[tuple[str, str], dict[str, Any]] = {}

    def heartbeat(
        self, desktop_id: str, viewer_id: str, *, kind: str, view_id: str | None = None
    ) -> dict[str, Any]:
        """One watcher says it is still here. The same id refreshes, never doubles."""
        now = time.time()
        with self._lock:
            self._sweep(now)
            held = [
                key for key in self._all if key[0] == desktop_id and key[1] != viewer_id
            ]
            if len(held) >= MAX_WATCHERS_PER_DESKTOP:
                # The watcher closest to expiring has been quietest the longest;
                # a fresh beat from somebody new outranks it.
                stalest = min(held, key=lambda key: self._all[key]["expiresAt"])
                del self._all[stalest]
            self._all[(desktop_id, viewer_id)] = {
                "kind": kind,
                "viewId": view_id,
                "expiresAt": now + WATCHER_TTL_SECONDS,
            }
            return self._summary(desktop_id, now)

    def summary(self, desktop_id: str) -> dict[str, Any]:
        """How many are watching, by kind. Counts only — no ids, nothing personal."""
        with self._lock:
            self._sweep(time.time())
            return self._summary(desktop_id, time.time())

    def drop(self, desktop_id: str) -> None:
        with self._lock:
            for key in [key for key in self._all if key[0] == desktop_id]:
                del self._all[key]

    def _sweep(self, now: float) -> None:
        for key in [key for key, watcher in self._all.items() if watcher["expiresAt"] <= now]:
            del self._all[key]

    def _summary(self, desktop_id: str, now: float) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        count = 0
        for (owner, _viewer_id), watcher in self._all.items():
            if owner != desktop_id or watcher["expiresAt"] <= now:
                continue
            count += 1
            kinds[watcher["kind"]] = kinds.get(watcher["kind"], 0) + 1
        return {"count": count, "kinds": kinds}


class Viewer:
    """Frames and control for every agent desktop on this server."""

    def __init__(self, desktops, runs, *, frames_dir: Path | None = None):
        self.desktops = desktops
        self.runs = runs
        self.leases = Leases()
        #: Who says they are watching, by heartbeat. Read with the status, so a
        #: controller can see a direct viewer counting beside its own beats.
        self.watchers = Watchers()
        #: The one line saying what the desktop is doing, written by the run
        #: loops. Attached to the supervisor the way the leases are.
        self.activity = Activity()
        self._frames_dir = Path(frames_dir or desktops.data_dir / "agent-frames")
        #: view id -> the last frame taken of it, so a viewer polling at ten
        #: frames a second does not ask the browser ten times a second.
        self._latest: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- frames --

    async def frame(self, desktop_id: str, view_id: str, *, max_age_ms: int = 400) -> dict[str, Any]:
        """A recent picture of one view, capturing a new one only if needed.

        `max_age_ms` is the viewer saying how fresh it needs. Two viewers
        watching the same desktop share one capture rather than doubling the
        cost of being watched — the agent is working, and being watched should
        not slow it down.

        A terminal's or an editor's picture is text, not a capture: the recent
        screen, served from the state the service already keeps (controller
        mode only; no such view exists anywhere else).
        """
        desktop_id = validate_id(desktop_id)
        view_id = validate_view_id(view_id)
        view = next(
            (
                item
                for item in self.desktops.views(desktop_id)["views"]
                if item["id"] == view_id
            ),
            None,
        )
        if view is None:
            raise DesktopError(404, "That window is not open on this desktop.")
        if view["kind"] == "terminal":
            terminals = self.desktops.terminals
            if terminals is None:
                raise DesktopError(503, "Terminals are not available on this server.")
            return terminals.frame(desktop_id, view_id)
        if view["kind"] == "editor":
            editors = self.desktops.editors
            if editors is None:
                raise DesktopError(503, "Editors are not available on this server.")
            return editors.frame(desktop_id, view_id)
        self.desktops.agent_runtime_for(desktop_id)
        if not view["agentViewable"]:
            # An owner surface is the person's own window. It is not something
            # the viewer streams, because there is nothing remote about it.
            raise DesktopError(422, "That window is one of yours, not the agent's.")

        cached = self._latest.get(view_id)
        if cached and (time.time() - cached["takenAt"]) * 1000 < max_age_ms:
            return cached

        try:
            captured = await self.desktops.runtime.command(
                "view.capture", desktopId=desktop_id, viewId=view_id, timeout=20.0
            )
        except RuntimeUnavailable as exc:
            raise DesktopError(503, str(exc)) from exc
        record = {
            "viewId": view_id,
            "desktopId": desktop_id,
            "file": captured["file"],
            "digest": captured["digest"],
            "bytes": captured["bytes"],
            "width": captured["width"],
            "height": captured["height"],
            "deviceScaleFactor": captured["deviceScaleFactor"],
            "capturedAt": captured["capturedAt"],
            "runtimeSessionId": captured["runtimeSessionId"],
            "controlEpoch": captured["controlEpoch"],
            "takenAt": time.time(),
        }
        self._latest[view_id] = record
        self.sweep()
        return record

    def frame_file(self, desktop_id: str, view_id: str, digest: str) -> Path:
        """The bytes of a frame this viewer was just told about.

        Checked against what was actually captured for this view rather than
        read from the path: a digest is a name, and a name that came from a
        request is not a reason to open a file.
        """
        record = self._latest.get(validate_view_id(view_id))
        if record is None or record["digest"] != digest or record["desktopId"] != desktop_id:
            raise DesktopError(404, "That picture is no longer available.")
        path = self._frames_dir / record["file"]
        if not path.is_file():
            raise DesktopError(404, "That picture is no longer available.")
        return path

    def sweep(self) -> int:
        """Remove old captures. Somebody's screen is not something to keep."""
        try:
            files = sorted(
                self._frames_dir.glob("*.png"), key=lambda path: path.stat().st_mtime, reverse=True
            )
        except OSError:
            return 0
        keep = {record["file"] for record in self._latest.values()}
        removed = 0
        now = time.time()
        for index, path in enumerate(files):
            if path.name in keep:
                continue
            try:
                if index >= MAX_FRAMES or now - path.stat().st_mtime > FRAME_TTL_SECONDS:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def forget_desktop(self, desktop_id: str) -> None:
        for view_id in [
            key for key, record in self._latest.items() if record["desktopId"] == desktop_id
        ]:
            self._latest.pop(view_id, None)
        self.leases.drop(desktop_id)
        self.watchers.drop(desktop_id)
        self.activity.drop(desktop_id)

    # ------------------------------------------------------------ control --

    def status(self, desktop_id: str) -> dict[str, Any]:
        """Who is watching what, who — if anyone — is typing, and what is happening."""
        desktop_id = validate_id(desktop_id)
        desktop = self.desktops.store.get(desktop_id)
        active = self.runs.store.active(desktop_id)
        return {
            "desktopId": desktop_id,
            "kind": desktop["kind"],
            "control": self.leases.holder(desktop_id),
            "task": {"runId": active["id"], "state": active["state"]} if active else None,
            "watchers": self.watchers.summary(desktop_id),
            "activity": self._activity(desktop_id, active),
        }

    def _activity(self, desktop_id: str, active: dict[str, Any] | None) -> dict[str, Any] | None:
        """The one line, only while the run it belongs to is actually working.

        A paused run, one waiting on an approval and one that has ended are all
        idle, and idle shows nothing — whatever the last step's line was.
        """
        if active is None or active["state"] not in WORKING_STATES:
            return None
        record = self.activity.current(desktop_id)
        if record is None or record["runId"] != active["id"]:
            return None
        return {"text": record["text"], "at": record["at"], "viewId": record["viewId"]}

    def heartbeat(self, desktop_id: str, request: Any, *, controller: bool) -> dict[str, Any]:
        """A watcher says it is still watching; the answer is who else is.

        The id is the watcher's own — a random name from its tab or process —
        so a refreshed beat replaces itself rather than counting twice. The kind
        says what the watcher is; only a controller-mode Vela has a controller
        to report, and only the kinds on the short list are words this keeps.
        """
        desktop_id = validate_id(desktop_id)
        self.desktops.store.get(desktop_id)
        request = request if isinstance(request, dict) else {}
        viewer_id = request.get("viewerId")
        if (
            not isinstance(viewer_id, str)
            or not viewer_id.strip()
            or len(viewer_id) > MAX_VIEWER_ID
            or any(char.isspace() or not char.isprintable() for char in viewer_id)
        ):
            raise AppServiceError(422, "A heartbeat names its watcher: a short id it chose.")
        kind = request.get("kind")
        if kind is None:
            kind = "controller" if controller else "person"
        if kind not in WATCHER_KINDS:
            raise AppServiceError(422, "A watcher is a person or a controller.")
        if kind == "controller" and not controller:
            raise AppServiceError(422, "This Vela has no controller to be watching for it.")
        view_id = request.get("viewId")
        view_id = validate_view_id(view_id) if view_id else None
        watchers = self.watchers.heartbeat(desktop_id, viewer_id.strip(), kind=kind, view_id=view_id)
        return {
            "watching": True,
            "expiresInSeconds": WATCHER_TTL_SECONDS,
            "watchers": watchers,
        }

    async def take_over(self, desktop_id: str, *, view_id: Any = None) -> dict[str, Any]:
        """Stop the agent, change the generation, then hand control over.

        In that order, and the order is the whole thing. Issuing the lease first
        would leave a window in which a command the agent sent a moment ago
        arrives while a person is typing.
        """
        desktop_id = validate_id(desktop_id)
        self.desktops.agent_runtime_for(desktop_id)
        view_id = validate_view_id(view_id) if view_id else None

        # 1. Dispatch stops. A run mid-step finishes that step and then holds;
        #    a run waiting on the model is not given another turn.
        active = self.runs.store.active(desktop_id)
        if active and active["state"] in ("running", "starting"):
            await self.runs.supervisor.control(desktop_id, active["id"], "pause")

        # 2. A new control generation, so everything the agent was holding —
        #    in-flight commands and every observation — stops being valid.
        try:
            result = await self.desktops.runtime.command(
                "control.take", desktopId=desktop_id, timeout=15.0
            )
        except RuntimeUnavailable as exc:
            raise DesktopError(503, str(exc)) from exc

        # 3. Only now is anybody allowed to type.
        lease = self.leases.take(desktop_id, view_id=view_id, epoch=int(result["controlEpoch"]))
        self.runs.supervisor.emit(
            desktop_id,
            "control.taken",
            {"leaseId": lease.id, "viewId": view_id, "controlEpoch": lease.epoch},
            run_id=active["id"] if active else None,
        )
        return {**lease.as_dict(), "task": self.status(desktop_id)["task"]}

    def release(self, desktop_id: str, lease_id: str) -> dict[str, Any]:
        """Give control back. Deliberately not the same as resuming the agent.

        Somebody may be releasing because they are finished, or because they are
        leaving. An agent that started working again because a person closed a
        laptop would be an agent nobody asked to continue.
        """
        desktop_id = validate_id(desktop_id)
        released = self.leases.release(desktop_id, lease_id)
        if released:
            self.runs.supervisor.emit(desktop_id, "control.released", {"leaseId": lease_id})
        active = self.runs.store.active(desktop_id)
        return {
            "released": released,
            "task": {"runId": active["id"], "state": active["state"]} if active else None,
            "note": (
                "The task is still paused. It carries on when you say so, and it looks at "
                "the screen again before it does anything."
            ),
        }

    async def send_input(self, desktop_id: str, lease_id: str, request: Any) -> dict[str, Any]:
        """One piece of human input, into the view it was aimed at."""
        desktop_id = validate_id(desktop_id)
        lease = self.leases.require(desktop_id, lease_id)
        request = request if isinstance(request, dict) else {}
        view_id = validate_view_id(request.get("viewId") or lease.view_id or "")
        view = next(
            (
                item
                for item in self.desktops.views(desktop_id)["views"]
                if item["id"] == view_id
            ),
            None,
        )
        if view is None or view["desktopId"] != desktop_id:
            raise AppServiceError(404, "That window is not open on this desktop.")
        if view["kind"] == "terminal":
            # The lease is the whole guard here: there is no picture to be
            # stale, so text and keys go to the pty as soon as the one writer
            # says so.
            return await self._send_terminal_input(desktop_id, view_id, request)
        if view["kind"] == "editor":
            # The same guard, the same one-writer rule: the lease holder sends
            # the file's new text whole, and nothing else reaches an editor.
            return self._send_editor_input(desktop_id, view_id, request)
        frame = self._latest.get(view_id)
        if frame is None or frame["desktopId"] != desktop_id:
            raise AppServiceError(409, "Look at this window before typing into it.")

        kind = request.get("kind")
        payload: dict[str, Any] = {"kind": kind}
        if kind in ("click", "move"):
            # Aimed at a picture, so the picture has to be recent and the point
            # has to be inside the view that picture was of.
            check_frame_age(request.get("frameAt") or frame["capturedAt"])
            payload["point"] = check_point(
                request.get("point"), {"width": frame["width"], "height": frame["height"]}
            )
            if request.get("button"):
                # A right click is a first-class input — the long-press a touch
                # viewer sends — and the button names are a closed list.
                payload["button"] = check_button(request["button"])
            if request.get("clickCount"):
                payload["clickCount"] = int(request["clickCount"])
        elif kind == "scroll":
            # A wheel, or a touch-drag that scrolls: the wheel's own deltas,
            # bounded the way the worker bounds them.
            dx, dy = check_scroll(request.get("dx"), request.get("dy"))
            payload["dx"] = dx
            payload["dy"] = dy
        elif kind == "key":
            payload["key"] = str(request.get("key") or "")[:32]
        elif kind == "text":
            # Text the person chose, sent deliberately. Vela never reads the
            # host clipboard and never asks for permission to.
            payload["text"] = str(request.get("text") or "")
        else:
            raise AppServiceError(422, "That is not something you can send to a window.")

        try:
            result = await self.desktops.runtime.command(
                "view.input",
                desktopId=desktop_id,
                viewId=view_id,
                controlEpoch=lease.epoch,
                input=payload,
                timeout=20.0,
            )
        except RuntimeUnavailable as exc:
            raise AppServiceError(503, str(exc)) from exc
        # What was on screen a moment ago no longer is.
        self._latest.pop(view_id, None)
        return {"ok": True, "after": result.get("after")}

    async def _send_terminal_input(
        self, desktop_id: str, view_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """A person's keystrokes, into the terminal that owns this window."""
        terminals = self.desktops.terminals
        if terminals is None:
            raise AppServiceError(503, "Terminals are not available on this server.")
        kind = request.get("kind")
        if kind == "text":
            # Text the person chose, sent deliberately — the same words the
            # browser path sends, into the pty instead of a page.
            return terminals.write(desktop_id, terminals.require_by_view(desktop_id, view_id).id,
                                   text=str(request.get("text") or ""))
        if kind == "key":
            key = str(request.get("key") or "").strip().lower()[:32]
            return terminals.write(desktop_id, terminals.require_by_view(desktop_id, view_id).id,
                                   keys=key)
        raise AppServiceError(
            422, "A terminal takes text or one named key, not clicks or scrolls."
        )

    def _send_editor_input(
        self, desktop_id: str, view_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """A person's save, into the editor their lease holds."""
        editors = self.desktops.editors
        if editors is None:
            raise AppServiceError(503, "Editors are not available on this server.")
        if request.get("kind") == "text":
            # Text the person chose and sent deliberately — the same words
            # the terminal path sends, saved here as the file's whole new
            # text. The lease is the guard: the one writer needs no revision.
            return editors.save_view(desktop_id, view_id, str(request.get("text") or ""))
        raise AppServiceError(
            422, "An editor takes text, the file's new content, not clicks or scrolls."
        )
