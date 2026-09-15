"""Starting points that only appear when their prerequisites are real.

A blueprint is a draft, not a promise. It is offered only when this server can
actually run it — the app it writes to is installed, the notification server is
configured — and it always arrives switched off, with nothing allowed yet, so the
usual review still happens before anything can change.
"""
from . import catalog as node_catalog


def _manual(payload='{}'):
    return {'id': 'start', 'type': 'manual-trigger', 'config': {'payload': payload}}


def _schedule():
    return {'id': 'start', 'type': 'vela-schedule-trigger',
            'config': {'every': 1, 'unit': 'days', 'atTime': '09:00',
                       'weekday': 'monday', 'timezone': ''}}


def _document(nodes, edges):
    return {'version': 1, 'nodes': nodes, 'edges': edges, 'meta': {}}


def available(registry, notifier) -> list[dict]:
    """Blueprints this installation can honour right now, with a reason when not."""
    catalog = node_catalog.build(registry)
    items = []

    notify = notifier.config()
    configured = bool(notify['server'] and notify['topic'])
    items.append({
        'id': 'scheduled-notification',
        'name': 'A daily reminder',
        'description': 'Sends one notification every morning at a time you pick.',
        'available': configured,
        'requirement': None if configured else
                       'Set up a notification server in Settings first.',
        'document': _document(
            [
                _schedule(),
                {'id': 'remind', 'type': 'vela-notify',
                 'config': {'title': 'Daily reminder', 'message': 'Time for your check-in.',
                            'priority': 3}},
            ],
            [{'id': 'e1', 'source': 'start', 'target': 'remind'}]),
    })

    for definition in catalog['nodes']:
        if not definition['id'].startswith(node_catalog.APP_ACTION_PREFIX + ':'):
            continue
        _, app, action = definition['id'].split(':', 2)
        manifest = registry.get(app)
        if manifest is None:
            continue
        fields = [field['key'] for field in definition['fields']]
        items.append({
            'id': f'manual-{app}-{action}',
            'name': f'Send text to {manifest.name}',
            'description': f'You type the details and press Run; {manifest.name} '
                           f'{definition["operationName"].lower()}.',
            'available': True,
            'requirement': None,
            'document': _document(
                [
                    _manual('{\n  "title": "",\n  "body": ""\n}'),
                    {'id': 'write', 'type': definition['id'],
                     'config': {key: '{{' + key[len('input.'):] + '}}' for key in fields}},
                ],
                [{'id': 'e1', 'source': 'start', 'target': 'write'}]),
        })

    return items


def find(registry, notifier, blueprint_id):
    for item in available(registry, notifier):
        if item['id'] == blueprint_id:
            return item
    return None
