"""What an agent desktop is allowed to touch.

The owner's four answers from the setup screen, stored and revisioned: which
installed apps, which sites, whether changes are asked about, and what the run
may spend. Everything an agent is permitted to do is derived from this document
plus the grants issued against it — there is no other way in, and in particular
there is no switch that means "trust this agent".

Nothing here inherits. A desktop with no policy allows nothing, which is the
right answer for a workspace whose owner has not yet said otherwise.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import DesktopError
from .site_policy import SITE_EFFECT_MODES

#: How changes are handled. `ask` pauses at every effect for the owner; `granted`
#: additionally allows the specific action scopes listed below without asking
#: each time. Neither is "allow everything" — that is not one of the choices.
APPROVAL_MODES = ("ask", "granted")

#: Proposed starting budgets, from section 2.6 of the plan. They are numbers to
#: measure and tune, not claims about what any machine can do.
DEFAULT_BUDGET = {"steps": 60, "activeSeconds": 900, "modelRequests": 120}
MAX_BUDGET = {"steps": 2000, "activeSeconds": 21600, "modelRequests": 4000}

_APP_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_ACTION_ID = re.compile(r"^[a-z][a-z0-9-]*$")

#: Bound on the document so a malformed policy cannot grow the file.
MAX_APPS = 64
MAX_SITES = 64
MAX_SCOPES = 128


def empty_policy() -> dict[str, Any]:
    """A desktop nobody has configured: nothing allowed, nothing asked for."""
    return {
        "revision": 0,
        "apps": [],
        "sites": [],
        "approvals": "ask",
        "actionScopes": [],
        "budget": dict(DEFAULT_BUDGET),
        "rememberSessions": False,
    }


def validate_policy(value: Any) -> dict[str, Any]:
    """Check a policy document, returning what may be stored.

    Refuses rather than repairs. A policy is the answer to "what may this thing
    touch", and quietly interpreting a malformed one is how the answer ends up
    being something nobody chose.
    """
    if not isinstance(value, dict):
        raise DesktopError(422, "A policy is an object.")
    out: dict[str, Any] = {}

    apps = value.get("apps", [])
    if not isinstance(apps, list) or len(apps) > MAX_APPS:
        raise DesktopError(422, f"A desktop allows at most {MAX_APPS} apps.")
    seen: set[str] = set()
    for app_id in apps:
        if not isinstance(app_id, str) or not _APP_ID.match(app_id):
            raise DesktopError(422, "An allowed app is named by its id.")
        seen.add(app_id)
    out["apps"] = sorted(seen)

    sites = value.get("sites", [])
    if not isinstance(sites, list) or len(sites) > MAX_SITES:
        raise DesktopError(422, f"A desktop allows at most {MAX_SITES} sites.")
    out["sites"] = [_site(entry) for entry in sites]

    approvals = value.get("approvals", "ask")
    if approvals not in APPROVAL_MODES:
        raise DesktopError(422, "Changes are either asked about or specifically granted.")
    out["approvals"] = approvals

    scopes = value.get("actionScopes", [])
    if not isinstance(scopes, list) or len(scopes) > MAX_SCOPES:
        raise DesktopError(422, f"A desktop holds at most {MAX_SCOPES} granted actions.")
    out["actionScopes"] = [_scope(entry) for entry in scopes]

    out["budget"] = _budget(value.get("budget"))

    # Whether a website login survives the browser being closed. Off by design:
    # a signed-in session that outlives the task is a credential sitting on the
    # disk, and turning it on is a decision somebody makes once, deliberately.
    out["rememberSessions"] = bool(value.get("rememberSessions"))

    # An action scope for an app the desktop does not allow is a contradiction,
    # and storing it would leave a permission that reappears the moment somebody
    # adds the app back.
    allowed = set(out["apps"])
    for scope in out["actionScopes"]:
        if scope["app"] not in allowed:
            raise DesktopError(
                422, f"{scope['app']} has a granted action but is not an allowed app."
            )

    if len(json.dumps(out)) > 20000:
        raise DesktopError(422, "That policy is too large to store.")
    return out


def _site(entry: Any) -> dict[str, Any]:
    """One approved origin, as an origin rather than as a string to match.

    `example.com` is a decision about example.com. Subdomains are opted into one
    rule at a time, because "everything under this name" is a different and much
    larger decision than the one someone thinks they are making.
    """
    rule = {"origin": entry} if isinstance(entry, str) else entry
    if not isinstance(rule, dict):
        raise DesktopError(422, "An approved site is an origin.")
    effects = rule.get("effects", "read")
    if effects not in SITE_EFFECT_MODES:
        raise DesktopError(
            422, "A site is either read-only for the agent, or one where changes are asked about."
        )
    origin = rule.get("origin")
    if not isinstance(origin, str) or not origin.startswith(("http://", "https://")):
        raise DesktopError(422, "An approved site needs an http or https origin.")
    if len(origin) > 300:
        raise DesktopError(422, "That origin is too long.")
    # Normalised here so two spellings of one site cannot be two rules.
    trimmed = origin.rstrip("/")
    if trimmed.count("/") != 2:
        raise DesktopError(422, "An approved site is an origin, not a page.")
    return {
        "origin": trimmed.lower(),
        "includeSubdomains": bool(rule.get("includeSubdomains")),
        # What the agent may cause on this site, as opposed to what it may read.
        # `read` is the default because approving a site to look at is not the
        # same decision as approving it to act on somebody's behalf.
        "effects": effects,
    }


def _scope(entry: Any) -> dict[str, Any]:
    """One named action the owner granted without wanting to be asked again."""
    if not isinstance(entry, dict):
        raise DesktopError(422, "A granted action names an app and an action.")
    app_id, action = entry.get("app"), entry.get("action")
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise DesktopError(422, "A granted action names an installed app.")
    if not isinstance(action, str) or not _ACTION_ID.match(action):
        raise DesktopError(422, "A granted action names one of that app's actions.")
    return {"app": app_id, "action": action}


def _budget(value: Any) -> dict[str, int]:
    if value is None:
        return dict(DEFAULT_BUDGET)
    if not isinstance(value, dict):
        raise DesktopError(422, "A budget is an object.")
    out = dict(DEFAULT_BUDGET)
    for key, ceiling in MAX_BUDGET.items():
        if key not in value:
            continue
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise DesktopError(422, f"{key} is a whole number of at least one.")
        if number > ceiling:
            raise DesktopError(422, f"{key} is at most {ceiling}.")
        out[key] = number
    return out


def allows_app(policy: dict[str, Any], app_id: str) -> bool:
    return app_id in (policy.get("apps") or [])


def site_rule(policy: dict[str, Any], origin: str) -> dict[str, Any] | None:
    """The rule an origin matched, or None. Compared as an origin, not as text.

    Returning the rule rather than a boolean is what lets the effect decision
    and the navigation decision read the same row: whether a site may be opened
    and what may be done on it are two answers from one place.
    """
    if not isinstance(origin, str):
        return None
    target = origin.rstrip("/").lower()
    for rule in policy.get("sites") or []:
        if target == rule["origin"]:
            return rule
        if rule.get("includeSubdomains"):
            scheme, _, host = rule["origin"].partition("://")
            if target.startswith(f"{scheme}://") and target.endswith(f".{host}"):
                return rule
    return None


def allows_site(policy: dict[str, Any], origin: str) -> bool:
    """Whether an origin is approved at all."""
    return site_rule(policy, origin) is not None


def granted_action(policy: dict[str, Any], app_id: str, action_id: str) -> bool:
    """Whether this exact action was granted ahead of time.

    Only consulted in `granted` mode. In `ask` mode every change pauses for the
    owner regardless of what is listed, which is what "Ask before changes"
    means.
    """
    if policy.get("approvals") != "granted":
        return False
    return any(
        scope["app"] == app_id and scope["action"] == action_id
        for scope in policy.get("actionScopes") or []
    )
