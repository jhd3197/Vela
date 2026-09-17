"""Tasks and what happened during them, on disk.

Two tables and one rule about each.

**A run is a record, not a process.** Its row says what was asked, what state it
reached and what it produced. The process that carried it out is the
supervisor's and does not survive a restart — which is why the first thing this
store does on the way up is move every nonterminal row to `interrupted`. A run
marked `running` in a database with no loop behind it is a lie, and the honest
answer after a crash is "this was interrupted", not a task that appears to still
be working.

**Events are a numbered stream, per desktop.** A viewer that reconnects asks for
everything after the last sequence it saw, so a dropped connection loses nothing
and a reconnect duplicates nothing. The sequence is allocated inside the same
transaction as the row, so two events cannot share a number.

Retention follows the existing `chat_history` setting, the same one Ask uses.
With history off nothing durable is written: runs and events live in memory for
as long as the process does and are gone afterwards, which is what somebody who
turned history off asked for.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..app_storage import AppServiceError

#: Task states, from section 5.1 of the plan.
STATES = (
    "queued",
    "starting",
    "running",
    "waiting_approval",
    "paused",
    "taking_over",
    "human_control",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "outcome_unknown",
)

#: States that never move again on their own. "Try again" makes a *new* run that
#: references this one; a terminal state quietly becoming active again would be
#: a task restarting itself.
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted", "outcome_unknown")

#: States where the supervisor holds an execution handle.
ACTIVE = ("starting", "running", "waiting_approval", "paused", "taking_over", "human_control")

MAX_INSTRUCTION = 4000
MAX_RUNS_PER_DESKTOP = 200
MAX_EVENTS_PER_DESKTOP = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  desktop_id TEXT NOT NULL,
  instruction TEXT NOT NULL,
  state TEXT NOT NULL,
  client_request_id TEXT,
  model TEXT,
  profile TEXT NOT NULL DEFAULT '{}',
  result TEXT,
  detail TEXT,
  outcome TEXT,
  budget TEXT NOT NULL DEFAULT '{}',
  position INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_by_desktop ON agent_runs (desktop_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_by_request
  ON agent_runs (desktop_id, client_request_id)
  WHERE client_request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_events (
  desktop_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  run_id TEXT,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (desktop_id, sequence)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_run_id() -> str:
    return uuid.uuid4().hex


class RunStore:
    """Durable tasks and events, or a volatile stand-in when history is off."""

    def __init__(self, path: Path, *, history=None):
        self.path = Path(path)
        #: A callable answering "may this be written down". Read on every call
        #: rather than cached: turning history off has to take effect now.
        self._history = history or (lambda: True)
        self._volatile_runs: dict[str, dict[str, Any]] = {}
        self._volatile_events: list[dict[str, Any]] = []
        self._volatile_sequence: dict[str, int] = {}
        with self.connection() as db:
            db.executescript(SCHEMA)

    @property
    def keeping_history(self) -> bool:
        return bool(self._history())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ------------------------------------------------------- on the way up --

    def reconcile(self) -> int:
        """Every run that was in flight when this process last stopped.

        Marked `interrupted`, not resumed. A run's browser session, its view and
        its model conversation are all gone, and starting again from a stored
        instruction would mean repeating real-world effects nobody re-approved.
        Queued work is left queued and waits for the person to say go.
        """
        moved = 0
        with self.connection() as db:
            rows = db.execute(
                "SELECT id FROM agent_runs WHERE state IN ({})".format(
                    ",".join("?" * len(ACTIVE))
                ),
                ACTIVE,
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE agent_runs SET state='interrupted', outcome='unknown', "
                    "detail=?, ended_at=? WHERE id=?",
                    (
                        "Vela stopped while this task was working. It was not resumed, "
                        "because anything it had already done cannot be undone by "
                        "starting again.",
                        _now(),
                        row["id"],
                    ),
                )
                moved += 1
        for run in self._volatile_runs.values():
            if run["state"] in ACTIVE:
                run["state"] = "interrupted"
                moved += 1
        return moved

    # -------------------------------------------------------------- runs --

    def submit(
        self,
        desktop_id: str,
        instruction: str,
        *,
        client_request_id: str | None = None,
        model: str | None = None,
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queue one task, or return the one this request already queued.

        Deduplication is by the caller's own request id, so a retried submission
        — a flaky connection, a double-tapped button — joins the queue once.
        """
        instruction = (instruction or "").strip()
        if not instruction:
            raise AppServiceError(422, "A task needs an instruction.")
        if len(instruction) > MAX_INSTRUCTION:
            raise AppServiceError(422, f"An instruction is at most {MAX_INSTRUCTION} characters.")
        record = {
            "id": new_run_id(),
            "desktopId": desktop_id,
            "instruction": instruction,
            "state": "queued",
            "clientRequestId": client_request_id,
            "model": model,
            "profile": profile or {},
            "result": None,
            "detail": None,
            "outcome": None,
            "budget": {},
            "createdAt": _now(),
            "startedAt": None,
            "endedAt": None,
        }
        if not self.keeping_history:
            existing = self._find_volatile(desktop_id, client_request_id)
            if existing:
                return dict(existing)
            record["position"] = len(self._volatile_runs) + 1
            self._volatile_runs[record["id"]] = record
            return dict(record)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if client_request_id:
                row = db.execute(
                    "SELECT * FROM agent_runs WHERE desktop_id=? AND client_request_id=?",
                    (desktop_id, client_request_id),
                ).fetchone()
                if row:
                    return _run(row)
            position = (
                db.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM agent_runs WHERE desktop_id=?",
                    (desktop_id,),
                ).fetchone()[0]
                or 1
            )
            db.execute(
                "INSERT INTO agent_runs (id, desktop_id, instruction, state, client_request_id,"
                " model, profile, budget, position, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record["id"],
                    desktop_id,
                    instruction,
                    "queued",
                    client_request_id,
                    model,
                    json.dumps(profile or {}),
                    "{}",
                    position,
                    record["createdAt"],
                ),
            )
            self._trim_runs(db, desktop_id)
            record["position"] = position
        return record

    def _find_volatile(self, desktop_id, client_request_id):
        if not client_request_id:
            return None
        for run in self._volatile_runs.values():
            if run["desktopId"] == desktop_id and run["clientRequestId"] == client_request_id:
                return run
        return None

    def get(self, run_id: str) -> dict[str, Any]:
        if not self.keeping_history:
            run = self._volatile_runs.get(run_id)
            if run is None:
                raise AppServiceError(404, "That task is not on this server.")
            return dict(run)
        with self.connection() as db:
            row = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise AppServiceError(404, "That task is not on this server.")
        return _run(row)

    def list(self, desktop_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not self.keeping_history:
            runs = [run for run in self._volatile_runs.values() if run["desktopId"] == desktop_id]
            runs.sort(key=lambda run: run["position"], reverse=True)
            return [dict(run) for run in runs[offset : offset + limit]]
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM agent_runs WHERE desktop_id=? ORDER BY position DESC"
                " LIMIT ? OFFSET ?",
                (desktop_id, max(1, min(int(limit), 200)), max(0, int(offset))),
            ).fetchall()
        return [_run(row) for row in rows]

    def next_queued(self, desktop_id: str) -> dict[str, Any] | None:
        """The oldest queued task on this desktop. FIFO, always."""
        if not self.keeping_history:
            queued = [
                run
                for run in self._volatile_runs.values()
                if run["desktopId"] == desktop_id and run["state"] == "queued"
            ]
            queued.sort(key=lambda run: run["position"])
            return dict(queued[0]) if queued else None
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_runs WHERE desktop_id=? AND state='queued'"
                " ORDER BY position LIMIT 1",
                (desktop_id,),
            ).fetchone()
        return _run(row) if row else None

    def desktops_with_queued(self) -> list[str]:
        if not self.keeping_history:
            return sorted(
                {run["desktopId"] for run in self._volatile_runs.values() if run["state"] == "queued"}
            )
        with self.connection() as db:
            rows = db.execute(
                "SELECT DISTINCT desktop_id FROM agent_runs WHERE state='queued'"
            ).fetchall()
        return sorted(row["desktop_id"] for row in rows)

    def active(self, desktop_id: str) -> dict[str, Any] | None:
        """The one run this desktop is carrying, if any. There is at most one."""
        if not self.keeping_history:
            for run in self._volatile_runs.values():
                if run["desktopId"] == desktop_id and run["state"] in ACTIVE:
                    return dict(run)
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_runs WHERE desktop_id=? AND state IN ({})"
                " ORDER BY position LIMIT 1".format(",".join("?" * len(ACTIVE))),
                (desktop_id, *ACTIVE),
            ).fetchone()
        return _run(row) if row else None

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        """Change a run's state or result. Terminal is terminal.

        A run that has ended does not move again: "try again" creates a new run
        referencing this one, which is what makes a completed task's record mean
        something afterwards.
        """
        current = self.get(run_id)
        state = changes.get("state")
        if state is not None:
            if state not in STATES:
                raise AppServiceError(422, f"{state} is not a task state.")
            if current["state"] in TERMINAL and state != current["state"]:
                raise AppServiceError(
                    409, f"That task already {current['state'].replace('_', ' ')}."
                )
        if not self.keeping_history:
            run = self._volatile_runs[run_id]
            run.update({key: value for key, value in changes.items() if value is not None})
            if state in TERMINAL:
                run["endedAt"] = _now()
            if state == "starting" and not run.get("startedAt"):
                run["startedAt"] = _now()
            return dict(run)

        columns = {
            "state": "state",
            "model": "model",
            "result": "result",
            "detail": "detail",
            "outcome": "outcome",
            "budget": "budget",
            "profile": "profile",
        }
        sets, values = [], []
        for key, column in columns.items():
            if key not in changes or changes[key] is None:
                continue
            value = changes[key]
            if key in ("result", "profile", "budget"):
                value = json.dumps(value)
            sets.append(f"{column}=?")
            values.append(value)
        if state == "starting" and not current["startedAt"]:
            sets.append("started_at=?")
            values.append(_now())
        if state in TERMINAL:
            sets.append("ended_at=?")
            values.append(_now())
        if not sets:
            return current
        with self.connection() as db:
            db.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id=?", (*values, run_id))
        return self.get(run_id)

    def _trim_runs(self, db, desktop_id: str) -> None:
        db.execute(
            "DELETE FROM agent_runs WHERE desktop_id=? AND id NOT IN ("
            " SELECT id FROM agent_runs WHERE desktop_id=? ORDER BY position DESC LIMIT ?)",
            (desktop_id, desktop_id, MAX_RUNS_PER_DESKTOP),
        )

    # ------------------------------------------------------------ events --

    def append(self, desktop_id: str, kind: str, payload: dict[str, Any], *, run_id=None) -> dict:
        """One numbered event. The number is allocated with the row, not before."""
        record = {
            "desktopId": desktop_id,
            "runId": run_id,
            "kind": kind,
            "payload": payload,
            "createdAt": _now(),
        }
        if not self.keeping_history:
            sequence = self._volatile_sequence.get(desktop_id, 0) + 1
            self._volatile_sequence[desktop_id] = sequence
            record["sequence"] = sequence
            self._volatile_events.append(record)
            if len(self._volatile_events) > MAX_EVENTS_PER_DESKTOP:
                del self._volatile_events[: len(self._volatile_events) - MAX_EVENTS_PER_DESKTOP]
            return dict(record)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = (
                db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE desktop_id=?",
                    (desktop_id,),
                ).fetchone()[0]
                or 1
            )
            db.execute(
                "INSERT INTO agent_events VALUES (?,?,?,?,?,?)",
                (
                    desktop_id,
                    sequence,
                    run_id,
                    kind,
                    json.dumps(payload, default=str),
                    record["createdAt"],
                ),
            )
            db.execute(
                "DELETE FROM agent_events WHERE desktop_id=? AND sequence <= ?",
                (desktop_id, sequence - MAX_EVENTS_PER_DESKTOP),
            )
        record["sequence"] = sequence
        return record

    def events(self, desktop_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if not self.keeping_history:
            found = [
                dict(event)
                for event in self._volatile_events
                if event["desktopId"] == desktop_id and event["sequence"] > after
            ]
            return found[:limit]
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM agent_events WHERE desktop_id=? AND sequence > ?"
                " ORDER BY sequence LIMIT ?",
                (desktop_id, int(after), limit),
            ).fetchall()
        return [
            {
                "desktopId": row["desktop_id"],
                "sequence": int(row["sequence"]),
                "runId": row["run_id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload"]),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def latest_sequence(self, desktop_id: str) -> int:
        if not self.keeping_history:
            return self._volatile_sequence.get(desktop_id, 0)
        with self.connection() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM agent_events WHERE desktop_id=?",
                (desktop_id,),
            ).fetchone()
        return int(row[0] or 0)

    # -------------------------------------------------------- forgetting --

    def purge(self) -> int:
        """Everything this store kept. Called when history is turned off.

        A deletion, not a preference change — the same meaning the setting has
        had for Ask since before any of this existed.
        """
        with self.connection() as db:
            removed = db.execute("DELETE FROM agent_runs").rowcount
            db.execute("DELETE FROM agent_events")
        self._volatile_runs.clear()
        self._volatile_events.clear()
        self._volatile_sequence.clear()
        return removed

    def forget_desktop(self, desktop_id: str) -> int:
        with self.connection() as db:
            removed = db.execute(
                "DELETE FROM agent_runs WHERE desktop_id=?", (desktop_id,)
            ).rowcount
            db.execute("DELETE FROM agent_events WHERE desktop_id=?", (desktop_id,))
        for key in [
            key for key, run in self._volatile_runs.items() if run["desktopId"] == desktop_id
        ]:
            self._volatile_runs.pop(key, None)
        self._volatile_events = [
            event for event in self._volatile_events if event["desktopId"] != desktop_id
        ]
        self._volatile_sequence.pop(desktop_id, None)
        return removed


def _run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "desktopId": row["desktop_id"],
        "instruction": row["instruction"],
        "state": row["state"],
        "clientRequestId": row["client_request_id"],
        "model": row["model"],
        "profile": json.loads(row["profile"] or "{}"),
        "result": json.loads(row["result"]) if row["result"] else None,
        "detail": row["detail"],
        "outcome": row["outcome"],
        "budget": json.loads(row["budget"] or "{}"),
        "position": int(row["position"]),
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
    }
