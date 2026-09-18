"""What an agent desktop keeps, and for how long.

Four kinds of thing accumulate while a desktop works: the record of a task, the
stream of events under it, the pictures taken of a view, and the files a task was
given or came back with. They have different lifetimes on purpose and they are
swept from one place on purpose — a cleanup scattered across four modules is
four places for something to be forgotten.

The classes, shortest first:

**Frames** are a picture of somebody's screen. They exist to be looked at once
and are gone in two minutes. Nothing retains one, nothing backs one up, and a
frame taken for a window animation is not different from one taken for the
viewer: both are transient by class, not by accident.

**Receipts** are how Vela knows whether an effect happened. They are kept for a
day because that is how long reconciling one is useful for, and they hold a
digest and an outcome rather than what was sent — a receipt cannot reconstruct a
conversation somebody chose not to keep.

**Artifacts** are files, and they are the only class a person deliberately put
something into. A week.

**History** — tasks and their events — is the one the owner decides about. With
history off nothing is written down at all, and turning it off removes what was
already there rather than hiding it.

One more rule, and it is the one worth stating plainly: **turning history off
has to reach everywhere.** A task's text removed from a database but left in a
generated filename, a log line, an event replay cache or a support bundle is a
task that was not really forgotten.
"""

from __future__ import annotations

import time
from typing import Any

#: A picture of a view. Transient by class.
FRAME_SECONDS = 120

#: The record that an effect happened, for reconciling an uncertain one.
RECEIPT_SECONDS = 24 * 3600

#: A staged or downloaded file.
ARTIFACT_SECONDS = 7 * 24 * 3600

#: What each class is, in one line each, for the interface and the documentation
#: to read from rather than restate.
CLASSES = (
    {
        "id": "frames",
        "name": "Pictures of a window",
        "seconds": FRAME_SECONDS,
        "backedUp": False,
        "note": "Taken to be looked at now. Never kept, never backed up.",
    },
    {
        "id": "receipts",
        "name": "Records that something happened",
        "seconds": RECEIPT_SECONDS,
        "backedUp": False,
        "note": "A digest and an outcome, so a lost answer can be reconciled.",
    },
    {
        "id": "artifacts",
        "name": "Files",
        "seconds": ARTIFACT_SECONDS,
        "backedUp": False,
        "note": "What you attached and what a task downloaded.",
    },
    {
        "id": "history",
        "name": "Tasks and their activity",
        "seconds": None,
        "backedUp": True,
        "note": "Kept while you keep history, and removed when you turn it off.",
    },
)


class Retention:
    """The sweep, and the account of what it did.

    Run on the way up and periodically after that. Everything it removes is
    something whose class says it should be gone; nothing here decides that
    something has been around long enough to be uninteresting.
    """

    def __init__(self, desktops, runs, *, log=None):
        self.desktops = desktops
        self.runs = runs
        self._log = log or (lambda message: None)
        self.last: dict[str, Any] | None = None

    def sweep(self) -> dict[str, Any]:
        """Remove what has expired. Safe to call at any moment, including during a task."""
        removed = {"frames": 0, "artifacts": 0, "history": 0}
        problems: list[str] = []

        try:
            # A frame being looked at right now is the newest one and is kept by
            # the viewer's own record, so this cannot delete what a viewer is
            # about to fetch.
            removed["frames"] = self.runs.viewer.sweep()
        except Exception as exc:  # noqa: BLE001 - a sweep never takes the server down
            problems.append(f"pictures: {exc}")

        try:
            # `ingest_download` moves a finished file into the store before it
            # is visible here, so a transfer in progress has nothing in the
            # staging directory for this to find.
            removed["artifacts"] = self.desktops.artifacts.sweep()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"files: {exc}")

        if not self.runs.store.keeping_history:
            try:
                # Not a sweep: a deletion. Somebody turned history off and this
                # is what makes that true of what was already written.
                removed["history"] = self.runs.purge()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"task history: {exc}")

        self.last = {
            "at": time.time(),
            "removed": removed,
            "problems": problems,
            "keepingHistory": self.runs.store.keeping_history,
        }
        total = sum(removed.values())
        if total or problems:
            self._log(
                f"agent desktop cleanup: removed {total} item(s)"
                + (f"; {len(problems)} problem(s)" if problems else "")
            )
        return self.last

    def status(self) -> dict[str, Any]:
        """What is being kept, how much of it there is, and under what rule."""
        try:
            used = self.desktops.artifacts.used()
            limits = self.desktops.artifacts.limits("")
        except Exception:  # noqa: BLE001 - a reading is not worth a failure
            used, limits = 0, {}
        return {
            "classes": [dict(entry) for entry in CLASSES],
            "keepingHistory": self.runs.store.keeping_history,
            "fileBytesUsed": used,
            "fileBytesLimit": limits.get("maxServerBytes"),
            "lastSweep": self.last,
        }
