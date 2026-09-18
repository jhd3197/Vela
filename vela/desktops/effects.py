"""Every bridge operation, classified, and what happened to it.

Two lists and a vocabulary.

The first list says what each operation an app can reach actually *is* — reading
data, changing it, restoring over it, invoking a named action, calling out
through a connection, publishing to the desk. An agent's authority is granted
per class, so an operation nobody has classified has no class to be granted and
is unavailable. That default is the whole reason this file is a list rather than
a set of checks scattered through the handlers: a new route added without a
thought about agents is refused, not waved through.

The second is the vocabulary for how an effect ended. `committed` and `denied`
are the easy ones. `unknown` is the one that matters: an external request whose
answer never arrived is not a failure, and treating it as one is how something
gets sent twice.
"""

from __future__ import annotations

#: What an operation does, from the point of view of authority.
#:
#: `read` is free to any agent whose desktop allows the app at all: looking is
#: what a task is mostly made of. Everything else needs a grant, and `restore`
#: needs its own rather than being covered by `write`, because restoring a
#: backup replaces everything and ordinary permission to save is not permission
#: to do that.
#:
#: `submit` is the one that is not an app operation at all: a request an
#: approved website would receive, described and bound the same way. It is in
#: this list so a site's authority is a row in the same table, expiring and
#: revoked by the same rules, rather than a second permission system nobody
#: audits.
EFFECT_CLASSES = ("read", "write", "restore", "action", "connection", "publish", "submit")

#: Classes that change something and therefore need authority beyond "this app
#: is allowed on this desktop".
EFFECTFUL = ("write", "restore", "action", "connection", "publish", "submit")

#: Every operation an app session can reach, and what it is.
#:
#: Keyed by `(method, path)` with the app-scoped prefix already removed, so the
#: gateway can answer from the request alone. Paths with a variable segment use
#: `{}` for it.
OPERATIONS = {
    ("DELETE", "/session"): ("session.close", "read"),
    # What the app's own SDK says it can do. Read-only in every sense: it can
    # only ever make Vela more careful, never more permissive.
    ("POST", "/features"): ("session.features", "read"),
    ("GET", "/storage"): ("storage.read", "read"),
    ("PUT", "/storage"): ("storage.write", "write"),
    ("GET", "/storage/snapshots"): ("storage.snapshots", "read"),
    ("POST", "/storage/snapshots"): ("storage.snapshot", "write"),
    ("POST", "/storage/snapshots/{}/restore"): ("storage.restore", "restore"),
    ("GET", "/actions"): ("actions.status", "read"),
    ("POST", "/actions/invoke"): ("actions.invoke", "action"),
    # Asking about a change this app is already waiting on, and asking for more
    # time to wait. Both are `read`: neither changes anything, and needing a
    # grant to ask about a grant would be a circle.
    ("GET", "/approvals/{}"): ("approvals.status", "read"),
    ("POST", "/approvals/{}/extend"): ("approvals.extend", "read"),
    ("POST", "/approvals/{}/abandon"): ("approvals.abandon", "read"),
    ("GET", "/connection"): ("connection.read", "read"),
    ("POST", "/connection/invoke"): ("connection.invoke", "connection"),
    ("PUT", "/widgets/{}"): ("widget.publish", "publish"),
}

#: How an effect ended. The distinction between the last three is what stops
#: something being sent twice.
OUTCOMES = (
    #: Refused before anything was attempted.
    "not_dispatched",
    #: Reached the boundary and was refused there.
    "denied",
    #: It happened, and there is a receipt saying so.
    "committed",
    #: It was attempted and failed before changing anything.
    "failed_before_commit",
    #: It was attempted and nobody can say whether it changed anything. Not a
    #: failure. Never a reason to try again on its own.
    "unknown",
)


def classify(method: str, path: str) -> tuple[str, str] | None:
    """The operation a request is, or None if nothing has classified it.

    `path` is what follows `/api/app`. None is not "allow": the caller refuses,
    which is what makes an unclassified route unavailable rather than open.
    """
    method = (method or "").upper()
    parts = [part for part in (path or "").split("/") if part]
    for (candidate_method, pattern), value in OPERATIONS.items():
        if candidate_method != method:
            continue
        expected = [part for part in pattern.split("/") if part]
        if len(expected) != len(parts):
            continue
        if all(want == "{}" or want == got for want, got in zip(expected, parts)):
            return value
    return None
