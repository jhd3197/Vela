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
this never widens it.

Frames are served through the authenticated owner API with `no-store`, and are
never static files. They are also never a credential: a frame carries the view,
the runtime session and the control epoch it was taken under, and input aimed at
one is checked against all three plus its age.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..app_storage import AppServiceError
from ..desktops.models import DesktopError, validate_id, validate_view_id
from ..desktops.runtime import RuntimeUnavailable
from .control import Leases, check_frame_age, check_point

#: How long a captured file is kept before the sweep removes it. Short: a frame
#: is a picture of a moment, and a directory of them is a directory of somebody's
#: screen.
FRAME_TTL_SECONDS = 120

#: Most frames kept on disk at once, whatever their age.
MAX_FRAMES = 60


class Viewer:
    """Frames and control for every agent desktop on this server."""

    def __init__(self, desktops, runs, *, frames_dir: Path | None = None):
        self.desktops = desktops
        self.runs = runs
        self.leases = Leases()
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
        """
        desktop_id = validate_id(desktop_id)
        view_id = validate_view_id(view_id)
        self.desktops.agent_runtime_for(desktop_id)
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

    # ------------------------------------------------------------ control --

    def status(self, desktop_id: str) -> dict[str, Any]:
        """Who is watching what, and who — if anyone — is typing."""
        desktop_id = validate_id(desktop_id)
        desktop = self.desktops.store.get(desktop_id)
        active = self.runs.store.active(desktop_id)
        return {
            "desktopId": desktop_id,
            "kind": desktop["kind"],
            "control": self.leases.holder(desktop_id),
            "task": {"runId": active["id"], "state": active["state"]} if active else None,
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
                payload["button"] = str(request["button"])[:8]
            if request.get("clickCount"):
                payload["clickCount"] = int(request["clickCount"])
        elif kind == "scroll":
            payload["dx"] = float(request.get("dx") or 0)
            payload["dy"] = float(request.get("dy") or 0)
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
