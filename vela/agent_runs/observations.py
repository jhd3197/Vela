"""What a run has seen, and whether anything is actually happening.

Two jobs, both about honesty.

The first is noticing that nothing is changing. A model that cannot find the
button will look again, and again, and a loop that only stops when a budget runs
out spends fifteen minutes to report the same nothing. Observations are
fingerprinted, and a run of identical ones stops with the reason rather than
continuing to pay for them.

The second is evidence. Each action records what it aimed at, what it expected,
what the tool answered and what the view looked like afterwards — enough to
check a claim of success against what actually changed. Deliberately not the
whole page and not every frame: keeping all of that by default would make a
record nobody reads out of content somebody would rather Vela had not kept.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

#: Identical observations in a row before a run is told to stop looking. Five is
#: enough to cover a page that is genuinely still loading and short of the point
#: where repeating it is telling anybody something new.
NO_PROGRESS_LIMIT = 5

#: Action records kept per run. Bounded because a stuck run would otherwise grow
#: this without limit, and the last few dozen steps are what explains a failure.
KEEP_ACTIONS = 60


class StalledError(Exception):
    """Nothing has changed for long enough that continuing is not worth it."""

    def __init__(self, detail: str, repeats: int):
        super().__init__(detail)
        self.detail = detail
        self.repeats = repeats


def fingerprint(observation: dict[str, Any]) -> str:
    """What makes this observation the same as, or different from, the last one.

    The address, the controls and how much text there is. Not the text itself: a
    clock in the corner would make every observation different and the check
    useless, while a page whose controls and length are unchanged is a page that
    has not moved in any way an agent can act on.
    """
    page = observation.get("page") or {}
    controls = [
        (control.get("role"), control.get("name"), control.get("value"), control.get("disabled"))
        for control in page.get("controls") or []
    ]
    material = {
        "url": page.get("url"),
        "title": page.get("title"),
        "controls": controls,
        "textLength": len(page.get("text") or ""),
        "dialogs": [dialog.get("name") for dialog in page.get("dialogs") or []],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ObservationLog:
    """Per-view observation state and per-run action evidence, in memory.

    In memory because it belongs to a running process. What survives a restart
    is the task record and its result, which Phase 8 owns; a fingerprint from
    before a restart would describe a page that is no longer open.
    """

    def __init__(self, *, no_progress_limit: int = NO_PROGRESS_LIMIT, keep: int = KEEP_ACTIONS):
        self._limit = max(2, int(no_progress_limit))
        self._keep = max(1, int(keep))
        #: (desktop, view) -> the last fingerprint and how many times running.
        self._views: dict[tuple[str, str], dict[str, Any]] = {}
        #: (desktop, run) -> bounded action evidence.
        self._actions: dict[tuple[str, str], list[dict[str, Any]]] = {}

    # ----------------------------------------------------- observations --

    def record(self, desktop_id: str, view_id: str, observation: dict[str, Any]) -> dict[str, Any]:
        """Note one observation and say whether the view is going anywhere.

        Raises `StalledError` on the observation that crosses the limit rather
        than returning a flag, because a caller that has to remember to look at
        a flag is a caller that will eventually forget.
        """
        key = (desktop_id, view_id)
        current = fingerprint(observation)
        previous = self._views.get(key)
        repeats = previous["repeats"] + 1 if previous and previous["fingerprint"] == current else 1
        self._views[key] = {
            "fingerprint": current,
            "repeats": repeats,
            "observationId": observation.get("observationId"),
            "at": time.time(),
        }
        if repeats >= self._limit:
            # Cleared, so a deliberate retry after a change of approach starts
            # from zero rather than failing on its first look.
            self._views.pop(key, None)
            raise StalledError(
                f"This view has looked the same for {repeats} observations in a row. "
                "Nothing is changing here; try something different or stop.",
                repeats,
            )
        return {"repeats": repeats, "fingerprint": current}

    def latest(self, desktop_id: str, view_id: str) -> dict[str, Any] | None:
        return self._views.get((desktop_id, view_id))

    def progressed(self, desktop_id: str, view_id: str) -> None:
        """Something changed the view, so the run of identical looks is over."""
        self._views.pop((desktop_id, view_id), None)

    # --------------------------------------------------------- evidence --

    def note_action(
        self,
        desktop_id: str,
        run_id: str,
        *,
        tool: str,
        target: Any = None,
        expected: str | None = None,
        result: Any = None,
        outcome: str = "committed",
    ) -> dict[str, Any]:
        """One line of what a run did. Short by design; this is a record, not a log."""
        entry = {
            "at": time.time(),
            "tool": tool,
            "target": target,
            "expected": expected,
            "outcome": outcome,
            "result": _compact(result),
        }
        records = self._actions.setdefault((desktop_id, run_id), [])
        records.append(entry)
        if len(records) > self._keep:
            del records[: len(records) - self._keep]
        return entry

    def evidence(self, desktop_id: str, run_id: str) -> list[dict[str, Any]]:
        return list(self._actions.get((desktop_id, run_id), ()))

    def forget_run(self, desktop_id: str, run_id: str) -> None:
        self._actions.pop((desktop_id, run_id), None)

    def forget_desktop(self, desktop_id: str) -> None:
        for key in [key for key in self._views if key[0] == desktop_id]:
            self._views.pop(key, None)
        for key in [key for key in self._actions if key[0] == desktop_id]:
            self._actions.pop(key, None)


def _compact(value: Any) -> Any:
    """What a result looks like in the record: its shape, not its contents.

    An observation's text is the page's, an action's answer is the tool's. The
    first is kept out of the record entirely; the second is kept because it is
    short and it is what a claim of success gets compared against.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("text", "controls", "page"):
                continue
            out[key] = _compact(item)
        return out
    if isinstance(value, list):
        return [_compact(item) for item in value[:10]]
    if isinstance(value, str):
        return value if len(value) <= 200 else value[:200] + "…"
    return value
