"""Reviewed release activation, declarative data migrations and crash recovery.

The SQLite commit is the decision point. A durable filesystem journal is written
before swapping installed code; startup undoes any swap without a committed row.
"""
import copy
import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .app_storage import AppServiceError
from .manifest import load_manifest, validate_manifest, ManifestError
from .package_files import copy_package, replace_dir, unpack, tree_digest, write_json, portable


def package_manifest(path):
    raw = json.loads((path / 'app.json').read_text(encoding='utf-8'))
    return validate_manifest(raw, raw.get('id', ''), path)


def migrate_value(value, declaration, root):
    """A bounded object-only JSON Patch subset; app-supplied code never executes."""
    operations = json.loads((root / declaration['file']).read_text(encoding='utf-8'))
    if not isinstance(operations, list) or len(operations) > 100:
        raise AppServiceError(422, 'Migration must contain at most 100 object patch operations')
    value = copy.deepcopy(value)
    for item in operations:
        if not isinstance(item, dict) or item.get('op') not in ('add', 'replace', 'remove') or not isinstance(item.get('path'), str):
            raise AppServiceError(422, 'Unsupported migration operation')
        expected = {'op', 'path'} | ({'value'} if item['op'] != 'remove' else set())
        if set(item) != expected or not item['path'].startswith('/'):
            raise AppServiceError(422, 'Invalid migration fields or path')
        parts = item['path'][1:].split('/')
        if any('~' in part or not part for part in parts):
            raise AppServiceError(422, 'Migration paths must use simple object keys')
        parent = value
        for part in parts[:-1]:
            if not isinstance(parent, dict) or part not in parent: raise AppServiceError(422, 'Migration path does not exist')
            parent = parent[part]
        key = parts[-1]
        if not isinstance(parent, dict): raise AppServiceError(422, 'Migration paths must address objects')
        if item['op'] == 'add':
            if key in parent: raise AppServiceError(422, 'Migration add would overwrite existing data')
            parent[key] = copy.deepcopy(item['value'])
        else:
            if key not in parent: raise AppServiceError(422, 'Migration path does not exist')
            if item['op'] == 'remove': del parent[key]
            else: parent[key] = copy.deepcopy(item['value'])
    return value


class Releases:
    def __init__(self, config, lifecycle, services, catalog):
        self.config, self.lifecycle, self.services, self.catalog = config, lifecycle, services, catalog
        self.storage, self.registry = lifecycle.storage, lifecycle.registry
        self.root = config.data_dir / 'releases'
        self.root.mkdir(parents=True, exist_ok=True)
        self.plans = {}
        with self.storage.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, app_id TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, previous_version TEXT, checkpoint TEXT NOT NULL)')
        self.recover()

    def recover(self):
        for journal in self.root.glob('*/journal.json'):
            record = json.loads(journal.read_text(encoding='utf-8'))
            # Paths come from our UUID directory and validated app identity only.
            app_id = record['app_id']
            import re
            if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', app_id): raise RuntimeError('Invalid release recovery journal')
            with self.storage.connection() as db:
                committed = db.execute('SELECT 1 FROM releases WHERE id=?', (journal.parent.name,)).fetchone()
            target = self.config.installed_dir / app_id
            if not committed:
                if record['previous']:
                    digest = tree_digest(journal.parent / 'previous')
                    if record.get('previousDigest') and digest != record['previousDigest']:
                        raise RuntimeError('Recovery package failed integrity verification; retained both copies')
                if target.exists(): shutil.rmtree(target)
                if record['previous']:
                    copy_package(journal.parent / 'previous', target)
            displaced = journal.parent / 'displaced'
            if displaced.exists(): shutil.rmtree(displaced)
            journal.unlink()

    def _document(self, db, app_id):
        row = db.execute('SELECT * FROM documents WHERE app_id=?', (app_id,)).fetchone()
        return dict(row) if row else None

    def _check_package(self, folder, *, allow_legacy=False):
        tree_digest(folder)
        manifest = package_manifest(folder)
        if (manifest.schema_version != 2 and not allow_legacy) or 'process' in manifest.raw.get('runtime', {}) or manifest.active_runtime(self.lifecycle.platform) == 'process':
            raise AppServiceError(422, 'Release import currently supports v2 static, external and headless packages; native process packages use the trusted bundled workflow')
        if not manifest.supports(self.lifecycle.platform): raise AppServiceError(422, 'Release is incompatible with this platform')
        data = manifest.raw.get('data', {})
        refs = [manifest.web.entry if manifest.web else None, manifest.icon, data.get('schema'), data.get('legacy', {}).get('schema')]
        refs += [item['file'] for item in data.get('migrations', [])]
        refs += [data.get('legacyBundle', {}).get('schema')]
        refs += [item[key] for item in manifest.raw.get('actions', []) for key in ('inputSchema', 'outputSchema')]
        for ref in filter(None, refs):
            path = folder / portable(ref)
            if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
                raise AppServiceError(422, f'Release is missing a declared asset: {ref}')
        for ref in filter(None, [data.get('schema'), data.get('legacy', {}).get('schema')]):
            self.services.validate_data(manifest.id, None, ref, manifest=manifest, schema_only=True)
        for ref in filter(None, [data.get('legacyBundle', {}).get('schema')] + [item[key] for item in manifest.raw.get('actions', []) for key in ('inputSchema', 'outputSchema')]):
            self.services.validate_data(manifest.id, None, ref, manifest=manifest, schema_only=True)
        # Parse every declared migration before any installation can change.
        for migration in data.get('migrations', []):
            if migration['from'] >= migration['to']: raise AppServiceError(422, 'Migrations must increase schema version')
            json.loads((folder / migration['file']).read_text(encoding='utf-8'))
        return manifest

    def prepare(self, *, folder=None, archive=None, app_id=None, rollback=None):
        with self.lifecycle.lock:
            now = time.monotonic()
            for token, plan in list(self.plans.items()):
                if plan['expires'] < now:
                    shutil.rmtree(plan['folder'].parent, ignore_errors=True); del self.plans[token]
            if len(self.plans) >= 20: raise AppServiceError(429, 'Too many pending releases; cancel a review first')
            token = str(uuid.uuid4())
            stage = self.config.data_dir / 'staging' / token / 'package'
            source = {'kind': 'local', 'publisher': 'Unverified local package'}
            rollback_row = None
            try:
                if rollback:
                    with self.storage.connection() as db:
                        rollback_row = db.execute('SELECT * FROM releases WHERE id=? AND app_id=?', (rollback, app_id)).fetchone()
                    if not rollback_row or not rollback_row['previous_version']: raise AppServiceError(404, 'Previous release is unavailable')
                    checkpoint = json.loads(rollback_row['checkpoint'])
                    if tree_digest(self.root / rollback / 'previous') != checkpoint['previousDigest']:
                        raise AppServiceError(422, 'Retained rollback package failed integrity verification')
                    copy_package(self.root / rollback / 'previous', stage)
                    source = {'kind': 'rollback', 'release': rollback, 'publisher': 'Retained local release'}
                elif folder:
                    path = Path(folder).expanduser().resolve()
                    if not path.is_dir(): raise AppServiceError(404, 'App folder is unavailable on the engine')
                    if path == self.config.data_dir.resolve() or path in self.config.data_dir.resolve().parents:
                        raise AppServiceError(422, 'Cannot import an ancestor of engine data')
                    source['path'] = str(path)
                    copy_package(path, stage)
                else:
                    if app_id:
                        archive, entry = self.catalog.archive(app_id)
                        source = {'kind': 'catalog', 'publisher': entry['publisher'], 'catalog': self.catalog.source, 'sha256': entry['sha256']}
                    if not archive: raise AppServiceError(422, 'Select one release source')
                    source['archiveSha256'] = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
                    unpack(archive, stage)
                manifest = self._check_package(stage, allow_legacy=bool(rollback_row))
                if (self.config.installed_dir / manifest.id).exists() and not self.registry.is_installed(manifest.id):
                    raise AppServiceError(409, 'An incomplete installation directory exists; refusing to overwrite it')
                if app_id and manifest.id != app_id: raise AppServiceError(422, 'Release app identity mismatch')
                if source['kind'] == 'catalog' and manifest.raw != entry['manifest']:
                    raise AppServiceError(422, 'Archive manifest differs from the pinned catalog manifest')
                installed = self.registry.get(manifest.id) if self.registry.is_installed(manifest.id) else None
                if installed and 'process' in installed.raw.get('runtime', {}): raise AppServiceError(422, 'Cannot replace a native process through static release import')
                if installed and not rollback and tuple(map(int, manifest.version.split('.'))) <= tuple(map(int, installed.version.split('.'))):
                    raise AppServiceError(409, 'Updates require a newer version; use rollback to restore an earlier release')
                with self.storage.connection() as db: document = self._document(db, manifest.id)
                value, schema_version = self._next_data(manifest, document, rollback_row)
                plan = {'folder': stage, 'expires': now + 1200, 'manifest': manifest, 'source': source,
                        'digest': tree_digest(stage), 'previousDigest': tree_digest(installed.path) if installed else None,
                        'revision': document['revision'] if document else 0, 'value': value, 'schemaVersion': schema_version,
                        'hasDocument': document is not None or (rollback_row is not None and json.loads(rollback_row['checkpoint'])['document'] is not None),
                        'rollback': dict(rollback_row) if rollback_row else None}
                self.plans[token] = plan
                old_caps = set(installed.capabilities if installed else [])
                return {'review': token, 'id': manifest.id, 'name': manifest.name, 'version': manifest.version,
                        'previousVersion': installed.version if installed else None, 'digest': plan['digest'], 'source': source,
                        'capabilities': manifest.capabilities, 'newCapabilities': sorted(set(manifest.capabilities) - old_caps),
                        'operations': manifest.raw.get('connection', {}).get('operations', []),
                        'revision': plan['revision'], 'schemaVersion': schema_version,
                        'dataChanges': value != (json.loads(document['value']) if document else None) or (document and document['schema_version'] != schema_version),
                        'rollback': bool(rollback), 'trustedLegacy': manifest.schema_version == 1, 'expiresIn': 1200}
            except (OSError, ValueError, ManifestError) as exc:
                shutil.rmtree(stage.parent, ignore_errors=True)
                raise AppServiceError(422, str(exc)) from exc
            except Exception:
                shutil.rmtree(stage.parent, ignore_errors=True)
                raise

    def _next_data(self, manifest, document, rollback):
        schema = manifest.raw.get('data', {}).get('schemaVersion', 1)
        if rollback:
            previous = json.loads(rollback['checkpoint'])['document']
            value = json.loads(previous['value']) if previous else None
            # No prior document means an empty state, represented by JSON null.
            if previous: schema = previous['schema_version']
        else:
            value = json.loads(document['value']) if document else None
            if document and document['schema_version'] != schema:
                migrations = manifest.raw.get('data', {}).get('migrations', [])
                matches = [item for item in migrations if item['from'] == document['schema_version'] and item['to'] == schema]
                if len(matches) != 1: raise AppServiceError(409, 'Release needs one explicit migration from the stored schema to the target schema')
                value = migrate_value(value, matches[0], manifest.path)
        if value is not None:
            self.services.validate_data(manifest.id, value, manifest=manifest)
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > manifest.raw.get('data', {}).get('quotaBytes', 1048576): raise AppServiceError(413, 'Migrated data exceeds release quota')
        return value, schema

    def cancel(self, token):
        with self.lifecycle.lock:
            plan = self.plans.pop(token, None)
            if plan: shutil.rmtree(plan['folder'].parent, ignore_errors=True)
        return {'cancelled': True}

    def commit(self, token, capabilities, operations):
        with self.lifecycle.lock:
            plan = self.plans.get(token)
            if not plan or plan['expires'] < time.monotonic(): raise AppServiceError(409, 'Release review expired; review again')
            manifest = plan['manifest']; app_id = manifest.id
            if sorted(capabilities) != sorted(manifest.capabilities) or sorted(operations) != sorted(manifest.raw.get('connection', {}).get('operations', [])):
                raise AppServiceError(403, 'Approve exactly the capabilities and operations shown in this release review')
            target = self.config.installed_dir / app_id
            if target.exists() and not self.registry.is_installed(app_id):
                raise AppServiceError(409, 'Installation directory changed since review')
            current_digest = tree_digest(target) if self.registry.is_installed(app_id) else None
            if current_digest != plan['previousDigest'] or tree_digest(plan['folder']) != plan['digest']:
                raise AppServiceError(409, 'Package changed since review; review again')
            # Validate again immediately before activation, using immutable staged bytes.
            self._check_package(plan['folder'], allow_legacy=bool(plan['rollback']))
            release_id = str(uuid.uuid4()); record = self.root / release_id
            record.mkdir()
            previous = package_manifest(target) if current_digest else None
            if previous: copy_package(target, record / 'previous')
            copy_package(plan['folder'], record / 'package')
            copy_package(plan['folder'], record / 'next')
            journal = record / 'journal.json'
            try:
                with self.storage.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    document = self._document(db, app_id)
                    if (document['revision'] if document else 0) != plan['revision']:
                        raise AppServiceError(409, 'App data changed since review; review again')
                    markers = [dict(row) for row in db.execute('SELECT * FROM migrations WHERE app_id=?', (app_id,)).fetchall()]
                    checkpoint = {'document': document, 'migrations': markers, 'previousDigest': current_digest}
                    if document: self.storage._snapshot(db, app_id, document, 'Before release change')
                    if plan['hasDocument']:
                        db.execute('INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)', (app_id, json.dumps(plan['value'], ensure_ascii=False, allow_nan=False), plan['revision'] + 1, plan['schemaVersion']))
                    if plan['rollback']:
                        db.execute('DELETE FROM migrations WHERE app_id=?', (app_id,))
                        for marker in json.loads(plan['rollback']['checkpoint'])['migrations']:
                            db.execute('INSERT INTO migrations VALUES (?, ?, ?, ?)', (app_id, marker['digest'], marker['original'], marker['created_at']))
                    db.execute('INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (release_id, app_id, manifest.version, plan['digest'], json.dumps(plan['source']), datetime.now(timezone.utc).isoformat(), previous.version if previous else None, json.dumps(checkpoint)))
                    identity = db.execute('SELECT * FROM installations WHERE app_id=?', (app_id,)).fetchone()
                    if not identity or not identity['active']:
                        db.execute('INSERT OR REPLACE INTO installations VALUES (?, ?, 1)', (app_id, str(uuid.uuid4())))
                    if 'connections' not in manifest.capabilities:
                        db.execute('DELETE FROM connections WHERE identity IN (SELECT identity FROM installations WHERE app_id=?)', (app_id,))
                    write_json(journal, {'app_id': app_id, 'previous': bool(previous), 'previousDigest': current_digest})
                    if target.exists(): replace_dir(target, record / 'displaced')
                    replace_dir(record / 'next', target)
                    # File-level readiness is checked at the final serving path too.
                    self._check_package(target, allow_legacy=bool(plan['rollback']))
                self.lifecycle.auth.revoke_app(app_id)
                self.recover()
                self.cancel(token)
                return {'id': app_id, 'version': manifest.version, 'release': release_id, 'installed': True}
            except Exception:
                self.recover()
                raise

    def history(self, app_id):
        with self.storage.connection() as db:
            return {'releases': [dict(row) for row in db.execute('SELECT id, version, digest, source, created_at, previous_version FROM releases WHERE app_id=? ORDER BY created_at DESC', (app_id,)).fetchall()]}
