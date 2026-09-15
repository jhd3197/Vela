"""Durable state for automations: workflows, revisions, runs, events and grants.

Its own SQLite database beside the app data, so an automation never reaches an
app's documents except through the action broker. Every table here is written
before the dashboard is told that something happened, which is what lets a run
survive a restart with an honest outcome instead of a hopeful one.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..app_storage import AppServiceError

#: Draft revisions kept per workflow. Activated revisions and any revision a run
#: still references are never pruned.
DRAFT_HISTORY_LIMIT = 20

#: Runs kept per workflow, and events kept per run.
RUN_HISTORY_LIMIT = 200
RUN_EVENT_LIMIT = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  draft_revision INTEGER NOT NULL,
  active_revision INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_revisions (
  workflow_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  document TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  catalog_version INTEGER NOT NULL,
  frozen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (workflow_id, revision)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL,
  trigger TEXT NOT NULL,
  trigger_input TEXT,
  queued_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  runtime TEXT NOT NULL DEFAULT '{}',
  occurrence_id TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  checkpoint TEXT,
  resumed_from TEXT
);
CREATE INDEX IF NOT EXISTS runs_by_workflow ON runs (workflow_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS runs_by_status ON runs (status, queued_at);
CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  at TEXT NOT NULL,
  type TEXT NOT NULL,
  node_id TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS automation_grants (
  workflow_id TEXT NOT NULL,
  app TEXT NOT NULL,
  action TEXT NOT NULL,
  request_contract TEXT NOT NULL,
  target_contract TEXT NOT NULL,
  installation TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (workflow_id, app, action)
);
CREATE TABLE IF NOT EXISTS automation_receipts (
  request_key TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  app TEXT NOT NULL,
  action TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
  workflow_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  config TEXT NOT NULL,
  timezone TEXT NOT NULL,
  next_due TEXT,
  last_occurrence TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_occurrences (
  workflow_id TEXT NOT NULL,
  occurrence_id TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  run_id TEXT,
  outcome TEXT NOT NULL DEFAULT 'claimed',
  PRIMARY KEY (workflow_id, occurrence_id)
);
CREATE TABLE IF NOT EXISTS webhooks (
  workflow_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  token_id TEXT NOT NULL UNIQUE,
  secret_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS webhook_replays (
  token_id TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (token_id, body_hash)
);
CREATE TABLE IF NOT EXISTS approvals (
  run_id TEXT NOT NULL,
  gate_key TEXT NOT NULL,
  node_id TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  decided_at TEXT,
  decided_by TEXT,
  comment TEXT,
  PRIMARY KEY (run_id, gate_key)
);
"""

TERMINAL_STATUSES = ('succeeded', 'failed', 'cancelled', 'interrupted', 'timed_out', 'rejected')
LIVE_STATUSES = ('queued', 'running', 'waiting')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    # ---------------------------------------------------------- workflows --

    def create_workflow(self, name, document, fingerprint, catalog_version, description=''):
        workflow_id = str(uuid.uuid4())
        stamp = now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM workflows').fetchone()[0] >= 200:
                raise AppServiceError(429, 'Vela keeps at most 200 automations. Archive one first.')
            db.execute('INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?)',
                       (workflow_id, name, description, 'draft', 1, None, stamp, stamp))
            db.execute('INSERT INTO workflow_revisions VALUES (?,?,?,?,?,?,?)',
                       (workflow_id, 1, json.dumps(document, separators=(',', ':')), fingerprint,
                        catalog_version, 0, stamp))
        return workflow_id

    def list_workflows(self, include_archived=False):
        with self.connection() as db:
            clause = '' if include_archived else "WHERE status != 'archived'"
            rows = db.execute(f'SELECT * FROM workflows {clause} ORDER BY updated_at DESC').fetchall()
            return [dict(row) for row in rows]

    def get_workflow(self, workflow_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM workflows WHERE id=?', (workflow_id,)).fetchone()
        if not row:
            raise AppServiceError(404, 'That automation no longer exists.')
        return dict(row)

    def get_revision(self, workflow_id, revision):
        with self.connection() as db:
            row = db.execute('SELECT * FROM workflow_revisions WHERE workflow_id=? AND revision=?',
                             (workflow_id, revision)).fetchone()
        if not row:
            raise AppServiceError(404, 'That version of the automation is no longer stored.')
        return {**dict(row), 'document': json.loads(row['document'])}

    def save_draft(self, workflow_id, expected_revision, document, fingerprint, catalog_version,
                   name=None, description=None):
        """Write a new draft revision, or refuse when someone else saved first."""
        stamp = now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            workflow = db.execute('SELECT * FROM workflows WHERE id=?', (workflow_id,)).fetchone()
            if not workflow:
                raise AppServiceError(404, 'That automation no longer exists.')
            if workflow['status'] == 'archived':
                raise AppServiceError(409, 'Restore this automation before editing it.')
            current = workflow['draft_revision']
            if expected_revision != current:
                raise AppServiceError(409, 'This automation changed in another window. '
                                           'Reload to see the current version before saving.')
            latest = db.execute(
                'SELECT fingerprint FROM workflow_revisions WHERE workflow_id=? AND revision=?',
                (workflow_id, current)).fetchone()
            revision = current
            if not latest or latest['fingerprint'] != fingerprint:
                revision = current + 1
                db.execute('INSERT INTO workflow_revisions VALUES (?,?,?,?,?,?,?)',
                           (workflow_id, revision, json.dumps(document, separators=(',', ':')),
                            fingerprint, catalog_version, 0, stamp))
            db.execute('UPDATE workflows SET draft_revision=?, name=COALESCE(?, name), '
                       'description=COALESCE(?, description), updated_at=? WHERE id=?',
                       (revision, name, description, stamp, workflow_id))
            self._prune_revisions(db, workflow_id)
            return revision

    def _prune_revisions(self, db, workflow_id):
        keep = db.execute(
            'SELECT revision FROM workflow_revisions WHERE workflow_id=? AND frozen=0 '
            'ORDER BY revision DESC LIMIT ?', (workflow_id, DRAFT_HISTORY_LIMIT)).fetchall()
        if len(keep) < DRAFT_HISTORY_LIMIT:
            return
        floor = keep[-1]['revision']
        db.execute(
            'DELETE FROM workflow_revisions WHERE workflow_id=? AND frozen=0 AND revision < ? '
            'AND revision NOT IN (SELECT revision FROM runs WHERE workflow_id=?)',
            (workflow_id, floor, workflow_id))

    def freeze_revision(self, workflow_id, revision):
        with self.connection() as db:
            db.execute('UPDATE workflow_revisions SET frozen=1 WHERE workflow_id=? AND revision=?',
                       (workflow_id, revision))

    def set_status(self, workflow_id, status, active_revision=...):
        with self.connection() as db:
            if active_revision is ...:
                db.execute('UPDATE workflows SET status=?, updated_at=? WHERE id=?',
                           (status, now(), workflow_id))
            else:
                db.execute('UPDATE workflows SET status=?, active_revision=?, updated_at=? WHERE id=?',
                           (status, active_revision, now(), workflow_id))

    def rename(self, workflow_id, name, description):
        with self.connection() as db:
            db.execute('UPDATE workflows SET name=?, description=?, updated_at=? WHERE id=?',
                       (name, description, now(), workflow_id))

    def delete_workflow(self, workflow_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for table in ('run_events',):
                db.execute(f'DELETE FROM {table} WHERE run_id IN '
                           '(SELECT id FROM runs WHERE workflow_id=?)', (workflow_id,))
            db.execute('DELETE FROM approvals WHERE run_id IN '
                       '(SELECT id FROM runs WHERE workflow_id=?)', (workflow_id,))
            for table in ('runs', 'workflow_revisions', 'automation_grants', 'automation_receipts',
                          'schedules', 'schedule_occurrences', 'webhooks'):
                db.execute(f'DELETE FROM {table} WHERE workflow_id=?', (workflow_id,))
            db.execute('DELETE FROM workflows WHERE id=?', (workflow_id,))

    # --------------------------------------------------------------- runs --

    def queue_run(self, workflow_id, revision, trigger, trigger_input, *, runtime,
                  occurrence_id=None, max_queued=25):
        run_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            queued = db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if queued >= max_queued:
                raise AppServiceError(429, 'Vela already has the maximum number of runs waiting. '
                                           'Wait for one to finish, or cancel one.')
            db.execute(
                'INSERT INTO runs (id, workflow_id, revision, status, trigger, trigger_input, '
                'queued_at, runtime, occurrence_id) VALUES (?,?,?,?,?,?,?,?,?)',
                (run_id, workflow_id, revision, 'queued', trigger,
                 json.dumps(trigger_input) if trigger_input is not None else None,
                 now(), json.dumps(runtime, sort_keys=True), occurrence_id))
            self._prune_runs(db, workflow_id)
        return run_id

    def _prune_runs(self, db, workflow_id):
        """Keep run history bounded without dropping anything still in use.

        A run that is still live, still holds a pending approval, or still owns
        receipts a retry could match is never removed.
        """
        doomed = db.execute(
            'SELECT id FROM runs WHERE workflow_id=? AND status NOT IN (?,?,?) '
            'AND id NOT IN (SELECT run_id FROM approvals WHERE status=\'pending\') '
            'AND id NOT IN (SELECT id FROM runs WHERE workflow_id=? ORDER BY queued_at DESC LIMIT ?)',
            (workflow_id, *LIVE_STATUSES, workflow_id, RUN_HISTORY_LIMIT)).fetchall()
        for row in doomed:
            db.execute('DELETE FROM run_events WHERE run_id=?', (row['id'],))
            db.execute('DELETE FROM automation_receipts WHERE run_id=?', (row['id'],))
            db.execute('DELETE FROM approvals WHERE run_id=?', (row['id'],))
            db.execute('DELETE FROM runs WHERE id=?', (row['id'],))

    def live_run(self, workflow_id, statuses=LIVE_STATUSES):
        """The first run of this workflow in one of the given states, if any."""
        placeholders = ','.join('?' * len(statuses))
        with self.connection() as db:
            row = db.execute(
                f'SELECT * FROM runs WHERE workflow_id=? AND status IN ({placeholders}) '
                'ORDER BY queued_at LIMIT 1', (workflow_id, *statuses)).fetchone()
        return dict(row) if row else None

    def claim_next_run(self):
        """Atomically take the oldest queued run, one active run per workflow.

        A run waiting for an approval still occupies its workflow: it has work
        left to do against the revision it started on, so a second run must not
        overtake it.
        """
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                "SELECT * FROM runs WHERE status='queued' AND workflow_id NOT IN "
                "(SELECT workflow_id FROM runs WHERE status IN ('running','waiting')) "
                'ORDER BY queued_at LIMIT 1').fetchone()
            if not row:
                return None
            db.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (now(), row['id']))
            return {**dict(row), 'status': 'running'}

    def get_run(self, run_id):
        with self.connection() as db:
            row = db.execute(
                'SELECT runs.*, workflows.name AS workflow_name FROM runs '
                'LEFT JOIN workflows ON workflows.id = runs.workflow_id WHERE runs.id=?',
                (run_id,)).fetchone()
        if not row:
            raise AppServiceError(404, 'That run is no longer stored.')
        return dict(row)

    def list_runs(self, workflow_id=None, limit=50, before=None):
        clauses, parameters = [], []
        if workflow_id:
            clauses.append('runs.workflow_id=?')
            parameters.append(workflow_id)
        if before:
            clauses.append('runs.queued_at < ?')
            parameters.append(before)
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.connection() as db:
            rows = db.execute(
                'SELECT runs.*, workflows.name AS workflow_name FROM runs '
                f'LEFT JOIN workflows ON workflows.id = runs.workflow_id {where} '
                'ORDER BY runs.queued_at DESC LIMIT ?', (*parameters, min(limit, 200))).fetchall()
        return [dict(row) for row in rows]

    def append_events(self, run_id, events):
        """Persist ordered run events. Returns the last sequence number written."""
        if not events:
            return None
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id=?',
                             (run_id,)).fetchone()
            seq = row['seq']
            stamp = now()
            if seq >= RUN_EVENT_LIMIT:
                return seq
            for event in events:
                seq += 1
                if seq > RUN_EVENT_LIMIT:
                    db.execute('INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?,?)',
                               (run_id, seq, stamp, 'log-truncated', None,
                                json.dumps({'detail': 'This run produced more log entries than Vela keeps.'})))
                    break
                db.execute('INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?,?)',
                           (run_id, seq, stamp, event['type'], event.get('nodeId'),
                            json.dumps(event, separators=(',', ':'))))
            return seq

    def events(self, run_id, after=0, limit=500):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM run_events WHERE run_id=? AND seq > ? ORDER BY seq LIMIT ?',
                              (run_id, after, limit)).fetchall()
        return [{'seq': row['seq'], 'at': row['at'], **json.loads(row['payload'])} for row in rows]

    def finish_run(self, run_id, status, error=None, checkpoint=None):
        with self.connection() as db:
            db.execute('UPDATE runs SET status=?, finished_at=?, error=?, checkpoint=? WHERE id=?',
                       (status, now(), error,
                        json.dumps(checkpoint, separators=(',', ':')) if checkpoint else None, run_id))

    def suspend_run(self, run_id, checkpoint):
        with self.connection() as db:
            db.execute("UPDATE runs SET status='waiting', checkpoint=? WHERE id=?",
                       (json.dumps(checkpoint, separators=(',', ':')), run_id))

    def request_cancel(self, run_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise AppServiceError(404, 'That run is no longer stored.')
            if row['status'] not in LIVE_STATUSES:
                raise AppServiceError(409, 'That run has already finished.')
            db.execute('UPDATE runs SET cancel_requested=1 WHERE id=?', (run_id,))
            if row['status'] == 'queued':
                db.execute("UPDATE runs SET status='cancelled', finished_at=?, "
                           "error='Cancelled before it started.' WHERE id=?", (now(), run_id))
                return 'cancelled'
            return row['status']

    def recover_interrupted(self, detail):
        """Mark runs that were live when the server stopped. Called once at startup."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id, status FROM runs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                if row['status'] == 'queued':
                    db.execute("UPDATE runs SET status='cancelled', finished_at=?, error=? WHERE id=?",
                               (now(), 'Vela stopped before this run started.', row['id']))
                else:
                    db.execute("UPDATE runs SET status='interrupted', finished_at=?, error=? WHERE id=?",
                               (now(), detail, row['id']))
            return len(rows)

    def waiting_runs(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM runs WHERE status='waiting'").fetchall()
        return [dict(row) for row in rows]

    def run_statistics(self, since):
        with self.connection() as db:
            row = db.execute(
                'SELECT count(*) AS total, '
                "SUM(status='succeeded') AS succeeded, "
                "SUM(status IN ('failed','timed_out','interrupted')) AS failed "
                'FROM runs WHERE queued_at >= ?', (since,)).fetchone()
            duration = db.execute(
                'SELECT started_at, finished_at FROM runs WHERE queued_at >= ? '
                'AND started_at IS NOT NULL AND finished_at IS NOT NULL LIMIT 500',
                (since,)).fetchall()
        elapsed = []
        for item in duration:
            try:
                start = datetime.fromisoformat(item['started_at'])
                end = datetime.fromisoformat(item['finished_at'])
            except (TypeError, ValueError):
                continue
            elapsed.append((end - start).total_seconds())
        return {
            'total': row['total'] or 0,
            'succeeded': row['succeeded'] or 0,
            'failed': row['failed'] or 0,
            'averageSeconds': round(sum(elapsed) / len(elapsed), 2) if elapsed else None,
        }

    # ------------------------------------------------------------- grants --

    def grants(self, workflow_id):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM automation_grants WHERE workflow_id=?',
                              (workflow_id,)).fetchall()
        return {(row['app'], row['action']): dict(row) for row in rows}

    def save_grant(self, workflow_id, app, action, request_contract, target_contract, installation):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO automation_grants VALUES (?,?,?,?,?,?,?)',
                       (workflow_id, app, action, request_contract, target_contract,
                        installation, now()))

    def revoke_grant(self, workflow_id, app, action):
        with self.connection() as db:
            db.execute('DELETE FROM automation_grants WHERE workflow_id=? AND app=? AND action=?',
                       (workflow_id, app, action))

    def revoke_all_grants(self, workflow_id):
        with self.connection() as db:
            db.execute('DELETE FROM automation_grants WHERE workflow_id=?', (workflow_id,))

    # ----------------------------------------------------------- schedules --

    def save_schedule(self, workflow_id, node_id, config, timezone_name, next_due):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?,?,?,?,'
                       '(SELECT last_occurrence FROM schedules WHERE workflow_id=?),?)',
                       (workflow_id, node_id, json.dumps(config, sort_keys=True), timezone_name,
                        next_due, workflow_id, now()))

    def clear_schedule(self, workflow_id):
        with self.connection() as db:
            db.execute('DELETE FROM schedules WHERE workflow_id=?', (workflow_id,))

    def due_schedules(self, moment):
        with self.connection() as db:
            rows = db.execute(
                'SELECT schedules.*, workflows.status, workflows.active_revision FROM schedules '
                'JOIN workflows ON workflows.id = schedules.workflow_id '
                "WHERE workflows.status='active' AND schedules.next_due IS NOT NULL "
                'AND schedules.next_due <= ?', (moment,)).fetchall()
        return [dict(row) for row in rows]

    def schedule(self, workflow_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM schedules WHERE workflow_id=?', (workflow_id,)).fetchone()
        return dict(row) if row else None

    def claim_occurrence(self, workflow_id, occurrence_id, next_due):
        """Take one scheduled occurrence exactly once, then advance the due time."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute(
                'SELECT 1 FROM schedule_occurrences WHERE workflow_id=? AND occurrence_id=?',
                (workflow_id, occurrence_id)).fetchone()
            db.execute('UPDATE schedules SET next_due=?, last_occurrence=? WHERE workflow_id=?',
                       (next_due, occurrence_id, workflow_id))
            if existing:
                return False
            db.execute('INSERT INTO schedule_occurrences (workflow_id, occurrence_id, claimed_at) '
                       'VALUES (?,?,?)', (workflow_id, occurrence_id, now()))
            db.execute(
                'DELETE FROM schedule_occurrences WHERE workflow_id=? AND occurrence_id NOT IN '
                '(SELECT occurrence_id FROM schedule_occurrences WHERE workflow_id=? '
                'ORDER BY claimed_at DESC LIMIT 200)', (workflow_id, workflow_id))
            return True

    def record_occurrence(self, workflow_id, occurrence_id, run_id, outcome):
        with self.connection() as db:
            db.execute('UPDATE schedule_occurrences SET run_id=?, outcome=? '
                       'WHERE workflow_id=? AND occurrence_id=?',
                       (run_id, outcome, workflow_id, occurrence_id))

    def recent_occurrences(self, workflow_id, limit=20):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM schedule_occurrences WHERE workflow_id=? '
                              'ORDER BY claimed_at DESC LIMIT ?', (workflow_id, limit)).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ webhooks --

    def save_webhook(self, workflow_id, node_id, token_id, secret_hash):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO webhooks VALUES (?,?,?,?,?,NULL)',
                       (workflow_id, node_id, token_id, secret_hash, now()))

    def webhook(self, workflow_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM webhooks WHERE workflow_id=?', (workflow_id,)).fetchone()
        return dict(row) if row else None

    def webhook_by_token(self, token_id):
        with self.connection() as db:
            row = db.execute(
                'SELECT webhooks.*, workflows.status, workflows.active_revision FROM webhooks '
                'JOIN workflows ON workflows.id = webhooks.workflow_id WHERE token_id=?',
                (token_id,)).fetchone()
        return dict(row) if row else None

    def clear_webhook(self, workflow_id):
        with self.connection() as db:
            db.execute('DELETE FROM webhook_replays WHERE token_id IN '
                       '(SELECT token_id FROM webhooks WHERE workflow_id=?)', (workflow_id,))
            db.execute('DELETE FROM webhooks WHERE workflow_id=?', (workflow_id,))

    def note_webhook_delivery(self, token_id, body_hash):
        """Record one delivery; False means this exact body was already accepted."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT 1 FROM webhook_replays WHERE token_id=? AND body_hash=?',
                                  (token_id, body_hash)).fetchone()
            if existing:
                return False
            db.execute('INSERT INTO webhook_replays VALUES (?,?,?)', (token_id, body_hash, now()))
            db.execute('UPDATE webhooks SET last_used_at=? WHERE token_id=?', (now(), token_id))
            db.execute('DELETE FROM webhook_replays WHERE token_id=? AND body_hash NOT IN '
                       '(SELECT body_hash FROM webhook_replays WHERE token_id=? '
                       'ORDER BY seen_at DESC LIMIT 500)', (token_id, token_id))
            return True

    # ----------------------------------------------------------- approvals --

    def record_approval_request(self, run_id, gate_key, node_id, message, expires_at):
        with self.connection() as db:
            db.execute(
                'INSERT OR IGNORE INTO approvals (run_id, gate_key, node_id, message, status, '
                'created_at, expires_at) VALUES (?,?,?,?,?,?,?)',
                (run_id, gate_key, node_id, message, 'pending', now(), expires_at))

    def decide_approval(self, run_id, gate_key, approved, decided_by, comment):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM approvals WHERE run_id=? AND gate_key=?',
                             (run_id, gate_key)).fetchone()
            if not row:
                raise AppServiceError(404, 'That approval request no longer exists.')
            if row['status'] != 'pending':
                raise AppServiceError(409, 'That approval was already decided.')
            if row['expires_at'] and row['expires_at'] <= now():
                db.execute("UPDATE approvals SET status='expired' WHERE run_id=? AND gate_key=?",
                           (run_id, gate_key))
                raise AppServiceError(409, 'That approval request expired.')
            db.execute('UPDATE approvals SET status=?, decided_at=?, decided_by=?, comment=? '
                       'WHERE run_id=? AND gate_key=?',
                       ('approved' if approved else 'rejected', now(), decided_by, comment,
                        run_id, gate_key))
        return self.approvals(run_id)

    def approvals(self, run_id):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM approvals WHERE run_id=? ORDER BY created_at',
                              (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def pending_approvals(self):
        with self.connection() as db:
            rows = db.execute(
                'SELECT approvals.*, runs.workflow_id, workflows.name AS workflow_name '
                'FROM approvals JOIN runs ON runs.id = approvals.run_id '
                'LEFT JOIN workflows ON workflows.id = runs.workflow_id '
                "WHERE approvals.status='pending' ORDER BY approvals.created_at").fetchall()
        return [dict(row) for row in rows]

    def expire_approvals(self):
        with self.connection() as db:
            db.execute("UPDATE approvals SET status='expired' WHERE status='pending' "
                       'AND expires_at IS NOT NULL AND expires_at <= ?', (now(),))

    # ------------------------------------------------------------ receipts --

    def find_receipt(self, request_key):
        with self.connection() as db:
            row = db.execute('SELECT * FROM automation_receipts WHERE request_key=?',
                             (request_key,)).fetchone()
        return dict(row) if row else None

    def save_receipt(self, request_key, workflow_id, run_id, node_id, app, action, input_hash, result):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO automation_receipts VALUES (?,?,?,?,?,?,?,?,?)',
                       (request_key, workflow_id, run_id, node_id, app, action, input_hash,
                        json.dumps(result, separators=(',', ':')), now()))
