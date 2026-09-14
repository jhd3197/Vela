"""Explicit installation grants and transactional, idempotent app write actions.

Only the declarative storage.append handler is implemented. App code, network
calls and cross-app raw storage access are never executed by this service.
"""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from .app_storage import AppServiceError


def fingerprint(manifest):
    return hashlib.sha256(json.dumps(manifest.raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Actions:
    def __init__(self, lifecycle, services):
        self.lifecycle, self.services = lifecycle, services
        self.registry, self.storage = lifecycle.registry, lifecycle.storage
        with self.storage.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS action_grants (
                  source TEXT, target TEXT, action TEXT, source_contract TEXT, target_contract TEXT,
                  PRIMARY KEY(source,target,action));
                CREATE TABLE IF NOT EXISTS action_results (
                  source TEXT, target TEXT, action TEXT, request_key TEXT, input_hash TEXT, result TEXT,
                  PRIMARY KEY(source,target,action,request_key));
                CREATE TABLE IF NOT EXISTS action_events (
                  id TEXT PRIMARY KEY, source_app TEXT, target_app TEXT, action TEXT, status TEXT,
                  error TEXT, created_at TEXT);
            ''')

    def context(self, app_id):
        manifest = self.registry.get(app_id)
        if not manifest or not self.registry.is_installed(app_id): raise AppServiceError(404, 'App is not installed')
        if manifest.schema_version != 2 or 'actions' not in manifest.capabilities:
            raise AppServiceError(403, 'App has no actions capability')
        return manifest, self.storage.activate(app_id)

    def pair(self, source_id, target_id, action_id):
        source, source_identity = self.context(source_id)
        if {'app': target_id, 'action': action_id} not in source.raw.get('actionRequests', []):
            raise AppServiceError(403, 'Caller did not declare this action request')
        target, target_identity = self.context(target_id)
        action = next((item for item in target.raw.get('actions', []) if item['id'] == action_id), None)
        if not action or 'storage' not in target.capabilities: raise AppServiceError(404, 'Target action is unavailable')
        return source, source_identity, target, target_identity, action

    def granted(self, db, source, sid, target, tid, action):
        row = db.execute('SELECT * FROM action_grants WHERE source=? AND target=? AND action=?', (sid, tid, action['id'])).fetchone()
        return bool(row and row['source_contract'] == fingerprint(source) and row['target_contract'] == fingerprint(target))

    def status(self, app_id):
        source, _ = self.context(app_id)
        results = []
        for request in source.raw.get('actionRequests', []):
            item = {**request, 'granted': False, 'available': False}
            try:
                source, sid, target, tid, action = self.pair(app_id, request['app'], request['action'])
                with self.storage.connection() as db: item['granted'] = self.granted(db, source, sid, target, tid, action)
                item.update(available=True, title=action['title'], effect=action['effect'], targetName=target.name)
                item.update(sourceContract=fingerprint(source), targetContract=fingerprint(target))
            except AppServiceError as exc: item['error'] = exc.detail
            results.append(item)
        return {'requests': results}

    def grant(self, app_id, target_id, action_id, allow, source_contract=None, target_contract=None):
        with self.lifecycle.lock:
            source, sid, target, tid, action = self.pair(app_id, target_id, action_id)
            if allow and (source_contract != fingerprint(source) or target_contract != fingerprint(target)):
                raise AppServiceError(409, 'Action contracts changed or were not reviewed; refresh before allowing')
            with self.storage.connection() as db:
                if allow:
                    db.execute('INSERT OR REPLACE INTO action_grants VALUES (?,?,?,?,?)', (sid, tid, action_id, fingerprint(source), fingerprint(target)))
                else: db.execute('DELETE FROM action_grants WHERE source=? AND target=? AND action=?', (sid, tid, action_id))
            return self.status(app_id)

    def _event(self, db, identity, source, target, action, status, error=None):
        db.execute('INSERT INTO action_events VALUES (?,?,?,?,?,?,?)', (identity, source, target, action, status, error, datetime.now(timezone.utc).isoformat()))
        db.execute('DELETE FROM action_events WHERE source_app=? AND id NOT IN (SELECT id FROM action_events WHERE source_app=? ORDER BY created_at DESC LIMIT 200)', (source, source))

    def invoke(self, session, target_id, action_id, value, key):
        started = time.monotonic()
        event_id = str(uuid.uuid4())
        with self.lifecycle.lock:
            try:
                source, sid, target, tid, action = self.pair(session['app_id'], target_id, action_id)
                if sid != session['installationId'] or 'actions' not in session['capabilities']:
                    raise AppServiceError(403, 'Caller action identity is no longer valid')
                encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
                if len(encoded.encode()) > 32768: raise AppServiceError(413, 'Action input exceeds 32 KiB')
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                self.services.validate_data(target.id, value, action['inputSchema'], manifest=target)
                with self.storage.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self.granted(db, source, sid, target, tid, action): raise AppServiceError(403, 'Allow this action in the Vela app controls first')
                    # Receipts follow retained app data across reinstall; grants still
                    # require the current pair of installation identities.
                    previous = db.execute('SELECT * FROM action_results WHERE source=? AND target=? AND action=? AND request_key=?', (source.id, target.id, action_id, key)).fetchone()
                    if previous:
                        if previous['input_hash'] != digest: raise AppServiceError(409, 'This retry key belongs to different input')
                        return {**json.loads(previous['result']), 'replayed': True}
                    if db.execute('SELECT count(*) FROM action_results WHERE source=?', (source.id,)).fetchone()[0] >= 10000:
                        raise AppServiceError(429, 'App action receipt limit reached')
                    handler = action['handler']
                    current = db.execute('SELECT * FROM documents WHERE app_id=?', (target_id,)).fetchone()
                    schema = target.raw['data']['schemaVersion']
                    if current and current['schema_version'] != schema: raise AppServiceError(409, 'Target data needs migration')
                    document = json.loads(current['value']) if current and json.loads(current['value']) is not None else copy.deepcopy(handler['initial'])
                    if not isinstance(document, dict): raise AppServiceError(422, 'Target action document must be an object')
                    records = document.get(handler['field'])
                    if not isinstance(records, list): raise AppServiceError(422, 'Target action field must be an array')
                    record = {field: value[field] for field in handler['fields'] if field in value}
                    record.update(id=str(uuid.uuid4()), updated=int(time.time() * 1000))
                    records.append(record)
                    self.services.validate_data(target_id, document, manifest=target)
                    saved = json.dumps(document, ensure_ascii=False, allow_nan=False)
                    if len(saved.encode()) > target.raw['data'].get('quotaBytes', 1048576): raise AppServiceError(413, 'Target data quota exceeded')
                    revision = (current['revision'] if current else 0) + 1
                    output = {'recordId': record['id'], 'revision': revision}
                    self.services.validate_data(target_id, output, action['outputSchema'], manifest=target)
                    result = {'executionId': event_id, 'status': 'succeeded', 'output': output, 'replayed': False}
                    if time.monotonic() - started > 2: raise AppServiceError(504, 'Action exceeded its two-second deadline')
                    db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?)', (target_id, saved, revision, schema))
                    db.execute('INSERT INTO action_results VALUES (?,?,?,?,?,?)', (source.id, target.id, action_id, key, digest, json.dumps(result)))
                    self._event(db, event_id, source.id, target.id, action_id, 'succeeded')
                return result
            except (AppServiceError, ValueError, TypeError) as exc:
                error = exc if isinstance(exc, AppServiceError) else AppServiceError(422, 'Invalid action input')
                with self.storage.connection() as db:
                    self._event(db, event_id, session['app_id'], target_id, action_id, 'failed', error.detail)
                raise error

    def history(self, app_id):
        with self.storage.connection() as db:
            return {'executions': [dict(row) for row in db.execute('SELECT * FROM action_events WHERE source_app=? OR target_app=? ORDER BY created_at DESC LIMIT 50', (app_id, app_id)).fetchall()]}
