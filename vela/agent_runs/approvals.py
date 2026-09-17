"""Asking a person, and waiting for a real answer.

The app bridge gives a request ten seconds. A person does not answer in ten
seconds — they are in another window, or in another room, or looking at the
change and thinking about it. So an effect that needs approval does not block:
it becomes a pending request with a name, a plain-language summary and an
expiry, the app is told it is waiting rather than told it failed, and the answer
arrives whenever the owner gives one.

Four rules hold this together.

**A pending request is not an effect.** Nothing is written while one is open.
Approving issues a grant bound to that exact change; the effect then commits by
the ordinary path, through the same transaction and the same check as always.
There is no second route into a write.

**It is bound to the change it describes.** The app, the installation, the
manifest fingerprint, the effect class and the digest of the exact request. Edit
the value after the prompt appears and the old approval no longer matches it —
approving "save these three notes" cannot commit a different three.

**It expires, absolutely.** Extensions are bounded and the absolute deadline
cannot be pushed past its ceiling. A prompt somebody left open over a weekend is
not a standing permission.

**Cancellation is final.** A closed app, a stopped task, a changed policy or a
lost runtime cancels the request, and a click that arrives afterwards resolves
nothing. The alternative — a decision reviving a write whose context is gone —
is the exact failure this design exists to prevent.

Held in memory on purpose. A pending approval that survived a restart would be
authority for an effect whose run, view and browser are all gone.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from ..app_storage import AppServiceError

#: How long a request waits before it expires on its own.
APPROVAL_SECONDS = 300

#: The ceiling no amount of extension can pass. Twenty minutes is long enough
#: for somebody to come back to their computer and short of "indefinitely".
MAX_APPROVAL_SECONDS = 1200

#: One extension's worth of extra time, and how many an app may ask for.
EXTENSION_SECONDS = 120
MAX_EXTENSIONS = 6

#: How long the grant an approval issues lasts. Long enough for the app to be
#: told and to retry, short enough that a grant nobody used is gone quickly.
APPROVED_GRANT_SECONDS = 120

#: A deliberately scoped "and next time too" lasts an hour, not a day.
SCOPED_GRANT_SECONDS = 3600

#: Most pending requests one desktop may hold. A run that could open them
#: without limit could bury a real one in noise.
MAX_PENDING_PER_DESKTOP = 8

STATES = ("pending", "approved", "denied", "expired", "cancelled")

#: Field names whose values are never repeated back in a summary. A prompt is
#: read by a person and may be screenshotted, logged or narrated aloud.
SECRET_HINTS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "credential",
    "private",
    "otp",
    "pin",
    "seed",
    "mnemonic",
)


class ApprovalPending(Exception):
    """This effect is waiting for the owner, and nothing has been written.

    Not an `AppServiceError`: a failure and a question are different answers and
    collapsing them is how an app learns to treat "wait" as "no".
    """

    def __init__(self, record: dict[str, Any]):
        super().__init__("This change is waiting for approval.")
        self.record = record

    @property
    def request_id(self) -> str:
        return self.record["requestId"]


class Approvals:
    """Every pending request on this server, and what became of each one."""

    def __init__(self, grants, *, log=None):
        self.grants = grants
        self._log = log or (lambda message: None)
        self._lock = threading.Lock()
        #: request id -> record. Resolved records are kept briefly so a host
        #: that was asking can be told what happened rather than finding
        #: nothing, which reads the same as "still waiting".
        self._records: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------- requesting --

    def request(
        self,
        *,
        desktop_id: str,
        run_id: str | None,
        view_id: str | None,
        effect: str,
        app_id: str,
        app_name: str,
        installation_id: str,
        contract: str,
        request_digest: str,
        scope: dict[str, Any] | None,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Open a pending request, or return the identical one already open.

        Deduplicated by everything that binds it. An app retrying the same write
        while the owner is deciding should find the same prompt, not add another
        one to the pile.
        """
        now = time.time()
        with self._lock:
            self._sweep(now)
            for record in self._records.values():
                if record["state"] != "pending":
                    continue
                if (
                    record["desktopId"] == desktop_id
                    and record["runId"] == run_id
                    and record["effect"] == effect
                    and record["appId"] == app_id
                    and record["installationId"] == installation_id
                    and record["contract"] == contract
                    and record["requestDigest"] == request_digest
                    and record["scope"] == (scope or {})
                ):
                    return _public(record)
            open_here = sum(
                1
                for record in self._records.values()
                if record["state"] == "pending" and record["desktopId"] == desktop_id
            )
            if open_here >= MAX_PENDING_PER_DESKTOP:
                raise AppServiceError(
                    429,
                    "This desktop already has as many changes waiting for you as it "
                    "can hold. Answer one of them first.",
                )
            record = {
                "requestId": str(uuid.uuid4()),
                "desktopId": desktop_id,
                "runId": run_id,
                "viewId": view_id,
                "effect": effect,
                "appId": app_id,
                "appName": app_name,
                "installationId": installation_id,
                "contract": contract,
                "requestDigest": request_digest,
                "scope": scope or {},
                "summary": summary,
                "state": "pending",
                "createdAt": now,
                "expiresAt": now + APPROVAL_SECONDS,
                # The line that cannot move. Extensions push `expiresAt` toward
                # it and never past it.
                "absoluteExpiry": now + MAX_APPROVAL_SECONDS,
                "extensions": 0,
                "resolvedAt": None,
                "resolution": None,
                "reason": None,
                "grantId": None,
            }
            self._records[record["requestId"]] = record
            self._log(
                f"approval requested: {app_id} {effect} on desktop {desktop_id} "
                f"({record['requestId']})"
            )
            return _public(record)

    # ------------------------------------------------------------ reading --

    def get(self, request_id: str, *, desktop_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._sweep(time.time())
            record = self._records.get(request_id)
            if record is None or (desktop_id is not None and record["desktopId"] != desktop_id):
                raise AppServiceError(404, "That request is no longer waiting for an answer.")
            return _public(record)

    def pending(self, desktop_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._sweep(time.time())
            return [
                _public(record)
                for record in self._records.values()
                if record["desktopId"] == desktop_id and record["state"] == "pending"
            ]

    # ---------------------------------------------------------- extending --

    def extend(self, request_id: str, *, desktop_id: str | None = None) -> dict[str, Any]:
        """Give the owner more time, within limits neither side chooses.

        The app asks, because the app is the one that would otherwise give up.
        What it cannot do is ask forever: each extension is bounded, the number
        of them is bounded, and the absolute deadline is the wall.
        """
        with self._lock:
            now = time.time()
            self._sweep(now)
            record = self._records.get(request_id)
            if record is None or (desktop_id is not None and record["desktopId"] != desktop_id):
                raise AppServiceError(404, "That request is no longer waiting for an answer.")
            if record["state"] != "pending":
                return _public(record)
            if record["extensions"] >= MAX_EXTENSIONS:
                return _public(record)
            record["extensions"] += 1
            record["expiresAt"] = min(
                record["expiresAt"] + EXTENSION_SECONDS, record["absoluteExpiry"]
            )
            return _public(record)

    # ---------------------------------------------------------- resolving --

    def resolve(
        self,
        request_id: str,
        decision: str,
        *,
        desktop_id: str | None = None,
        expected_digest: str | None = None,
        scope_future: bool = False,
    ) -> dict[str, Any]:
        """The owner's answer.

        `expected_digest` is what the person was looking at when they clicked.
        It has to still be the request's own digest, so a prompt that was
        replaced between being rendered and being answered resolves nothing.
        """
        if decision not in ("approve", "deny"):
            raise AppServiceError(422, "An approval is either approved or denied.")
        with self._lock:
            now = time.time()
            self._sweep(now)
            record = self._records.get(request_id)
            if record is None or (desktop_id is not None and record["desktopId"] != desktop_id):
                raise AppServiceError(404, "That request is no longer waiting for an answer.")
            if record["state"] != "pending":
                raise AppServiceError(
                    409,
                    f"That request was already {record['state']}"
                    + (f": {record['reason']}" if record["reason"] else "."),
                )
            if expected_digest is not None and expected_digest != record["requestDigest"]:
                raise AppServiceError(
                    409,
                    "What was asked for has changed since this was shown to you. "
                    "Look at it again.",
                )
            if decision == "deny":
                record.update(
                    state="denied", resolvedAt=now, resolution="denied", reason="you said no"
                )
                self._log(f"approval denied: {record['appId']} ({request_id})")
                return _public(record)

            # Approving is issuing a grant, and nothing else. The effect commits
            # through its own transaction, where the grant is checked again.
            grant = self.grants.issue(
                desktop_id=record["desktopId"],
                effect=record["effect"],
                app_id=record["appId"],
                installation_id=record["installationId"],
                contract=record["contract"],
                run_id=record["runId"],
                # A one-off approval names the exact request. A scoped one
                # deliberately does not, and says so to the person granting it.
                request_digest=None if scope_future else record["requestDigest"],
                scope=record["scope"] or None,
                seconds=SCOPED_GRANT_SECONDS if scope_future else APPROVED_GRANT_SECONDS,
            )
            record.update(
                state="approved",
                resolvedAt=now,
                resolution="approved_scope" if scope_future else "approved_once",
                grantId=grant["id"],
            )
            self._log(
                f"approval granted: {record['appId']} {record['effect']} "
                f"({'scoped' if scope_future else 'once'}, {request_id})"
            )
            return _public(record)

    # ------------------------------------------------------- cancellation --

    def cancel(
        self,
        *,
        desktop_id: str | None = None,
        run_id: str | None = None,
        view_id: str | None = None,
        request_id: str | None = None,
        app_id: str | None = None,
        reason: str = "it was cancelled",
    ) -> int:
        """Stop waiting, permanently.

        A cancelled request cannot be approved afterwards. That is the whole
        point: the app that asked is gone, or the task is stopped, or the policy
        changed, and a decision arriving now would authorize an effect whose
        reason for existing has gone with it.
        """
        cancelled = 0
        with self._lock:
            now = time.time()
            for record in self._records.values():
                if record["state"] != "pending":
                    continue
                if request_id is not None and record["requestId"] != request_id:
                    continue
                if desktop_id is not None and record["desktopId"] != desktop_id:
                    continue
                if run_id is not None and record["runId"] != run_id:
                    continue
                if view_id is not None and record["viewId"] != view_id:
                    continue
                # An app that was removed, replaced or updated. The question was
                # about code that is no longer installed, and an answer to it
                # would authorize an effect against something else.
                if app_id is not None and record["appId"] != app_id:
                    continue
                record.update(
                    state="cancelled", resolvedAt=now, resolution="cancelled", reason=reason
                )
                cancelled += 1
        if cancelled:
            self._log(f"{cancelled} pending approval(s) cancelled: {reason}")
        return cancelled

    def forget_desktop(self, desktop_id: str) -> None:
        with self._lock:
            for key in [
                key
                for key, record in self._records.items()
                if record["desktopId"] == desktop_id
            ]:
                self._records.pop(key, None)

    # ----------------------------------------------------------- internal --

    def _sweep(self, now: float) -> None:
        """Expire what ran out, and drop what nobody needs to be told about.

        A resolved record is kept for five minutes so the host that was waiting
        finds out what happened. After that it goes: a decision nobody collected
        is not a decision worth storing.
        """
        for record in list(self._records.values()):
            if record["state"] == "pending" and now >= record["expiresAt"]:
                record.update(
                    state="expired",
                    resolvedAt=now,
                    resolution="expired",
                    reason="nobody answered in time",
                )
            if record["state"] != "pending" and record["resolvedAt"] and now - record["resolvedAt"] > 300:
                self._records.pop(record["requestId"], None)


def _public(record: dict[str, Any]) -> dict[str, Any]:
    """What anyone outside this module sees.

    The installation and the manifest fingerprint stay in: they are what the
    binding is made of and the owner surface shows whether an app was replaced.
    Nothing here carries a token, a path or a field value that was not already
    cleared for the summary.
    """
    return {
        "requestId": record["requestId"],
        "desktopId": record["desktopId"],
        "runId": record["runId"],
        "viewId": record["viewId"],
        "effect": record["effect"],
        "appId": record["appId"],
        "appName": record["appName"],
        "installationId": record["installationId"],
        "requestDigest": record["requestDigest"],
        "scope": record["scope"],
        "summary": record["summary"],
        "state": record["state"],
        "createdAt": record["createdAt"],
        "expiresAt": record["expiresAt"],
        "absoluteExpiry": record["absoluteExpiry"],
        "extensions": record["extensions"],
        "resolution": record["resolution"],
        "reason": record["reason"],
    }


# ------------------------------------------------------------ summaries --

#: Most individual changes a prompt lists before it starts counting instead.
MAX_CHANGES = 10

#: How deep the comparison goes. Past this, a change is reported as "something
#: under here changed" rather than pretended to be understood.
MAX_DEPTH = 4

#: Longest value repeated back to the person reading the prompt.
MAX_VALUE = 80


def redact(key: Any, value: Any) -> str:
    """One value, as a prompt may say it.

    A field whose name suggests a secret is described by its shape and never by
    its contents. A prompt is read by a person and may be screenshotted, logged
    or read aloud, and none of those are places for a password.
    """
    name = str(key or "").lower()
    if any(hint in name for hint in SECRET_HINTS):
        return "(hidden)"
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.replace("\n", " ").strip()
        return f'"{text[:MAX_VALUE]}…"' if len(text) > MAX_VALUE else f'"{text}"'
    if isinstance(value, list):
        return f"a list of {len(value)}"
    if isinstance(value, dict):
        return f"{len(value)} field{'s' if len(value) != 1 else ''}"
    return "something"


def changes(current: Any, proposal: Any) -> tuple[list[str], bool]:
    """What is different, in the words somebody would use.

    Returns the lines and whether they are the whole story. Beyond the limits a
    prompt says how much changed rather than listing it, which is still true —
    "seven things under notes changed" is a description, "here are three of
    seven" pretending to be all of them is not.
    """
    lines: list[str] = []
    complete = True

    def walk(before: Any, after: Any, path: str, depth: int) -> None:
        nonlocal complete
        if len(lines) >= MAX_CHANGES:
            complete = False
            return
        if before == after:
            return
        # The first thing an app ever saves has nothing to compare against.
        # "nothing becomes 2 fields" is true and useless; what the person needs
        # to know is which fields, so an empty document of the same shape is
        # compared against instead.
        if before is None and isinstance(after, (dict, list)):
            before = {} if isinstance(after, dict) else []
        label = path or "its data"
        if depth >= MAX_DEPTH:
            lines.append(f"{label} changes")
            complete = False
            return
        if isinstance(before, list) and isinstance(after, list):
            if len(before) != len(after):
                word = "entry" if len(after) == 1 else "entries"
                lines.append(f"{label} goes from {len(before)} to {len(after)} {word}")
            else:
                lines.append(f"{len(after)} entries in {label} change")
                complete = False
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                if len(lines) >= MAX_CHANGES:
                    complete = False
                    return
                here = f"{path}.{key}" if path else key
                if key not in before:
                    lines.append(f"{here} is added as {redact(key, after[key])}")
                elif key not in after:
                    lines.append(f"{here} is removed")
                else:
                    walk(before[key], after[key], here, depth + 1)
            return
        lines.append(f"{label} changes from {redact(path, before)} to {redact(path, after)}")

    walk(current, proposal, "", 0)
    if not lines:
        lines.append("nothing visible changes")
    return lines, complete


def summarize(
    effect: str,
    *,
    app_name: str,
    current: Any = None,
    proposal: Any = None,
    scope: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """The prompt's own words for what is about to happen.

    Built from the request rather than from anything the agent said about it. A
    model describing its own effect would be a model writing the sentence the
    person makes their decision from.
    """
    scope = scope or {}
    if effect == "write":
        detail, complete = changes(current, proposal)
        return {
            "effect": effect,
            "headline": f"{app_name} wants to save a change.",
            "detail": detail,
            "complete": complete,
        }
    if effect == "restore":
        return {
            "effect": effect,
            "headline": f"{app_name} wants to replace everything it has saved with a backup.",
            "detail": [note or "Everything currently saved in this app is replaced."],
            "complete": True,
        }
    if effect == "action":
        action = scope.get("action") or "an action"
        target = scope.get("app")
        # The scope names the target by id and the prompt names the caller by
        # its display name, so "notes" and "Notes" are the same app here.
        same = target and target.replace("-", " ").lower() == app_name.replace("-", " ").lower()
        whose = "" if same or not target else f"{target}'s "
        detail = []
        if isinstance(proposal, dict):
            for key in sorted(proposal)[:MAX_CHANGES]:
                detail.append(f"{key}: {redact(key, proposal[key])}")
            complete = len(proposal) <= MAX_CHANGES
        else:
            detail = [redact("value", proposal)]
            complete = True
        return {
            "effect": effect,
            "headline": f"{app_name} wants to run {whose}“{action}”.",
            "detail": detail or ["with no values"],
            "complete": complete,
        }
    if effect == "connection":
        return {
            "effect": effect,
            "headline": f"{app_name} wants to send a request to the service it is connected to.",
            "detail": [note or scope.get("operation") or "an operation on its connection"],
            "complete": True,
        }
    if effect == "publish":
        return {
            "effect": effect,
            "headline": f"{app_name} wants to update its widget on your desk.",
            "detail": [note or "Only what the widget shows changes."],
            "complete": True,
        }
    return {
        "effect": effect,
        "headline": f"{app_name} wants to make a change.",
        "detail": [note or "Vela cannot describe this one in detail."],
        "complete": False,
    }


def digest_of(value: Any) -> str:
    """The digest an approval is bound to. Same rule everywhere it is taken."""
    import hashlib

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
