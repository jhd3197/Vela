"""The one vetted node catalog shared by editor validation and execution.

Every node Vela is willing to save, activate and run is declared here. The
dashboard renders these definitions through Tramo's editor, the server validates
saved documents against them, and the worker refuses any node type it has no
executor for. Three agreeing lists is the point: a node cannot be added to the
picker without also being validated and executable.

Tramo ships many more nodes. The ones that compile configuration strings with
`new Function` (`js-transform`, `if`, `switch`, `json-parse`, the loop family,
the state variables, `call-flow`) and the ones that reach the network directly
(`http-request`, `mcp-tool-call`, the AI nodes) are deliberately absent. A
separate process is not a sandbox, so they are excluded from the registry rather
than hidden in the interface.
"""
import hashlib
import json

# Bump when the meaning of a stored configuration changes, not when help text
# is edited. Runs record the catalog version they executed against.
CATALOG_VERSION = 1

_COLORS = {
    'trigger': '#10b981',
    'action': '#3b82f6',
    'transform': '#a855f7',
    'logic': '#f59e0b',
}

#: Comparisons the declarative condition node offers. Mirrored by the worker.
COMPARISON_OPERATORS = (
    'equals', 'not-equals', 'contains', 'not-contains', 'starts-with', 'ends-with',
    'greater-than', 'greater-or-equal', 'less-than', 'less-or-equal',
    'is-empty', 'is-not-empty', 'is-true', 'is-false',
)
UNARY_OPERATORS = frozenset({'is-empty', 'is-not-empty', 'is-true', 'is-false'})

#: Node types that may start a workflow, and how Vela dispatches each one.
TRIGGER_NODES = {
    'manual-trigger': 'manual',
    'vela-schedule-trigger': 'schedule',
    'vela-webhook-trigger': 'webhook',
}

APP_ACTION_PREFIX = 'vela-app-action'

#: Graph bounds. A personal server should refuse a runaway document early.
MAX_NODES = 60
MAX_EDGES = 120
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_TEXT_FIELD_BYTES = 32 * 1024

_SCHEDULE_UNITS = ('minutes', 'hours', 'days', 'weeks')


def _option(value, label=None):
    return {'label': label or value, 'value': value}


STATIC_NODES = [
    {
        'id': 'manual-trigger',
        'name': 'Run manually',
        'category': 'trigger',
        'description': 'Starts when you press Run in Vela.',
        'icon': 'Play',
        'color': _COLORS['trigger'],
        'inputs': [],
        'outputs': [{'key': 'out', 'label': 'Started', 'type': 'object'}],
        'fields': [{
            'key': 'payload',
            'type': 'json',
            'label': 'Starting value',
            'default': '{}',
            'help': 'JSON handed to the first step. Later steps read it with {{field}}.',
        }],
    },
    {
        'id': 'vela-schedule-trigger',
        'name': 'On a schedule',
        'category': 'trigger',
        'description': 'Starts on a repeating schedule that Vela keeps, in a timezone you choose.',
        'icon': 'Clock',
        'color': _COLORS['trigger'],
        'inputs': [],
        'outputs': [{'key': 'out', 'label': 'Fired', 'type': 'object'}],
        'fields': [
            {'key': 'every', 'type': 'number', 'label': 'Repeat every', 'default': 1,
             'help': 'How many units between runs. Minimum one minute.'},
            {'key': 'unit', 'type': 'select', 'label': 'Unit', 'default': 'hours',
             'options': [_option(unit, unit.capitalize()) for unit in _SCHEDULE_UNITS]},
            {'key': 'atTime', 'type': 'text', 'label': 'At time (HH:MM)', 'default': '09:00',
             'optional': True, 'help': 'Used for daily and weekly schedules. Ignored for minutes and hours.'},
            {'key': 'weekday', 'type': 'select', 'label': 'Day of the week', 'default': 'monday',
             'optional': True,
             'options': [_option(day, day.capitalize()) for day in
                         ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')]},
            {'key': 'timezone', 'type': 'text', 'label': 'Timezone', 'default': '',
             'optional': True,
             'help': "An IANA name such as America/New_York. Empty uses the server's timezone."},
        ],
    },
    {
        'id': 'vela-webhook-trigger',
        'name': 'On a web request',
        'category': 'trigger',
        'description': 'Starts when an authenticated request arrives at this workflow’s Vela address.',
        'icon': 'CloudDownload',
        'color': _COLORS['trigger'],
        'inputs': [],
        'outputs': [{'key': 'out', 'label': 'Request', 'type': 'object'}],
        'fields': [{
            'key': 'note',
            'type': 'text',
            'label': 'Note',
            'default': '',
            'optional': True,
            'help': 'Vela issues the address and token when you activate the workflow. '
                    'Requests are accepted only on the addresses Vela already listens on.',
        }],
    },
    {
        'id': 'template',
        'name': 'Text',
        'category': 'transform',
        'description': 'Builds a piece of text from the incoming value.',
        'icon': 'Type',
        'color': _COLORS['transform'],
        'inputs': [{'key': 'in', 'label': 'Value', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Text', 'type': 'string'}],
        'fields': [{
            'key': 'template',
            'type': 'textarea',
            'label': 'Text',
            'default': 'Hello {{name}}',
            'help': 'Use {{field}} for a value from the previous step, or {{steps.step_name.field}} '
                    'for an earlier one. A missing field renders as nothing.',
        }],
    },
    {
        'id': 'json-stringify',
        'name': 'Value as JSON text',
        'category': 'transform',
        'description': 'Turns the incoming value into JSON text.',
        'icon': 'Code',
        'color': _COLORS['transform'],
        'inputs': [{'key': 'in', 'label': 'Value', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Text', 'type': 'string'}],
        'fields': [{'key': 'indent', 'type': 'number', 'label': 'Indent spaces', 'default': 0,
                    'help': '0 keeps it on one line. 2 makes it readable.'}],
    },
    {
        'id': 'vela-condition',
        'name': 'Only if',
        'category': 'logic',
        'description': 'Sends the value down one branch or the other by comparing a single field.',
        'icon': 'GitBranch',
        'color': _COLORS['logic'],
        'inputs': [{'key': 'in', 'label': 'Value', 'type': 'any'}],
        'outputs': [
            {'key': 'true', 'label': 'Matches', 'type': 'any'},
            {'key': 'false', 'label': 'Does not match', 'type': 'any'},
        ],
        'fields': [
            {'key': 'subject', 'type': 'text', 'label': 'Field', 'default': 'status',
             'help': 'A field path such as status or user.email. Values are read, never evaluated as code.'},
            {'key': 'operator', 'type': 'select', 'label': 'Comparison', 'default': 'equals',
             'options': [_option(operator, operator.replace('-', ' ').capitalize())
                         for operator in COMPARISON_OPERATORS]},
            {'key': 'value', 'type': 'text', 'label': 'Compare with', 'default': '', 'optional': True,
             'help': 'Supports {{field}}. Not used by the "is empty" and "is true" comparisons.'},
            {'key': 'valueType', 'type': 'select', 'label': 'Compare as', 'default': 'string',
             'options': [_option('string', 'Text'), _option('number', 'Number'),
                         _option('boolean', 'Yes / no'), _option('json', 'JSON')]},
        ],
    },
    {
        'id': 'merge',
        'name': 'Bring branches together',
        'category': 'logic',
        'description': 'Joins the values arriving from several branches into one.',
        'icon': 'ArrowsMerge',
        'color': _COLORS['logic'],
        'inputs': [
            {'key': 'in', 'label': 'A', 'type': 'any'},
            {'key': 'b', 'label': 'B', 'type': 'any'},
        ],
        'outputs': [{'key': 'out', 'label': 'Joined', 'type': 'any'}],
        'fields': [{'key': 'mode', 'type': 'select', 'label': 'Join as', 'default': 'object',
                    'options': [_option('object', 'One combined object'), _option('array', 'A list'),
                                _option('first', 'The first value that arrived')]}],
    },
    {
        'id': 'delay',
        'name': 'Wait',
        'category': 'action',
        'description': 'Pauses before the next step. Cancelling the run ends the wait.',
        'icon': 'Hourglass',
        'color': _COLORS['action'],
        'inputs': [{'key': 'in', 'label': 'In', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Out', 'type': 'any'}],
        'fields': [{'key': 'ms', 'type': 'number', 'label': 'Wait (milliseconds)', 'default': 1000,
                    'help': 'At most five minutes. The run keeps its slot while it waits.'}],
    },
    {
        'id': 'log',
        'name': 'Note in the run log',
        'category': 'action',
        'description': 'Writes the incoming value into this run’s log and passes it on.',
        'icon': 'StickyNote',
        'color': _COLORS['action'],
        'inputs': [{'key': 'in', 'label': 'In', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Out', 'type': 'any'}],
        'fields': [
            {'key': 'level', 'type': 'select', 'label': 'Level', 'default': 'info',
             'options': [_option(level) for level in ('debug', 'info', 'warn', 'error')]},
            {'key': 'prefix', 'type': 'text', 'label': 'Label', 'default': '', 'optional': True},
        ],
    },
    {
        'id': 'vela-notify',
        'name': 'Send a notification',
        'category': 'action',
        'description': 'Publishes through the notification service configured in Vela’s settings.',
        'icon': 'Bell',
        'color': _COLORS['action'],
        'inputs': [{'key': 'in', 'label': 'In', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Receipt', 'type': 'object'}],
        'fields': [
            {'key': 'title', 'type': 'text', 'label': 'Title', 'default': 'Vela automation'},
            {'key': 'message', 'type': 'textarea', 'label': 'Message', 'default': '', 'optional': True,
             'help': 'Supports {{field}} from the previous step.'},
            {'key': 'priority', 'type': 'number', 'label': 'Priority (1–5)', 'default': 3},
        ],
    },
    {
        'id': 'approval-gate',
        'name': 'Wait for your approval',
        'category': 'logic',
        'description': 'Pauses the run until you approve or reject it in Vela.',
        'icon': 'ShieldCheck',
        'color': _COLORS['logic'],
        'inputs': [{'key': 'in', 'label': 'In', 'type': 'any'}],
        'outputs': [
            {'key': 'approved', 'label': 'Approved', 'type': 'any'},
            {'key': 'rejected', 'label': 'Rejected', 'type': 'any'},
        ],
        'fields': [
            {'key': 'message', 'type': 'textarea', 'label': 'What are you approving?',
             'default': 'Approve this step?', 'help': 'Supports {{field}}.'},
            {'key': 'timeoutSec', 'type': 'number', 'label': 'Expire after (seconds)', 'default': 0,
             'optional': True, 'help': '0 keeps the request open until you decide.'},
        ],
    },
]

STATIC_NODE_IDS = frozenset(node['id'] for node in STATIC_NODES)

#: Config keys each static node accepts. Anything else is rejected on save.
_ALLOWED_KEYS = {node['id']: frozenset(field['key'] for field in node['fields']) for node in STATIC_NODES}

#: Schema-file JSON types Vela can render as an app-action input field.
_SUPPORTED_INPUT_TYPES = {'string': 'textarea', 'number': 'number', 'integer': 'number', 'boolean': 'boolean'}


def _load_schema(manifest, filename):
    path = (manifest.path / filename).resolve()
    if not path.is_relative_to(manifest.path.resolve()) or not path.is_file():
        return None
    try:
        schema = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return schema if isinstance(schema, dict) else None


def app_action_node_id(app_id: str, action_id: str) -> str:
    return f'{APP_ACTION_PREFIX}:{app_id}:{action_id}'


def app_action_definition(manifest, action) -> dict | None:
    """Build one editor node for an installed app's declared write action.

    Returns None when the action's input schema uses shapes Vela cannot render
    as a reviewed form; an unrenderable action simply stays unavailable.
    """
    schema = _load_schema(manifest, action['inputSchema'])
    if not schema or schema.get('type') != 'object':
        return None
    properties = schema.get('properties')
    if not isinstance(properties, dict) or not properties:
        return None
    required = set(schema.get('required', []))
    fields = []
    for key, spec in sorted(properties.items()):
        kind = spec.get('type') if isinstance(spec, dict) else None
        control = _SUPPORTED_INPUT_TYPES.get(kind)
        if control is None:
            return None
        fields.append({
            'key': f'input.{key}',
            'type': 'textarea' if control == 'textarea' else control,
            'label': spec.get('title') or key.replace('_', ' ').capitalize(),
            'default': '' if control == 'textarea' else (0 if control == 'number' else False),
            'optional': key not in required,
            'help': spec.get('description') or 'Supports {{field}} from the previous step.',
        })
    return {
        'id': app_action_node_id(manifest.id, action['id']),
        'name': f"{manifest.name}: {action['title']}",
        'operationName': action['title'],
        'category': 'action',
        'description': f"Asks {manifest.name} to {action['title'].lower()}. Needs your permission first.",
        'icon': 'Sparkles',
        'color': manifest.color or _COLORS['action'],
        'integrationId': f'vela-app-{manifest.id}',
        'inputs': [{'key': 'in', 'label': 'In', 'type': 'any'}],
        'outputs': [{'key': 'out', 'label': 'Result', 'type': 'object'}],
        'fields': fields,
    }


def app_action_integration(manifest) -> dict:
    return {
        'id': f'vela-app-{manifest.id}',
        'name': manifest.name,
        'description': f'Actions {manifest.name} offers to your automations.',
        'icon': 'Sparkles',
        'color': manifest.color or _COLORS['action'],
        'category': 'Your apps',
    }


def build(registry) -> dict:
    """The full catalog for the current installation: static nodes plus app actions."""
    nodes = [dict(node) for node in STATIC_NODES]
    integrations = []
    for manifest in sorted(registry.manifests().values(), key=lambda item: item.name.lower()):
        if not registry.is_installed(manifest.id) or manifest.schema_version != 2:
            continue
        actions = manifest.raw.get('actions') or []
        if not actions or 'actions' not in manifest.capabilities or 'storage' not in manifest.capabilities:
            continue
        available = [node for node in
                     (app_action_definition(manifest, action) for action in actions) if node]
        if not available:
            continue
        integrations.append(app_action_integration(manifest))
        nodes.extend(available)
    return {
        'version': CATALOG_VERSION,
        'fingerprint': fingerprint(nodes),
        'nodes': nodes,
        'integrations': integrations,
    }


def fingerprint(nodes) -> str:
    """Stable digest of a node set, so a run records the catalog it ran against."""
    payload = json.dumps(sorted(nodes, key=lambda node: node['id']), sort_keys=True,
                         separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def allowed_config_keys(node_type: str, catalog: dict) -> frozenset[str]:
    if node_type in _ALLOWED_KEYS:
        return _ALLOWED_KEYS[node_type]
    for node in catalog['nodes']:
        if node['id'] == node_type:
            return frozenset(field['key'] for field in node['fields'])
    return frozenset()


def definition(node_type: str, catalog: dict) -> dict | None:
    for node in catalog['nodes']:
        if node['id'] == node_type:
            return node
    return None
