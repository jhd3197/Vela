"""Who is asking, and what that entitles them to.

Vela already had three kinds of caller: the owner at the dashboard, an installed
app in its iframe, and an automation running a reviewed workflow. An agent is a
fourth, and it is deliberately not any of the others. It is not the owner —
owner authentication is what approves things, and an agent that could approve
its own effects would make approval meaningless. It is not an ordinary app
session either: an app session carries everything its manifest declares, for an
hour, and an agent gets only what one run on one desktop needs, briefly.

The binding is the point. An agent session names the desktop, the run, the view
and the installation it was issued for, and every one of those is checked again
when an effect is about to commit. A token that outlives the run it belonged to,
or that could be used on another desktop, would be exactly the thing this file
exists to prevent.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..app_storage import AppServiceError

#: How long an agent's app session lasts. Short on purpose: an ordinary app
#: session runs for an hour because a person is sitting in front of it, and a
#: run that needs longer refreshes, which revalidates it.
AGENT_SESSION_SECONDS = 300

#: The kinds of caller the effect boundary knows about.
PRINCIPAL_KINDS = ("owner", "app", "agent", "automation")

_ID = re.compile(r"^[0-9a-zA-Z_-]{1,64}$")


@dataclass(frozen=True)
class AgentPrincipal:
    """An agent run, addressed precisely enough to be revoked."""

    desktop_id: str
    run_id: str
    view_id: str | None = None
    actor_id: str | None = None
    policy_revision: int = 0
    #: Monotonic deadline. Compared with `time.monotonic()`, like app sessions.
    expires: float = 0.0
    scopes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "desktopId": self.desktop_id,
            "runId": self.run_id,
            "viewId": self.view_id,
            "actorId": self.actor_id,
            "policyRevision": self.policy_revision,
            "scopes": list(self.scopes),
        }

    @property
    def expired(self) -> bool:
        return self.expires <= time.monotonic()


def validate_identifier(value: Any, *, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise AppServiceError(422, f"That is not a valid {what}.")
    return value


def agent_of(session: Any) -> AgentPrincipal | None:
    """The agent behind an app session, or None when a person is.

    Everything that can change data takes a session; this is how each of those
    places asks "is this a person clicking, or a run acting" without any of them
    having to know how a session is put together.
    """
    if not isinstance(session, dict):
        return None
    raw = session.get("agent")
    if not isinstance(raw, dict):
        return None
    return AgentPrincipal(
        desktop_id=raw["desktopId"],
        run_id=raw["runId"],
        view_id=raw.get("viewId"),
        actor_id=raw.get("actorId"),
        policy_revision=int(raw.get("policyRevision") or 0),
        expires=float(raw.get("expires") or 0.0),
        scopes=tuple(raw.get("scopes") or ()),
    )


def kind_of(session: Any) -> str:
    """`owner`, `app` or `agent` for a resolved session."""
    if not isinstance(session, dict):
        return "owner"
    return "agent" if agent_of(session) else "app"
