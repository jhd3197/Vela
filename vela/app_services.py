"""App sessions and storage authorization, independent of HTTP handlers."""

from .app_storage import AppServiceError
import hashlib
import json
import uuid


def _digest(value):
    """What was asked for, as one string, so a grant can name exactly it."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode()).hexdigest()
from jsonschema import Draft202012Validator, SchemaError


class AppServices:
    """App sessions and storage authorization.

    `guard` answers "what has to be true for this caller to change this, and
    when does it have to be true". For a person it is None and nothing changes.
    For an agent it returns a check that the write transaction runs before it
    writes, so a permission taken away a moment earlier stops the write rather
    than losing a race with it.
    """

    def __init__(self, registry, auth, storage, guard=None):
        self.registry = registry
        self.auth = auth
        self.storage = storage
        self.guard = guard or (lambda *args, **kwargs: None)

    def open(self, app_id, owner=None):
        manifest = self.registry.get(app_id)
        if not manifest or not self.registry.is_installed(app_id):
            raise AppServiceError(404, "App is not installed")
        if manifest.schema_version != 2 or manifest.view["surface"] != "embedded":
            raise AppServiceError(400, "Only v2 embedded apps use the bridge")
        return self.auth.issue(manifest, self.storage.activate(app_id), owner)

    def authorize_storage(self, session):
        if not self.registry.is_installed(session["app_id"]):
            raise AppServiceError(401, "App is no longer installed")
        if "storage" not in session["capabilities"]:
            raise AppServiceError(403, "Storage capability was not granted")

    def read(self, session):
        self.authorize_storage(session)
        return self.storage.read(session["installationId"], session["schemaVersion"])

    def write(self, session, value, revision):
        self.authorize_storage(session)
        self.validate_data(session["app_id"], value)
        authorize = self.guard(session, "write", request_digest=_digest(value))
        return self.storage.write(session["installationId"], value, revision, session["schemaVersion"], session["quota"], authorize=authorize)

    def validate_data(self, app_id, value, schema_file=None, *, manifest=None, schema_only=False):
        manifest = manifest or self.registry.get(app_id)
        if not manifest:
            raise AppServiceError(404, "App is not installed")
        schema_file = schema_file or manifest.raw.get("data", {}).get("schema")
        if not schema_file:
            return
        path = (manifest.path / schema_file).resolve()
        if not path.is_relative_to(manifest.path.resolve()):
            raise AppServiceError(422, "Data schema path escapes the app package")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            # External references cannot turn data validation into network access.
            def check_refs(node):
                if isinstance(node, dict):
                    for ref in ("$ref", "$dynamicRef", "$recursiveRef"):
                        if ref in node and (not isinstance(node[ref], str) or not node[ref].startswith("#")):
                            raise ValueError("Only document-local schema references are supported")
                    for child in node.values(): check_refs(child)
                elif isinstance(node, list):
                    for child in node: check_refs(child)
            check_refs(schema)
            Draft202012Validator.check_schema(schema)
            if schema_only:
                return
            errors = list(Draft202012Validator(schema).iter_errors(value))
            if errors:
                raise ValueError("Data does not match the app's schema at " + "/".join(map(str, errors[0].absolute_path)))
        except (OSError, ValueError, SchemaError) as exc:
            raise AppServiceError(422, str(exc)) from exc

    def snapshots(self, session):
        self.authorize_storage(session)
        return {"snapshots": self.storage.snapshots(session["installationId"])}

    def snapshot(self, session):
        self.authorize_storage(session)
        return self.storage.snapshot(session["installationId"], authorize=self.guard(session, "write"))

    def restore(self, session, snapshot_id, revision):
        self.authorize_storage(session)
        saved = self.storage.get_snapshot(session["installationId"], snapshot_id)
        if saved["schemaVersion"] != session["schemaVersion"]:
            raise AppServiceError(409, "Backup needs a schema migration before restoring")
        self.validate_data(session["app_id"], saved["value"])
        # Restoring replaces everything. Permission to save is not permission to
        # do that, so it has its own effect class rather than riding on `write`.
        authorize = self.guard(session, "restore", scope={"snapshot": snapshot_id})
        return self.storage.write(session["installationId"], saved["value"], revision, session["schemaVersion"], session["quota"], snapshot_reason="Before restore", authorize=authorize)

    def migrate(self, app_id, value, revision, *, commit=False):
        manifest = self.registry.get(app_id)
        if not manifest or not self.registry.is_installed(app_id):
            raise AppServiceError(404, "App is not installed")
        bundle = manifest.raw.get('data', {}).get('legacyBundle')
        if bundle:
            return self.migrate_bundle(manifest, bundle, value, revision, commit)
        spec = manifest.raw.get("data", {}).get("legacy")
        if not spec or "storage" not in manifest.capabilities:
            raise AppServiceError(403, "This app has no declared legacy import")
        if not spec["key"].startswith(f"vela.{app_id}."):
            raise AppServiceError(403, "Legacy key is outside the app namespace")
        self.validate_data(app_id, value, spec["schema"])
        if not isinstance(value, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in value):
            raise AppServiceError(422, "Legacy import requires records with string IDs")
        if len({item["id"] for item in value}) != len(value):
            raise AppServiceError(422, "Legacy record IDs must be unique")
        original = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        quota = manifest.raw["data"].get("quotaBytes", 1048576)
        if len(original.encode()) > quota:
            raise AppServiceError(413, "Legacy import exceeds the app quota")
        digest = hashlib.sha256((spec["key"] + original).encode()).hexdigest()
        identity = self.storage.activate(app_id)
        schema_version = manifest.raw["data"]["schemaVersion"]
        current = self.storage.read(identity, schema_version)
        if self.storage.migration_done(identity, digest):
            return {**current, "alreadyImported": True, "added": 0, "conflicts": 0}
        merged = dict(current["value"] or {})
        records = list(merged.get(spec["field"], []))
        existing = {item["id"]: item for item in records}
        added, conflicts = 0, 0
        for item in value:
            if item["id"] in existing:
                if item == existing[item["id"]]: continue
                item = {**item, "id": str(uuid.uuid4())}
                conflicts += 1
            records.append(item)
            added += 1
        merged[spec["field"]] = records
        self.validate_data(app_id, merged)
        if not commit:
            return {"revision": current["revision"], "added": added, "conflicts": conflicts, "alreadyImported": False}
        return self.storage.write(identity, merged, revision, schema_version, quota,
                                  snapshot_reason="Before legacy import", migration=(digest, original))

    def migrate_bundle(self, manifest, spec, value, revision, commit):
        if 'storage' not in manifest.capabilities: raise AppServiceError(403, 'Storage grant required')
        self.validate_data(manifest.id, value, spec['schema'])
        original = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256((json.dumps(spec['keys'], sort_keys=True) + original).encode()).hexdigest()
        identity = self.storage.activate(manifest.id)
        version = manifest.raw['data']['schemaVersion']
        current = self.storage.read(identity, version)
        if self.storage.migration_done(identity, digest):
            return {**current, 'alreadyImported': True, 'added': 0, 'conflicts': 0}
        import copy
        merged = copy.deepcopy(current['value'] if current['value'] is not None else spec['initial'])
        records = merged.setdefault(spec['field'], [])
        records.append({'id': digest, **value})
        self.validate_data(manifest.id, merged)
        quota = manifest.raw['data'].get('quotaBytes', 1048576)
        if len(json.dumps(merged).encode()) > quota: raise AppServiceError(413, 'Import exceeds app quota')
        if not commit: return {'revision': current['revision'], 'added': 1, 'conflicts': 0, 'alreadyImported': False}
        return self.storage.write(identity, merged, revision, version, quota, snapshot_reason='Before legacy import', migration=(digest, original))
