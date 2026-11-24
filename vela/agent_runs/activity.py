"""What a desktop is doing right now, in one short line.

A controller showing its bot's computer in a chat preview needs one sentence —
"Running npm run dev", "Clicking 'Re-run failed jobs'" — and the event stream
is too much to read for that. This is that sentence: set from the step that is
actually running, held while the run is working, and gone when the run is
idle, paused or over.

Two rules keep it honest.

**The line is derived, never invented.** Every text comes from the tool being
called and the arguments it was called with — the command for `terminal_run`,
the path for `workspace_write`, the control's own accessible name for a click
when the last observation named one. A step this file has no words for is
reported by its tool name rather than by a guess.

**The line expires.** A step may run for a minute and a half, so the line
outlives the longest step; a controller that died mid-step does not get to
leave "Clicking…" up forever. The read also gates on the run still working, so
a paused or waiting run shows nothing even inside that window.
"""

from __future__ import annotations

import threading
import time
from typing import Any

#: How long a line may go unrefreshed before the read stops believing it.
#: Longer than the longest a single step may run (90 seconds), so a working
#: step never flickers out mid-call; short enough that a controller that
#: vanished mid-step does not leave its last line up for good.
ACTIVITY_TTL_SECONDS = 150

#: Longest line kept. The preview is one line; a command or a control name is
#: clipped to fit rather than allowed to become a paragraph.
MAX_ACTIVITY_CHARS = 120

#: Run states in which a desktop is doing something. A run waiting for an
#: approval, paused for a person or finished is idle, and idle shows nothing.
WORKING_STATES = ("starting", "running")


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_ACTIVITY_CHARS else text[: MAX_ACTIVITY_CHARS - 1] + "…"


def describe_step(
    tool: str, arguments: dict[str, Any] | None = None, *, names: dict[str, str] | None = None
) -> str:
    """One step, as a person would describe watching it.

    `names` maps a control reference to the accessible name the last
    observation gave it, so a click can say what it is clicking rather than
    which reference it holds. Everything else comes from the arguments
    themselves. The vocabulary deliberately matches the dashboard's activity
    words (`web/src/desktops/TaskActivity.jsx`), with the specifics filled in.
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    names = names or {}

    def named(fallback: str) -> str:
        name = names.get(str(arguments.get("ref") or ""))
        return f"{fallback} '{_clip(name)}'" if name else fallback

    if tool == "desktop.observe":
        return "Looking at the window"
    if tool == "desktop.open_app":
        app_id = arguments.get("appId")
        return _clip(f"Opening {app_id}") if app_id else "Opening an app"
    if tool == "desktop.open_site":
        url = str(arguments.get("url") or "")
        origin = "/".join(url.split("/")[:3]) if url.startswith(("http://", "https://")) else ""
        return _clip(f"Opening {origin}") if origin else "Opening a website"
    if tool == "desktop.select_view":
        return "Switching window"
    if tool == "desktop.click":
        return named("Clicking")
    if tool == "desktop.type":
        return named("Typing into")
    if tool == "desktop.keypress":
        key = arguments.get("key")
        return _clip(f"Pressing {key}") if key else "Pressing a key"
    if tool == "desktop.scroll":
        return "Scrolling"
    if tool == "desktop.wait":
        return "Waiting for the page"
    if tool == "desktop.attach_file":
        return "Attaching a file"
    if tool == "task.needs_person":
        return "Asking you to take over"
    if tool == "app.invoke_action":
        app_id, action_id = arguments.get("appId"), arguments.get("actionId")
        if app_id and action_id:
            return _clip(f"Running {app_id}.{action_id}")
        return "Asking the app to do something"
    if tool == "task.finish":
        return "Wrapping up"
    if tool == "terminal_open":
        return "Opening a terminal"
    if tool == "terminal_run":
        command = arguments.get("command")
        return _clip(f"Running {command}") if command else "Running a command"
    if tool == "terminal_read":
        return "Reading the terminal"
    if tool == "terminal_write":
        return "Typing in the terminal"
    if tool == "terminal_close":
        return "Closing a terminal"
    if tool == "workspace_list":
        return "Listing workspace files"
    if tool == "workspace_read":
        path = arguments.get("path")
        return _clip(f"Reading {path}") if path else "Reading a workspace file"
    if tool == "workspace_write":
        path = arguments.get("path")
        return _clip(f"Writing {path}") if path else "Writing a workspace file"
    if tool == "editor_open":
        path = arguments.get("path")
        return _clip(f"Opening {path} in the editor") if path else "Opening the editor"
    # A tool with no words of its own is still reported truthfully: by name.
    return _clip(tool)


class Activity:
    """The current line for every desktop, and when it was set.

    In memory, like the steps it describes: a restart ends every run that was
    working, and a line about a run that no longer exists is a line about
    nothing. Guarded by a lock because steps arrive on the server's loop while
    reads arrive on any worker thread.
    """

    def __init__(self):
        self._lock = threading.Lock()
        #: desktop id -> {"text", "at", "viewId", "runId"}.
        self._all: dict[str, dict[str, Any]] = {}

    def note(
        self,
        desktop_id: str,
        text: str,
        *,
        view_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """The desktop is doing this now. Refreshes the expiry by existing."""
        record = {
            "text": _clip(text),
            "at": time.time(),
            "viewId": view_id,
            "runId": run_id,
        }
        with self._lock:
            self._all[desktop_id] = record
        return dict(record)

    def current(self, desktop_id: str) -> dict[str, Any] | None:
        """The line, or None once it has gone unrefreshed for too long."""
        with self._lock:
            record = self._all.get(desktop_id)
            if record is None:
                return None
            if time.time() - record["at"] > ACTIVITY_TTL_SECONDS:
                del self._all[desktop_id]
                return None
            return dict(record)

    def clear(self, desktop_id: str, *, run_id: str | None = None) -> bool:
        """The desktop went idle. With a run id, only that run's line goes."""
        with self._lock:
            record = self._all.get(desktop_id)
            if record is None:
                return False
            if run_id is not None and record["runId"] != run_id:
                return False
            del self._all[desktop_id]
            return True

    def drop(self, desktop_id: str) -> None:
        """The desktop is gone; nothing it was doing is worth remembering."""
        with self._lock:
            self._all.pop(desktop_id, None)
