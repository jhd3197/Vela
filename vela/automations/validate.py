"""Server-side validation of workflow documents.

Two levels, deliberately separate:

* **Storable** — safe to keep as an editable draft. The document is a real Tramo
  `WorkflowDoc`, every node type is in Vela's catalog, every configuration key
  belongs to its node, and the graph is within bounds and acyclic. A half-built
  draft passes: missing required values are not an error yet.
* **Executable** — safe to activate or run. Everything above, plus exactly one
  trigger at the root, every step reachable from it, and no missing required
  value.

Both run on import, save, activation and dispatch. The dashboard's copy of these
rules is a convenience; this one decides.
"""
import json
import re

from . import catalog as node_catalog

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')

#: Documents Vela can execute. A newer document must not silently downgrade.
SUPPORTED_DOCUMENT_VERSIONS = (1,)

MAX_DELAY_MS = 5 * 60 * 1000


class DocumentError(ValueError):
    """A workflow document Vela will not store or will not run."""

    def __init__(self, detail: str, node_id: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.node_id = node_id


def _fail(detail, node_id=None):
    raise DocumentError(detail, node_id)


def canonical(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def check_storable(document, catalog) -> dict:
    """Validate and return a normalized document, or raise DocumentError."""
    if not isinstance(document, dict):
        _fail('A workflow must be a JSON object.')
    version = document.get('version')
    if version not in SUPPORTED_DOCUMENT_VERSIONS:
        _fail(f'This workflow uses document format {version!r}, which this version of Vela '
              'cannot open. Update Vela, or export it from the tool that made it.')
    nodes, edges = document.get('nodes'), document.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list):
        _fail('A workflow needs a list of steps and a list of connections.')
    meta = document.get('meta')
    if meta is not None and not isinstance(meta, dict):
        _fail('Workflow details must be an object.')
    if len(nodes) > node_catalog.MAX_NODES:
        _fail(f'A workflow can hold at most {node_catalog.MAX_NODES} steps.')
    if len(edges) > node_catalog.MAX_EDGES:
        _fail(f'A workflow can hold at most {node_catalog.MAX_EDGES} connections.')

    seen = set()
    normalized_nodes = []
    for node in nodes:
        normalized_nodes.append(_check_node(node, catalog, seen))

    definitions = {node['id']: node for node in catalog['nodes']}
    ports = {
        node['id']: (
            {port['key'] for port in definitions[node['type']]['inputs']},
            {port['key'] for port in definitions[node['type']]['outputs']},
        )
        for node in normalized_nodes
    }
    normalized_edges = _check_edges(edges, ports)

    encoded = canonical({'version': 1, 'nodes': normalized_nodes, 'edges': normalized_edges,
                         'meta': _clean_meta(meta)})
    if len(encoded.encode('utf-8')) > node_catalog.MAX_DOCUMENT_BYTES:
        _fail('This workflow is larger than Vela stores for one automation.')
    _check_acyclic(normalized_nodes, normalized_edges)
    return json.loads(encoded)


def _clean_meta(meta):
    meta = dict(meta or {})
    clean = {}
    name = meta.get('name')
    if isinstance(name, str):
        clean['name'] = name[:120]
    description = meta.get('description')
    if isinstance(description, str):
        clean['description'] = description[:500]
    tags = meta.get('tags')
    if isinstance(tags, list):
        clean['tags'] = [str(tag)[:40] for tag in tags[:10] if isinstance(tag, str)]
    groups = meta.get('groups')
    if isinstance(groups, list):
        clean['groups'] = groups[:20]
    # `mcpServers` is never kept: Vela does not run Model Context Protocol nodes,
    # and the field can carry an endpoint and a bearer token.
    return clean


def _check_node(node, catalog, seen):
    if not isinstance(node, dict):
        _fail('Every step must be an object.')
    node_id = node.get('id')
    if not isinstance(node_id, str) or not _ID_RE.match(node_id):
        _fail(f'Step id {node_id!r} is not a valid identifier.')
    if node_id in seen:
        _fail(f'Two steps share the id {node_id!r}.', node_id)
    seen.add(node_id)
    node_type = node.get('type')
    if not isinstance(node_type, str):
        _fail('Every step needs a type.', node_id)
    definition = node_catalog.definition(node_type, catalog)
    if definition is None:
        _fail(f'Vela has no step called “{node_type}”. It may come from an app that is not '
              'installed, or from a workflow built for a different tool.', node_id)
    config = node.get('config')
    if config is None:
        config = {}
    if not isinstance(config, dict):
        _fail('Step settings must be an object.', node_id)
    allowed = node_catalog.allowed_config_keys(node_type, catalog)
    unknown = sorted(set(config) - allowed)
    if unknown:
        _fail(f'“{definition["name"]}” has no setting called “{unknown[0]}”.', node_id)
    clean_config = {key: _check_value(key, value, node_id) for key, value in config.items()}
    _check_node_rules(node_type, clean_config, node_id)

    clean = {'id': node_id, 'type': node_type, 'config': clean_config}
    label = node.get('label')
    if isinstance(label, str) and label.strip():
        clean['label'] = label.strip()[:80]
    run_after = node.get('runAfter')
    if run_after is not None:
        if run_after not in ('on-success', 'on-error', 'always'):
            _fail('Unsupported step condition.', node_id)
        clean['runAfter'] = run_after
    retry = node.get('retry')
    if retry is not None:
        clean['retry'] = _check_retry(retry, node_id)
    # `sensitive` and `requiredRole` belong to Tramo's multi-user role model.
    # Vela authorizes per workflow instead, so they are dropped rather than
    # stored as settings that look enforced but are not.
    return clean


def _check_value(key, value, node_id):
    if isinstance(value, str):
        if len(value.encode('utf-8')) > node_catalog.MAX_TEXT_FIELD_BYTES:
            _fail(f'The “{key}” setting is longer than Vela stores.', node_id)
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
            _fail(f'The “{key}” setting must be a finite number.', node_id)
        return value
    _fail(f'The “{key}” setting must be text, a number or yes/no.', node_id)


def _check_retry(retry, node_id):
    if not isinstance(retry, dict):
        _fail('Retry settings must be an object.', node_id)
    count = retry.get('count')
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 5:
        _fail('A step can retry at most five times.', node_id)
    delay = retry.get('delayMs', 0)
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 0 <= delay <= 60000:
        _fail('Retry delay must be between 0 and 60000 milliseconds.', node_id)
    backoff = retry.get('backoff', 'fixed')
    if backoff not in ('fixed', 'linear', 'exponential'):
        _fail('Unsupported retry backoff.', node_id)
    return {'count': count, 'delayMs': int(delay), 'backoff': backoff}


def _check_node_rules(node_type, config, node_id):
    """Per-node limits that a free-form field type cannot express."""
    if node_type == 'manual-trigger' and isinstance(config.get('payload'), str):
        text = config['payload'].strip()
        if text:
            try:
                json.loads(text)
            except ValueError:
                _fail('The starting value must be valid JSON.', node_id)
    if node_type == 'delay':
        ms = config.get('ms', 0)
        if isinstance(ms, (int, float)) and not 0 <= ms <= MAX_DELAY_MS:
            _fail('A wait must be between 0 and five minutes.', node_id)
    if node_type == 'vela-condition':
        operator = config.get('operator', 'equals')
        if operator not in node_catalog.COMPARISON_OPERATORS:
            _fail('Unsupported comparison.', node_id)
        subject = config.get('subject')
        if isinstance(subject, str) and subject and not all(
                part.strip() and _ID_RE.match(part.strip()) for part in subject.split('.')):
            _fail('A field path may only contain names separated by dots.', node_id)
        if config.get('valueType') not in (None, 'string', 'number', 'boolean', 'json'):
            _fail('Unsupported comparison type.', node_id)
    if node_type == 'vela-notify':
        priority = config.get('priority', 3)
        if isinstance(priority, (int, float)) and not 1 <= priority <= 5:
            _fail('Notification priority must be between 1 and 5.', node_id)
    if node_type == 'log' and config.get('level') not in (None, '', 'debug', 'info', 'warn', 'error'):
        _fail('Unsupported log level.', node_id)
    if node_type == 'merge' and config.get('mode') not in (None, '', 'object', 'array', 'first'):
        _fail('Unsupported join mode.', node_id)
    if node_type == 'approval-gate':
        timeout = config.get('timeoutSec', 0)
        if isinstance(timeout, (int, float)) and not 0 <= timeout <= 30 * 24 * 3600:
            _fail('An approval can wait at most 30 days.', node_id)
    if node_type == 'vela-schedule-trigger':
        _check_schedule_config(config, node_id)


def _check_schedule_config(config, node_id):
    every = config.get('every', 1)
    if not isinstance(every, (int, float)) or isinstance(every, bool) or not 1 <= every <= 999:
        _fail('A schedule must repeat between 1 and 999 units.', node_id)
    unit = config.get('unit', 'hours')
    if unit not in node_catalog._SCHEDULE_UNITS:
        _fail('Unsupported schedule unit.', node_id)
    at_time = config.get('atTime')
    if unit in ('days', 'weeks') and at_time and not _TIME_RE.match(str(at_time)):
        _fail('Enter the time as HH:MM in 24-hour form.', node_id)
    timezone = config.get('timezone')
    if timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(str(timezone))
        except Exception:
            _fail(f'“{timezone}” is not a timezone name Vela knows. Use a name such as '
                  'America/New_York.', node_id)


def _check_edges(edges, ports):
    seen = set()
    clean = []
    for edge in edges:
        if not isinstance(edge, dict):
            _fail('Every connection must be an object.')
        edge_id = edge.get('id')
        if not isinstance(edge_id, str) or not _ID_RE.match(edge_id):
            _fail(f'Connection id {edge_id!r} is not a valid identifier.')
        source, target = edge.get('source'), edge.get('target')
        if source not in ports or target not in ports:
            _fail('A connection points at a step that is not in this workflow.')
        if source == target:
            _fail('A step cannot connect to itself.', source)
        source_handle = edge.get('sourceHandle') or 'out'
        target_handle = edge.get('targetHandle') or 'in'
        if source_handle not in ports[source][1]:
            _fail(f'“{source_handle}” is not an output of this step.', source)
        if target_handle not in ports[target][0]:
            _fail(f'“{target_handle}” is not an input of this step.', target)
        key = (source, source_handle, target, target_handle)
        if key in seen:
            _fail('The same two steps are connected twice in the same way.', source)
        seen.add(key)
        clean.append({'id': edge_id, 'source': source, 'target': target,
                      'sourceHandle': source_handle, 'targetHandle': target_handle})
    return clean


def _check_acyclic(nodes, edges):
    outgoing = {node['id']: [] for node in nodes}
    indegree = {node['id']: 0 for node in nodes}
    for edge in edges:
        outgoing[edge['source']].append(edge['target'])
        indegree[edge['target']] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        _fail('The steps form a loop. Vela runs a workflow from its trigger forward, '
              'so connections cannot cycle back.')


def executable_problems(document, catalog) -> list[dict]:
    """Reasons this document cannot be activated or run. Empty means it can."""
    problems: list[dict] = []
    nodes = document['nodes']
    if not nodes:
        return [{'detail': 'Add a trigger and at least one step before turning this on.'}]
    if len(nodes) < 2:
        # A trigger on its own would start and immediately do nothing.
        problems.append({'detail': 'Add a trigger and at least one step before turning this on.'})

    triggers = [node for node in nodes if node['type'] in node_catalog.TRIGGER_NODES]
    if not triggers:
        problems.append({'detail': 'Add a trigger so Vela knows when to run this.'})
    elif len(triggers) > 1:
        problems.append({'detail': 'A workflow runs from one trigger. Remove the extra ones.',
                         'nodeId': triggers[1]['id']})

    incoming = {node['id']: 0 for node in nodes}
    for edge in document['edges']:
        incoming[edge['target']] += 1
    for trigger in triggers:
        if incoming[trigger['id']]:
            problems.append({'detail': 'A trigger cannot have anything connected into it.',
                             'nodeId': trigger['id']})

    if triggers:
        reachable = {triggers[0]['id']}
        frontier = [triggers[0]['id']]
        outgoing: dict[str, list[str]] = {node['id']: [] for node in nodes}
        for edge in document['edges']:
            outgoing[edge['source']].append(edge['target'])
        while frontier:
            for target in outgoing[frontier.pop()]:
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        for node in nodes:
            if node['id'] not in reachable:
                problems.append({
                    'detail': f'“{_label(node, catalog)}” is not connected to the trigger, so it '
                              'would never run.',
                    'nodeId': node['id'],
                })

    for node in nodes:
        definition = node_catalog.definition(node['type'], catalog)
        if definition is None:
            problems.append({'detail': f'“{node["type"]}” is not available on this server.',
                             'nodeId': node['id']})
            continue
        for field in definition['fields']:
            if field.get('optional'):
                continue
            value = node['config'].get(field['key'], field.get('default'))
            if value is None or (isinstance(value, str) and not value.strip()):
                problems.append({
                    'detail': f'“{_label(node, catalog)}” still needs a value for '
                              f'“{field["label"]}”.',
                    'nodeId': node['id'],
                })
    return problems


def _label(node, catalog):
    if node.get('label'):
        return node['label']
    definition = node_catalog.definition(node['type'], catalog)
    return definition['name'] if definition else node['type']


def declared_app_actions(document) -> list[dict]:
    """Every app action this document asks for, in a stable order."""
    requests: dict[tuple[str, str], dict] = {}
    for node in document['nodes']:
        if not node['type'].startswith(node_catalog.APP_ACTION_PREFIX + ':'):
            continue
        _, app, action = node['type'].split(':', 2)
        entry = requests.setdefault((app, action), {'app': app, 'action': action, 'nodes': []})
        entry['nodes'].append(node['id'])
    return [requests[key] for key in sorted(requests)]


def trigger_kind(document) -> str | None:
    for node in document['nodes']:
        kind = node_catalog.TRIGGER_NODES.get(node['type'])
        if kind:
            return kind
    return None


def trigger_node(document) -> dict | None:
    for node in document['nodes']:
        if node['type'] in node_catalog.TRIGGER_NODES:
            return node
    return None
