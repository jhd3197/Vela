"""Side effects an automation may ask Vela to perform, and the checks in front.

The worker never performs an effect. It sends a request over its pipe and Vela
decides, here, using the workflow revision the run is pinned to. Authorization is
re-checked on every call and on every retry, so revoking a permission stops the
next write even while a run is in flight.
"""
import asyncio
import hashlib
import json

from ..actions import fingerprint as manifest_fingerprint
from ..app_storage import AppServiceError
from ..notify import NotifyError
from . import catalog as node_catalog
from . import validate


def request_contract(document, app, action) -> str:
    """Digest of what a workflow asks one app to do.

    Covers the app, the action, which steps call it and which inputs those steps
    fill in — the shape the user reviews. It deliberately excludes the text of
    each input, so editing the wording of a note does not re-open a permission
    prompt, while adding a step or a new input field does.
    """
    node_type = node_catalog.app_action_node_id(app, action)
    steps = []
    for node in document['nodes']:
        if node['type'] != node_type:
            continue
        steps.append({
            'id': node['id'],
            'inputs': sorted(key[len('input.'):] for key in node['config']
                             if key.startswith('input.')),
        })
    payload = {'app': app, 'action': action, 'steps': sorted(steps, key=lambda step: step['id'])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def retry_key(run_id, node_id, iteration=0) -> str:
    """Stable per-effect key: the same effect in the same run reuses it.

    A resumed run keeps its run id, so a retry replays the stored receipt. A new
    run gets a new id and therefore a new key, which is what makes "run it again"
    different from "retry the step".
    """
    seed = f'{run_id}:{node_id}:{iteration}'.encode('utf-8')
    return 'auto-' + hashlib.sha256(seed).hexdigest()[:48]


class Effects:
    """Performs authorized effects for one server."""

    def __init__(self, store, actions, registry, notifier, settings):
        self.store = store
        self.actions = actions
        self.registry = registry
        self.notifier = notifier
        self.settings = settings

    # --------------------------------------------------------------- grants --

    def review(self, workflow_id, document):
        """Every app action this document asks for, with its current state."""
        grants = self.store.grants(workflow_id)
        requests = []
        for declared in validate.declared_app_actions(document):
            app, action_id = declared['app'], declared['action']
            entry = {'app': app, 'action': action_id, 'nodes': declared['nodes'],
                     'granted': False, 'available': False}
            manifest = self.registry.get(app)
            if not manifest or not self.registry.is_installed(app):
                entry['error'] = 'That app is not installed on this server.'
                requests.append(entry)
                continue
            action = next((item for item in manifest.raw.get('actions', [])
                           if item['id'] == action_id), None)
            if not action or 'actions' not in manifest.capabilities:
                entry['error'] = f'{manifest.name} no longer offers this action.'
                requests.append(entry)
                continue
            contract = request_contract(document, app, action_id)
            target = manifest_fingerprint(manifest)
            entry.update(available=True, appName=manifest.name, title=action['title'],
                         effect=action['effect'], requestContract=contract, targetContract=target,
                         inputs=sorted({key[len('input.'):] for node in document['nodes']
                                        if node['type'] == node_catalog.app_action_node_id(app, action_id)
                                        for key in node['config'] if key.startswith('input.')}))
            existing = grants.get((app, action_id))
            if existing:
                entry['granted'] = (existing['request_contract'] == contract
                                    and existing['target_contract'] == target)
                if not entry['granted']:
                    entry['stale'] = True
                    entry['staleReason'] = (
                        'This step changed since you allowed it.'
                        if existing['request_contract'] != contract
                        else f'{manifest.name} was updated since you allowed it.')
            requests.append(entry)
        return requests

    def grant(self, workflow_id, document, app, action_id, allow, request_contract_seen,
              target_contract_seen):
        if not allow:
            self.store.revoke_grant(workflow_id, app, action_id)
            return
        manifest = self.registry.get(app)
        if not manifest or not self.registry.is_installed(app):
            raise AppServiceError(404, 'That app is not installed on this server.')
        action = next((item for item in manifest.raw.get('actions', []) if item['id'] == action_id), None)
        if not action or 'actions' not in manifest.capabilities or 'storage' not in manifest.capabilities:
            raise AppServiceError(404, f'{manifest.name} does not offer that action.')
        contract = request_contract(document, app, action_id)
        target = manifest_fingerprint(manifest)
        if not any(node['type'] == node_catalog.app_action_node_id(app, action_id)
                   for node in document['nodes']):
            raise AppServiceError(409, 'This automation no longer uses that action.')
        if request_contract_seen != contract or target_contract_seen != target:
            raise AppServiceError(409, 'This request changed since it was shown to you. '
                                       'Reload and review it again before allowing it.')
        identity = self.actions.storage.activate(app)
        self.store.save_grant(workflow_id, app, action_id, contract, target, identity)

    def check_grant(self, workflow_id, document, app, action_id):
        """Raise unless a current grant covers this exact request. Used at call time."""
        manifest = self.registry.get(app)
        if not manifest or not self.registry.is_installed(app):
            raise AppServiceError(403, f'“{app}” is not installed, so this step cannot run.')
        grant = self.store.grants(workflow_id).get((app, action_id))
        if not grant:
            raise AppServiceError(403, f'This automation is not allowed to use {manifest.name}. '
                                       'Review and allow the request in Vela first.')
        if grant['request_contract'] != request_contract(document, app, action_id):
            raise AppServiceError(403, 'This step changed since you allowed it. '
                                       'Review the request again before running it.')
        if grant['target_contract'] != manifest_fingerprint(manifest):
            raise AppServiceError(403, f'{manifest.name} was updated since you allowed this. '
                                       'Review the request again before running it.')
        return grant

    # -------------------------------------------------------------- effects --

    async def perform(self, *, workflow_id, run_id, revision_document, kind, node_id, payload):
        if kind == 'app-action':
            # The broker takes a process-wide lock and writes SQLite; keep that
            # off the server's event loop so other requests keep responding.
            return await asyncio.to_thread(self._app_action, workflow_id, run_id,
                                           revision_document, node_id, payload)
        if kind == 'notify':
            return await self._notify(workflow_id, run_id, node_id, payload)
        raise AppServiceError(400, f'Vela does not support the “{kind}” step.')

    def _app_action(self, workflow_id, run_id, document, node_id, payload):
        app = str(payload.get('app', ''))
        action_id = str(payload.get('action', ''))
        value = payload.get('input')
        if not isinstance(value, dict):
            raise AppServiceError(422, 'This step did not produce an object to send.')
        node = next((item for item in document['nodes'] if item['id'] == node_id), None)
        if node is None or node['type'] != node_catalog.app_action_node_id(app, action_id):
            raise AppServiceError(403, 'This step is not part of the version being run.')
        value = self._coerce_input(app, action_id, value)
        grant = self.check_grant(workflow_id, document, app, action_id)

        def authorize(target_manifest, target_identity):
            # Re-run inside the write transaction so a revocation a moment ago
            # still stops the write, and confirm the installation has not been
            # replaced since the grant was given.
            current = self.check_grant(workflow_id, document, app, action_id)
            if current['installation'] != target_identity:
                raise AppServiceError(403, f'{target_manifest.name} was reinstalled since you '
                                           'allowed this. Review the request again.')

        del grant
        result = self.actions.invoke_for_automation(
            workflow_id, app, action_id, value, retry_key(run_id, node_id), authorize)
        return {'ok': True, 'recordId': result['output'].get('recordId'),
                'replayed': result.get('replayed', False)}

    def _coerce_input(self, app, action_id, value):
        """Turn the rendered field values into the types the action's schema wants."""
        manifest = self.registry.get(app)
        action = next((item for item in (manifest.raw.get('actions') or [])
                       if item['id'] == action_id), None) if manifest else None
        if not action:
            return value
        definition = node_catalog.app_action_definition(manifest, action)
        if not definition:
            raise AppServiceError(403, 'Vela cannot show this action for review, so it will not run it.')
        wanted = {field['key'][len('input.'):]: field['type'] for field in definition['fields']}
        coerced = {}
        for key, raw in value.items():
            kind = wanted.get(key)
            if kind is None:
                raise AppServiceError(422, f'“{key}” is not an input of this action.')
            if kind == 'number':
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    raise AppServiceError(422, f'“{key}” must be a number.') from None
                coerced[key] = int(number) if number.is_integer() else number
            elif kind == 'boolean':
                coerced[key] = raw is True or str(raw).strip().lower() in ('true', 'yes', '1')
            else:
                coerced[key] = '' if raw is None else str(raw)
        return coerced

    async def _notify(self, workflow_id, run_id, node_id, payload):
        """Publish through Vela's configured notifier.

        Delivery leaves this computer, so it cannot be exactly-once. Vela records
        the attempt before publishing: a retry of a request that was in flight
        when the server stopped is reported as unknown rather than sent again.
        """
        key = retry_key(run_id, node_id)
        existing = self.store.find_receipt(key)
        if existing:
            stored = json.loads(existing['result'])
            if stored.get('status') == 'attempted':
                return {'ok': False, 'status': 'unknown',
                        'detail': 'Vela stopped while this notification was being sent, so it is '
                                  'not resending it. Check your notifications.'}
            return {**stored, 'replayed': True}
        destination = self.notifier.config()
        if not destination['server'] or not destination['topic']:
            raise AppServiceError(409, 'Set up notifications in Vela’s settings before using this step.')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.store.save_receipt(key, workflow_id, run_id, node_id, 'vela', 'notify', digest,
                                {'status': 'attempted'})
        try:
            receipt = await self.notifier.publish(
                payload.get('title') or 'Vela automation', payload.get('message') or '',
                tags=['gear'], priority=int(payload.get('priority', 3)), kind='automation')
        except NotifyError as exc:
            self.store.save_receipt(key, workflow_id, run_id, node_id, 'vela', 'notify', digest,
                                    {'status': 'failed', 'detail': str(exc)})
            raise AppServiceError(502, str(exc)) from exc
        result = {'ok': True, 'status': 'sent', 'id': receipt['id'],
                  'acceptedAt': receipt['accepted_at'],
                  'destination': f"{destination['server']}/{destination['topic']}"}
        self.store.save_receipt(key, workflow_id, run_id, node_id, 'vela', 'notify', digest, result)
        return result

    def notification_destination(self) -> dict:
        """What the notification step would actually do, in plain terms."""
        config = self.notifier.config()
        configured = bool(config['server'] and config['topic'])
        return {
            'configured': configured,
            'server': config['server'],
            'topic': config['topic'],
            'detail': (f"Notifications are published to {config['server']}/{config['topic']}. "
                       'That server receives the title and message.') if configured else
                      'No notification server is set up yet. Add one in Settings before using '
                      'this step.',
        }
