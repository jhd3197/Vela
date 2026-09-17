"""The narrow door an agent's app session may knock on.

An ordinary app session can reach every `/api/app` route its manifest entitles
it to, for an hour. An agent's session reaches the same routes, briefly, and
only after this has agreed — so there is one place to read to know what a run
can touch, rather than a check in each handler and the hope that a new handler
remembers to have one.

Two things it refuses without thinking about policy at all. A route nobody has
classified in `effects.py`: an unclassified route has no effect class, so there
is nothing to grant, so the answer is no. And anything outside `/api/app` — the
owner's dashboard, the desktop configuration, the approval controls — which an
agent session cannot reach anyway because those want a hub token and this is not
one, but which is worth refusing here too rather than relying on that.

The coarse question is answered here: is this operation a thing, is the app
allowed on this desktop, is the session still the session it claims to be. The
exact question — is *this* change, with *these* values, allowed — is answered
inside the transaction that writes it, because that is the only place where the
answer cannot go stale between being given and being used.
"""

from __future__ import annotations

from ..app_storage import AppServiceError
from .effects import EFFECTFUL, classify
from .principals import agent_of
from .policy import allows_app

#: Everything an agent session may address, as a path prefix. Short list on
#: purpose.
APP_PREFIX = "/api/app"


class Gateway:
    """Decides whether an agent-bound app session may make a request."""

    def __init__(self, desktops):
        self.desktops = desktops

    def check(self, session, method: str, path: str) -> dict | None:
        """Refuse, or return what this request was understood to be.

        Returns None for a request from a person, which this has no opinion
        about: an app session with no agent behind it is the owner using their
        own computer.
        """
        principal = agent_of(session)
        if principal is None:
            return None

        if not path.startswith(APP_PREFIX):
            # Not reachable in practice — everything else wants a hub token —
            # but refusing here means that stays true if the middleware changes.
            raise AppServiceError(403, "An agent session cannot reach the Vela dashboard.")

        if principal.expired:
            raise AppServiceError(401, "This agent session has expired.")

        classified = classify(method, path[len(APP_PREFIX) :])
        if classified is None:
            # A route added without a thought about agents is refused, not
            # waved through. That default is the point of the list.
            raise AppServiceError(403, "That operation is not available to an agent.")
        operation, effect = classified

        policy = self.desktops.policy(principal.desktop_id)
        app_id = session.get("app_id")
        if not allows_app(policy, app_id):
            raise AppServiceError(
                403, f"This desktop is not allowed to use {app_id}."
            )
        if policy["revision"] != principal.policy_revision:
            # The owner changed what this desktop may touch. The session was
            # issued under the old answer, so it stops here and a new one has to
            # be issued under the new one.
            raise AppServiceError(403, "This desktop's permissions changed. Reopen the app.")

        return {
            "operation": operation,
            "effect": effect,
            "needsGrant": effect in EFFECTFUL,
            "desktopId": principal.desktop_id,
            "runId": principal.run_id,
        }
