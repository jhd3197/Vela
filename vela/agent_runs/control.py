"""Who is allowed to type into a desktop right now.

One writer. Not "usually one", not "one unless two requests arrive together" —
one, decided by a lock, with everybody else an observer who can see who holds it.
Two things typing into the same window is the failure this file exists to
prevent, and it is not a failure you notice until something has already been
typed into the wrong field.

Taking over is a transition, not a flag:

1. Dispatch stops. The run is told to hold before anything else happens.
2. The control epoch changes. Every command the agent had in flight, and every
   observation it was holding, stops being valid at the worker — not marked
   stale, *invalid*, so a command that arrives late is refused rather than
   applied to a screen a person is now using.
3. The lease is issued, and only then is human input accepted.

Giving it back is deliberately not the reverse. Releasing the lease does not
resume the agent: the person may have released it because they walked away, or
because their connection dropped, and an agent that started typing again because
a network link went down would be an agent doing something nobody asked for. The
run stays paused until somebody says go, and when they do it gets a new epoch
and has to look again before it can act — because the screen it remembers is not
the screen it is now looking at.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from typing import Any

from ..app_storage import AppServiceError

#: How long a lease lives without being used. Short, because the common way a
#: lease ends is a person closing a laptop lid rather than clicking Release.
LEASE_SECONDS = 180

#: How long a frame may be before input aimed at it is refused. A click decided
#: from a five-second-old picture is a click at whatever is there now.
MAX_FRAME_AGE_SECONDS = 10


class Lease:
    """One person's exclusive right to type into one desktop."""

    __slots__ = ("id", "desktop_id", "view_id", "holder", "epoch", "created_at", "touched_at")

    def __init__(self, desktop_id: str, view_id: str | None, holder: str, epoch: int):
        self.id = uuid.uuid4().hex
        self.desktop_id = desktop_id
        self.view_id = view_id
        self.holder = holder
        self.epoch = epoch
        self.created_at = time.time()
        self.touched_at = self.created_at

    @property
    def expired(self) -> bool:
        return time.time() - self.touched_at > LEASE_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "leaseId": self.id,
            "desktopId": self.desktop_id,
            "viewId": self.view_id,
            "holder": self.holder,
            "controlEpoch": self.epoch,
            "expiresAt": self.touched_at + LEASE_SECONDS,
        }


class Leases:
    """Every desktop's writer, and the one lock that decides who it is."""

    def __init__(self):
        self._lock = threading.Lock()
        self._leases: dict[str, Lease] = {}
        #: Desktops whose lease expired and whose expiry nobody has been told
        #: about yet. Drained by `sweep`, so the event goes out once.
        self._expired: set[str] = set()

    def sweep(self) -> list[str]:
        """Desktops whose control has just timed out, reported once each.

        The common way a lease ends is not somebody clicking Release; it is a
        lid closing. Noticing that has to happen somewhere other than the next
        request from the person who is no longer there.
        """
        with self._lock:
            for desktop_id, lease in list(self._leases.items()):
                if lease.expired:
                    del self._leases[desktop_id]
                    self._expired.add(desktop_id)
            expired, self._expired = sorted(self._expired), set()
        return expired

    def holder(self, desktop_id: str) -> dict[str, Any] | None:
        """Who has control here, or None. Every viewer may ask; that is the point.

        An observer that could not see who holds control would be an observer
        who assumes nobody does.
        """
        with self._lock:
            lease = self._leases.get(desktop_id)
            if lease is None:
                return None
            if lease.expired:
                self._leases.pop(desktop_id, None)
                return None
            return lease.as_dict()

    def take(self, desktop_id: str, *, view_id: str | None, epoch: int, holder: str = "") -> Lease:
        """Issue the lease, or refuse because somebody already has it.

        Under one lock, so two requests arriving together produce one writer and
        one refusal rather than two writers who each believe they are alone.
        """
        with self._lock:
            current = self._leases.get(desktop_id)
            if current is not None and not current.expired:
                raise AppServiceError(
                    409,
                    "Somebody else has control of this desktop. They have to give it back, "
                    "or their session has to time out.",
                )
            lease = Lease(desktop_id, view_id, holder or secrets.token_hex(4), epoch)
            self._leases[desktop_id] = lease
            return lease

    def require(self, desktop_id: str, lease_id: str) -> Lease:
        """The lease this input claims to hold, checked before anything is typed."""
        with self._lock:
            lease = self._leases.get(desktop_id)
            if lease is None or lease.id != lease_id:
                raise AppServiceError(
                    409, "You no longer have control of this desktop. Take it again to continue."
                )
            if lease.expired:
                self._leases.pop(desktop_id, None)
                raise AppServiceError(
                    409, "Your control of this desktop timed out. Take it again to continue."
                )
            lease.touched_at = time.time()
            return lease

    def release(self, desktop_id: str, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.get(desktop_id)
            if lease is None or lease.id != lease_id:
                return False
            self._leases.pop(desktop_id, None)
            return True

    def drop(self, desktop_id: str) -> bool:
        """End it regardless of who held it. Used by logout and by Stop."""
        with self._lock:
            return self._leases.pop(desktop_id, None) is not None


def check_point(point: Any, viewport: dict[str, Any]) -> dict[str, float]:
    """A click, in the viewport the frame it was decided from was taken at.

    CSS pixels, like everywhere else. The device pixel ratio belongs to
    rendering; a caller that multiplied by it would click at twice the intended
    place on a dense display, and a viewer scaling a 1280-wide frame into a
    400-wide box has to undo its own scaling rather than have Vela guess at it.
    """
    if not isinstance(point, dict):
        raise AppServiceError(422, "Input needs a point.")
    try:
        x = float(point.get("x"))
        y = float(point.get("y"))
    except (TypeError, ValueError) as exc:
        raise AppServiceError(422, "Input needs a numeric point.") from exc
    width = float(viewport.get("width") or 0)
    height = float(viewport.get("height") or 0)
    if not width or not height:
        raise AppServiceError(409, "Vela does not know how big that view is yet.")
    if x < 0 or y < 0 or x > width or y > height:
        raise AppServiceError(
            422, f"({x:.0f}, {y:.0f}) is outside the {width:.0f}×{height:.0f} view."
        )
    return {"x": x, "y": y}


def check_frame_age(captured_at: Any) -> None:
    """Refuse input decided from a picture that is too old to be about now."""
    try:
        age = time.time() - float(captured_at) / 1000.0
    except (TypeError, ValueError) as exc:
        raise AppServiceError(422, "Input has to say which frame it was aimed at.") from exc
    if age > MAX_FRAME_AGE_SECONDS:
        raise AppServiceError(
            409,
            "That was decided from a picture more than "
            f"{MAX_FRAME_AGE_SECONDS} seconds old. Wait for a fresh one.",
        )
