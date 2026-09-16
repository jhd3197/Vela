"""Automations: documents, permissions, durable runs, schedules and webhooks.

Everything here uses a disposable data directory and the pinned fixture apps.
Tests that need the Node worker skip with a clear reason when it has not been
installed, so the suite still reports honestly on a machine without it.
"""
import copy
import json
import os
import shutil
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

import test_app_contract as base
from fastapi.testclient import TestClient
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.api import create_app
from vela.automations import catalog as node_catalog
from vela.automations import schedules, validate
from vela.automations.effects import request_contract, retry_key
from vela.automations.worker import availability

WORKER = availability()
NEEDS_WORKER = unittest.skipUnless(
    WORKER['available'],
    f'automation worker unavailable: {WORKER["detail"]} '
    '(run python scripts/setup-automation-worker.py)')

TERMINAL = ('succeeded', 'failed', 'cancelled', 'interrupted', 'timed_out')


def document(*nodes, edges=()):
    return {'version': 1, 'nodes': list(nodes), 'edges': list(edges), 'meta': {}}


def node(node_id, node_type, **config):
    return {'id': node_id, 'type': node_type, 'config': config}


def edge(edge_id, source, target, source_handle=None, target_handle=None):
    item = {'id': edge_id, 'source': source, 'target': target}
    if source_handle:
        item['sourceHandle'] = source_handle
    if target_handle:
        item['targetHandle'] = target_handle
    return item


TEXT_FLOW = document(
    node('start', 'manual-trigger', payload='{"name": "Vela"}'),
    node('say', 'template', template='Hello {{name}}'),
    node('note', 'log', level='info', prefix='greeting'),
    edges=[edge('e1', 'start', 'say'), edge('e2', 'say', 'note')],
)


class DocumentTests(unittest.TestCase):
    """Validation decides what can be stored and what can be run."""

    def setUp(self):
        self.catalog = {'version': 1, 'fingerprint': 'x',
                        'nodes': node_catalog.STATIC_NODES, 'integrations': []}

    def check(self, doc):
        return validate.check_storable(copy.deepcopy(doc), self.catalog)

    def rejects(self, doc, fragment):
        with self.assertRaises(validate.DocumentError) as caught:
            self.check(doc)
        self.assertIn(fragment, caught.exception.detail)

    def test_accepts_a_supported_graph_and_normalizes_its_ports(self):
        clean = self.check(TEXT_FLOW)
        self.assertEqual(clean['edges'][0]['sourceHandle'], 'out')
        self.assertEqual(clean['edges'][0]['targetHandle'], 'in')
        self.assertEqual(validate.trigger_kind(clean), 'manual')
        self.assertEqual(validate.executable_problems(clean, self.catalog), [])

    def test_rejects_code_execution_and_every_unknown_step(self):
        for node_type in ('js-transform', 'if', 'switch', 'json-parse', 'for-each', 'loop-start',
                          'set-var', 'call-flow', 'http-request', 'mcp-tool-call', 'ai-prompt',
                          'vela-app-action:notes:create-note'):
            self.rejects(document(node('n1', node_type)), 'Vela has no step called')

    def test_rejects_unsupported_document_versions_and_broken_graphs(self):
        self.rejects({**TEXT_FLOW, 'version': 2}, 'cannot open')
        self.rejects(document(node('a', 'log'), node('a', 'log')), 'share the id')
        self.rejects(document(node('a', 'log'), edges=[edge('e', 'a', 'a')]),
                     'cannot connect to itself')
        self.rejects(document(node('a', 'log'), node('b', 'log'),
                              edges=[edge('e1', 'a', 'b'), edge('e2', 'b', 'a')]), 'form a loop')
        self.rejects(document(node('a', 'log'), node('b', 'log'),
                              edges=[edge('e', 'a', 'b', source_handle='nope')]),
                     'is not an output of this step')
        self.rejects(document(node('a', 'log', level='info', unknown='x')),
                     'has no setting called')
        self.rejects(document(node('a', 'manual-trigger', payload='not json')),
                     'must be valid JSON')
        self.rejects(document(node('a', 'delay', ms=10 * 60 * 1000)), 'five minutes')
        self.rejects(document(node('a', 'vela-condition', operator='exec')),
                     'Unsupported comparison')
        self.rejects(document(node('a', 'vela-condition', subject='a.b();c')), 'field path')
        self.rejects(document(node('a', 'vela-schedule-trigger', unit='days', timezone='Mars/Olympus')),
                     'not a timezone name')

    def test_drops_mcp_servers_and_role_gates_rather_than_storing_them(self):
        clean = self.check({**TEXT_FLOW, 'meta': {
            'name': 'x', 'mcpServers': [{'id': 's', 'name': 'S', 'url': 'https://x', 'tools': [],
                                         'authToken': 'secret-token'}]}})
        self.assertNotIn('mcpServers', clean['meta'])
        self.assertNotIn('secret-token', json.dumps(clean))
        graph = copy.deepcopy(TEXT_FLOW)
        graph['nodes'][2]['requiredRole'] = 'admin'
        graph['nodes'][2]['sensitive'] = True
        stored = self.check(graph)
        self.assertNotIn('requiredRole', stored['nodes'][2])
        self.assertNotIn('sensitive', stored['nodes'][2])

    def test_a_draft_may_be_incomplete_but_cannot_be_run(self):
        draft = document(node('say', 'template', template=''))
        clean = self.check(draft)
        problems = validate.executable_problems(clean, self.catalog)
        self.assertTrue(any('trigger' in problem['detail'] for problem in problems))
        self.assertTrue(any('still needs a value' in problem['detail'] for problem in problems))

        two = self.check(document(node('a', 'manual-trigger', payload='{}'),
                                  node('b', 'vela-schedule-trigger', every=1, unit='hours')))
        self.assertTrue(any('one trigger' in problem['detail']
                            for problem in validate.executable_problems(two, self.catalog)))

        orphan = self.check(document(node('a', 'manual-trigger', payload='{}'),
                                     node('b', 'log', level='info')))
        self.assertTrue(any('not connected to the trigger' in problem['detail']
                            for problem in validate.executable_problems(orphan, self.catalog)))

    def test_graph_and_field_sizes_are_bounded(self):
        many = document(*[node(f'n{index}', 'log', level='info')
                          for index in range(node_catalog.MAX_NODES + 1)])
        self.rejects(many, 'at most')
        self.rejects(document(node('a', 'template', template='x' * 40000)), 'longer than Vela stores')


class ScheduleTests(unittest.TestCase):
    """Schedules follow a wall clock in one timezone; Vela is the only ticker."""

    def test_daily_time_survives_both_clock_changes(self):
        config = {'every': 1, 'unit': 'days', 'atTime': '09:00'}
        zone = 'America/New_York'
        # Spring forward in 2026 is 8 March; autumn back is 1 November.
        before_spring = datetime(2026, 3, 7, 20, 0, tzinfo=timezone.utc)
        after_spring = schedules.next_occurrence(config, zone, before_spring)
        self.assertEqual(after_spring.astimezone(schedules.ZoneInfo(zone)).hour, 9)
        before_autumn = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
        after_autumn = schedules.next_occurrence(config, zone, before_autumn)
        self.assertEqual(after_autumn.astimezone(schedules.ZoneInfo(zone)).hour, 9)
        # The two instants differ by an hour of UTC offset, which is the point.
        self.assertNotEqual(after_spring.utcoffset(), None)

    def test_a_time_inside_the_spring_gap_fires_once_at_the_next_real_moment(self):
        config = {'every': 1, 'unit': 'days', 'atTime': '02:30'}
        zone = 'America/New_York'
        start = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)  # 00:00 local
        first = schedules.next_occurrence(config, zone, start)
        second = schedules.next_occurrence(config, zone, first)
        self.assertGreater(first, start)
        self.assertGreater(second, first)
        self.assertGreater(second - first, timedelta(hours=20))

    def test_occurrences_advance_and_missed_ones_are_counted_not_queued(self):
        config = {'every': 15, 'unit': 'minutes'}
        start = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        first = schedules.next_occurrence(config, 'UTC', start)
        self.assertEqual(first, datetime(2026, 5, 1, 12, 15, tzinfo=timezone.utc))
        later = start + timedelta(hours=2)
        self.assertEqual(schedules.missed_between(config, 'UTC', first, later), 7)

    def test_weekly_schedules_land_on_the_chosen_day(self):
        config = {'every': 1, 'unit': 'weeks', 'weekday': 'sunday', 'atTime': '18:00'}
        start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
        moment = schedules.next_occurrence(config, 'UTC', start)
        self.assertEqual(moment.weekday(), 6)
        self.assertEqual(moment.hour, 18)
        self.assertIn('Sunday', schedules.describe(config, 'UTC'))

    def test_an_unknown_timezone_is_refused(self):
        with self.assertRaises(ValueError):
            schedules.resolve_timezone('Nowhere/Nothing')


class RetryKeyTests(unittest.TestCase):
    def test_a_key_identifies_one_effect_in_one_run(self):
        first = retry_key('run-1', 'node-a')
        self.assertEqual(first, retry_key('run-1', 'node-a'))
        self.assertNotEqual(first, retry_key('run-1', 'node-b'))
        self.assertNotEqual(first, retry_key('run-2', 'node-a'))
        self.assertRegex(first, r'^[a-zA-Z0-9_-]{8,100}$')


class AutomationApiTests(unittest.TestCase):
    """The HTTP surface: storage, revisions, permissions and the run queue."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        for app in ('notes', 'meals'):
            shutil.copytree(FIXTURE_APPS / app, self.apps / app)
        self.client.close()
        self.stack = TestClient(create_app(self.config))
        self.client = self.stack.__enter__()
        self.hub = {'Authorization': 'Bearer ' + self.client.get(
            '/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}

    def tearDown(self):
        self.stack.__exit__(None, None, None)
        self.temp.cleanup()

    # ---------------------------------------------------------- helpers --

    def create(self, name='Test automation'):
        response = self.client.post('/api/automations', headers=self.hub, json={'name': name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def save(self, workflow, doc, revision=None, expect=200):
        response = self.client.put(
            f'/api/automations/{workflow["id"]}', headers=self.hub,
            json={'revision': revision if revision is not None else workflow['documentRevision'],
                  'document': doc})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def install(self, app_id):
        self.assertEqual(
            self.client.post(f'/api/apps/{app_id}/install', headers=self.hub).status_code, 200)

    def wait_for(self, run_id, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            run = self.client.get(f'/api/automations/runs/{run_id}', headers=self.hub).json()
            if run['status'] in TERMINAL or run['status'] == 'waiting':
                return run
            time.sleep(0.2)
        self.fail(f'run {run_id} did not finish in {seconds}s')

    def notes(self):
        _, session = base.ApiBoundaryTests.session(self, 'notes')
        return self.client.get('/api/app/storage', headers=session).json()

    # ------------------------------------------------------------ tests --

    def test_an_empty_server_reports_no_automations_and_no_invented_activity(self):
        listing = self.client.get('/api/automations', headers=self.hub).json()
        self.assertEqual(listing['automations'], [])
        self.assertEqual(listing['statistics'], {'total': 0, 'succeeded': 0, 'failed': 0,
                                                 'averageSeconds': None})
        self.assertEqual(self.client.get('/api/automations/runs', headers=self.hub).json()['runs'], [])

    def test_the_catalog_offers_only_vetted_steps_and_installed_app_actions(self):
        catalog = self.client.get('/api/automations/catalog', headers=self.hub).json()
        ids = {item['id'] for item in catalog['nodes']}
        self.assertEqual(ids, set(node_catalog.STATIC_NODE_IDS))
        self.install('notes')
        catalog = self.client.get('/api/automations/catalog', headers=self.hub).json()
        ids = {item['id'] for item in catalog['nodes']}
        self.assertIn('vela-app-action:notes:create-note', ids)
        self.assertNotIn('js-transform', ids)
        self.client.delete('/api/apps/notes', headers=self.hub)
        after = self.client.get('/api/automations/catalog', headers=self.hub).json()
        self.assertNotIn('vela-app-action:notes:create-note',
                         {item['id'] for item in after['nodes']})

    def test_blueprints_are_offered_only_when_their_prerequisites_are_real(self):
        offered = self.client.get('/api/automations/blueprints', headers=self.hub).json()['blueprints']
        by_id = {item['id']: item for item in offered}
        # Notifications are not configured on a fresh server.
        self.assertFalse(by_id['scheduled-notification']['available'])
        self.assertIn('notification server', by_id['scheduled-notification']['requirement'])
        blocked = self.client.post('/api/automations/blueprints/scheduled-notification',
                                   headers=self.hub, json={})
        self.assertEqual(blocked.status_code, 409)
        # An app action only appears once its app is installed.
        self.assertNotIn('manual-notes-create-note', by_id)
        self.install('notes')
        offered = self.client.get('/api/automations/blueprints', headers=self.hub).json()['blueprints']
        by_id = {item['id']: item for item in offered}
        self.assertTrue(by_id['manual-notes-create-note']['available'])

        self.client.patch('/api/settings', headers=self.hub, json={
            'ntfy_config': {'server': 'https://ntfy.example', 'topic': 'vela-test'}})
        offered = self.client.get('/api/automations/blueprints', headers=self.hub).json()['blueprints']
        self.assertTrue({item['id']: item for item in offered}['scheduled-notification']['available'])

        created = self.client.post('/api/automations/blueprints/manual-notes-create-note',
                                   headers=self.hub, json={})
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(draft['draftProblems'], [])
        self.assertFalse(draft['grants'][0]['granted'])
        self.assertEqual(self.client.post(f'/api/automations/{draft["id"]}/activate',
                                          headers=self.hub).status_code, 403)
        self.assertEqual(self.client.post('/api/automations/blueprints/nonsense',
                                          headers=self.hub, json={}).status_code, 404)

    def test_a_server_without_the_runtime_explains_itself_and_refuses_to_run(self):
        workflow = self.create('Greeting')
        self.save(workflow, TEXT_FLOW)
        # An installation whose automation runtime is missing must say so, not
        # accept a run it cannot finish.
        with mock.patch.dict(os.environ, {'VELA_AUTOMATION_NODE': str(self.root / 'absent-node')}):
            status = self.client.get('/api/automations/status', headers=self.hub).json()
            self.assertFalse(status['available'])
            self.assertIn('Reinstall the Vela download', status['detail'])
            refused = self.client.post(f'/api/automations/{workflow["id"]}/runs', headers=self.hub,
                                       json={})
            self.assertEqual(refused.status_code, 503)
            self.assertEqual(refused.json()['detail'], status['detail'])
            # The rest of the page keeps working.
            self.assertEqual(self.client.get('/api/automations', headers=self.hub).status_code, 200)
            self.assertEqual(
                self.client.get(f'/api/automations/{workflow["id"]}', headers=self.hub).status_code,
                200)

    def test_status_counts_todays_runs_for_the_desk(self):
        """The Flows widget reads these three fields, so they are part of the
        status contract rather than something the page derives from /runs."""
        status = self.client.get('/api/automations/status', headers=self.hub).json()
        self.assertEqual(status['runsToday'], 0)
        self.assertEqual(status['failuresToday'], 0)
        self.assertIsNone(status['averageDurationMs'])

        workflow = self.create('Counted')
        self.save(workflow, TEXT_FLOW)
        now = datetime.now(timezone.utc)
        rows = [
            # Queued a minute ago: today, succeeded, two seconds long.
            ((now - timedelta(minutes=1)).isoformat(), 'succeeded',
             (now - timedelta(minutes=1)).isoformat(),
             (now - timedelta(minutes=1) + timedelta(seconds=2)).isoformat()),
            # Also today, and failed.
            ((now - timedelta(minutes=2)).isoformat(), 'failed', None, None),
            # Yesterday: outside the window, so neither count moves.
            ((now - timedelta(days=1, hours=2)).isoformat(), 'succeeded', None, None),
        ]
        store = self.client.app.state.automations.store
        with store.connection() as db:
            for index, (queued, state, started, finished) in enumerate(rows):
                db.execute(
                    'INSERT INTO runs (id, workflow_id, revision, status, trigger, queued_at, '
                    'started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)',
                    (f'run-{index}', workflow['id'], 1, state, 'manual', queued, started, finished))

        status = self.client.get('/api/automations/status', headers=self.hub).json()
        self.assertEqual(status['runsToday'], 2)
        self.assertEqual(status['failuresToday'], 1)
        self.assertEqual(status['averageDurationMs'], 2000)

    def test_app_sessions_cannot_reach_automations(self):
        self.install('notes')
        _, session = base.ApiBoundaryTests.session(self, 'notes')
        for path in ('/api/automations', '/api/automations/catalog', '/api/automations/runs'):
            response = self.client.get(path, headers=session)
            self.assertEqual(response.status_code, 401, f'GET {path}: {response.text}')
        created = self.client.post('/api/automations', headers=session, json={'name': 'x'})
        self.assertEqual(created.status_code, 401, created.text)
        self.assertEqual(self.client.get('/api/automations').status_code, 401)

    def test_documents_survive_a_restart_and_a_second_editor_gets_a_conflict(self):
        workflow = self.create('Greeting')
        saved = self.save(workflow, TEXT_FLOW)
        self.assertEqual(saved['documentRevision'], 2)
        self.assertEqual(saved['draftProblems'], [])

        stale = self.client.put(f'/api/automations/{workflow["id"]}', headers=self.hub,
                                json={'revision': 1, 'document': TEXT_FLOW})
        self.assertEqual(stale.status_code, 409)
        self.assertIn('another window', stale.json()['detail'])

        # An unchanged save keeps the same revision instead of inflating history.
        self.assertEqual(self.save(saved, TEXT_FLOW, revision=2)['documentRevision'], 2)

        self.stack.__exit__(None, None, None)
        self.stack = TestClient(create_app(self.config))
        self.client = self.stack.__enter__()
        self.hub = {'Authorization': 'Bearer ' + self.client.get(
            '/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
        again = self.client.get(f'/api/automations/{workflow["id"]}', headers=self.hub).json()
        self.assertEqual(again['document']['nodes'][1]['config']['template'], 'Hello {{name}}')
        self.assertEqual(again['documentRevision'], 2)

    def test_the_server_refuses_documents_it_cannot_run(self):
        workflow = self.create()
        broken = self.client.put(f'/api/automations/{workflow["id"]}', headers=self.hub, json={
            'revision': 1, 'document': document(node('a', 'js-transform', expression='return 1'))})
        self.assertEqual(broken.status_code, 422)
        self.assertIn('Vela has no step called', broken.json()['detail'])

    def test_import_and_export_carry_the_graph_but_never_permissions(self):
        self.install('notes')
        workflow = self.create('Note maker')
        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Hi", "body": "There"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}', 'input.body': '{{body}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)
        review = detail['grants'][0]
        self.assertEqual(self.client.put(
            f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
            json={'app': 'notes', 'action': 'create-note', 'allow': True,
                  'requestContract': review['requestContract'],
                  'targetContract': review['targetContract']}).status_code, 200)

        exported = self.client.get(f'/api/automations/{workflow["id"]}/export',
                                   headers=self.hub).json()
        self.assertNotIn('grant', json.dumps(exported).lower())
        imported = self.client.post('/api/automations/import', headers=self.hub,
                                    json=exported).json()
        self.assertNotEqual(imported['id'], workflow['id'])
        self.assertEqual(imported['status'], 'draft')
        self.assertFalse(imported['grants'][0]['granted'])
        self.assertIsNone(imported['activeRevision'])

        copied = self.client.post(f'/api/automations/{workflow["id"]}/duplicate',
                                  headers=self.hub).json()
        self.assertEqual(copied['status'], 'draft')
        self.assertFalse(copied['grants'][0]['granted'])

    def test_an_import_from_a_newer_vela_or_with_unknown_steps_is_refused(self):
        newer = self.client.post('/api/automations/import', headers=self.hub, json={
            'vela': {'kind': 'automation', 'exportVersion': 99}, 'document': TEXT_FLOW})
        self.assertEqual(newer.status_code, 422)
        self.assertIn('newer version of Vela', newer.json()['detail'])
        unknown = self.client.post('/api/automations/import', headers=self.hub, json={
            'vela': {'kind': 'automation', 'exportVersion': 1},
            'document': document(node('a', 'vela-app-action:ghost:write'))})
        self.assertEqual(unknown.status_code, 422)

    def test_turning_an_automation_on_requires_a_runnable_graph_and_every_permission(self):
        self.install('notes')
        workflow = self.create('Note maker')
        blocked = self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)
        self.assertEqual(blocked.status_code, 409)

        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Hi", "body": "There"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}', 'input.body': '{{body}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)
        ungranted = self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)
        self.assertEqual(ungranted.status_code, 403)
        self.assertIn('Allow this automation', ungranted.json()['detail'])

        review = detail['grants'][0]
        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': True,
                              'requestContract': review['requestContract'],
                              'targetContract': review['targetContract']})
        active = self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)
        self.assertEqual(active.status_code, 200, active.text)
        self.assertEqual(active.json()['status'], 'active')
        self.assertEqual(active.json()['activeRevision'], active.json()['documentRevision'])

    def test_a_grant_cannot_be_given_against_a_contract_that_was_not_reviewed(self):
        self.install('notes')
        workflow = self.create('Note maker')
        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Hi", "body": "There"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)
        review = detail['grants'][0]
        wrong = self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                                json={'app': 'notes', 'action': 'create-note', 'allow': True,
                                      'requestContract': '0' * 64,
                                      'targetContract': review['targetContract']})
        self.assertEqual(wrong.status_code, 409)

        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': True,
                              'requestContract': review['requestContract'],
                              'targetContract': review['targetContract']})
        self.assertTrue(self.client.get(f'/api/automations/{workflow["id"]}',
                                        headers=self.hub).json()['grants'][0]['granted'])

        # Adding an input to the granted step is a different request.
        doc['nodes'][1]['config']['input.body'] = '{{body}}'
        after = self.save(workflow, doc, revision=2)
        self.assertFalse(after['grants'][0]['granted'])

    def test_an_app_update_or_removal_invalidates_the_grant(self):
        self.install('notes')
        workflow = self.create('Note maker')
        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Hi", "body": "There"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}', 'input.body': '{{body}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)
        review = detail['grants'][0]
        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': True,
                              'requestContract': review['requestContract'],
                              'targetContract': review['targetContract']})
        manifest = self.config.installed_dir / 'notes/app.json'
        raw = json.loads(manifest.read_text())
        raw['version'] = '9.9.9'
        manifest.write_text(json.dumps(raw))
        updated = self.client.get(f'/api/automations/{workflow["id"]}', headers=self.hub).json()
        self.assertFalse(updated['grants'][0]['granted'])
        self.assertIn('was updated', updated['grants'][0]['staleReason'])

    def test_a_webhook_needs_its_secret_and_refuses_an_identical_replay(self):
        workflow = self.create('Hook')
        doc = document(node('start', 'vela-webhook-trigger', note=''),
                       node('note', 'log', level='info'),
                       edges=[edge('e1', 'start', 'note')])
        self.save(workflow, doc)
        self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)
        rotated = self.client.post(f'/api/automations/{workflow["id"]}/webhook',
                                   headers=self.hub).json()
        path, secret = rotated['path'], rotated['secret']

        self.assertEqual(self.client.post(path, json={'id': 1}).status_code, 401)
        self.assertEqual(self.client.post(
            path, json={'id': 1}, headers={'X-Vela-Automation-Secret': 'wrong'}).status_code, 401)
        accepted = self.client.post(path, json={'id': 1},
                                    headers={'X-Vela-Automation-Secret': secret})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIn('runId', accepted.json())
        replay = self.client.post(path, json={'id': 1},
                                  headers={'X-Vela-Automation-Secret': secret})
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(self.client.post(path, json={'id': 2},
                                          headers={'X-Vela-Automation-Secret': secret}).status_code,
                         200)
        self.assertEqual(self.client.post('/api/automations/hooks/unknown', json={},
                                          headers={'X-Vela-Automation-Secret': secret}).status_code,
                         404)

    def test_pausing_stops_the_schedule_and_archiving_drops_permissions(self):
        self.install('notes')
        workflow = self.create('Scheduled')
        doc = document(
            node('start', 'vela-schedule-trigger', every=1, unit='hours', atTime='09:00',
                 weekday='monday', timezone='UTC'),
            node('note', 'log', level='info'),
            edges=[edge('e1', 'start', 'note')])
        self.save(workflow, doc)
        active = self.client.post(f'/api/automations/{workflow["id"]}/activate',
                                  headers=self.hub).json()
        self.assertIsNotNone(active['schedule']['nextRun'])
        self.assertEqual(active['schedule']['timezone'], 'UTC')
        paused = self.client.post(f'/api/automations/{workflow["id"]}/pause',
                                  headers=self.hub).json()
        self.assertEqual(paused['status'], 'paused')
        self.assertIsNone(paused['schedule'])
        archived = self.client.post(f'/api/automations/{workflow["id"]}/archive',
                                    headers=self.hub).json()
        self.assertEqual(archived['status'], 'archived')
        self.assertEqual(self.client.get('/api/automations', headers=self.hub).json()['automations'], [])
        self.assertEqual(len(self.client.get('/api/automations?archived=true',
                                             headers=self.hub).json()['automations']), 1)

    def test_one_due_occurrence_is_claimed_once_even_across_a_restart(self):
        workflow = self.create('Scheduled')
        doc = document(
            node('start', 'vela-schedule-trigger', every=1, unit='hours', atTime='09:00',
                 weekday='monday', timezone='UTC'),
            node('note', 'log', level='info'),
            edges=[edge('e1', 'start', 'note')])
        self.save(workflow, doc)
        self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)

        automations = self.client.app.state.automations
        schedule = automations.store.schedule(workflow['id'])
        due = datetime.now(timezone.utc) - timedelta(minutes=5)
        with automations.store.connection() as db:
            db.execute('UPDATE schedules SET next_due=? WHERE workflow_id=?',
                       (due.isoformat(), workflow['id']))

        # Two dispatch passes over the same due moment, as a restart would do.
        automations._dispatch_due()
        first = self.client.get(f'/api/automations/runs?workflowId={workflow["id"]}',
                                headers=self.hub).json()['runs']
        automations._dispatch_due()
        second = self.client.get(f'/api/automations/runs?workflowId={workflow["id"]}',
                                 headers=self.hub).json()['runs']
        scheduled = [run for run in second if run['trigger'] == 'schedule']
        self.assertEqual(len(first), 1)
        self.assertEqual(len(scheduled), 1)
        self.assertGreater(
            datetime.fromisoformat(automations.store.schedule(workflow['id'])['next_due']),
            datetime.now(timezone.utc))
        occurrences = automations.store.recent_occurrences(workflow['id'])
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]['outcome'], 'queued')
        del schedule

    def test_a_paused_automation_never_dispatches(self):
        workflow = self.create('Scheduled')
        doc = document(
            node('start', 'vela-schedule-trigger', every=1, unit='hours', atTime='09:00',
                 weekday='monday', timezone='UTC'),
            node('note', 'log', level='info'),
            edges=[edge('e1', 'start', 'note')])
        self.save(workflow, doc)
        self.client.post(f'/api/automations/{workflow["id"]}/activate', headers=self.hub)
        automations = self.client.app.state.automations
        with automations.store.connection() as db:
            db.execute('UPDATE schedules SET next_due=? WHERE workflow_id=?',
                       ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                        workflow['id']))
        self.client.post(f'/api/automations/{workflow["id"]}/pause', headers=self.hub)
        automations._dispatch_due()
        self.assertEqual(self.client.get(f'/api/automations/runs?workflowId={workflow["id"]}',
                                         headers=self.hub).json()['runs'], [])

    @NEEDS_WORKER
    def test_a_pending_approval_survives_a_restart(self):
        workflow = self.create('Approved later')
        doc = document(
            node('start', 'manual-trigger', payload='{}'),
            node('gate', 'approval-gate', message='Still there?', timeoutSec=0),
            node('note', 'log', level='info', prefix='resumed'),
            edges=[edge('e1', 'start', 'gate'),
                   edge('e2', 'gate', 'note', source_handle='approved')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'waiting')

        self.stack.__exit__(None, None, None)
        self.stack = TestClient(create_app(self.config))
        self.client = self.stack.__enter__()
        self.hub = {'Authorization': 'Bearer ' + self.client.get(
            '/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}

        after = self.client.get(f'/api/automations/runs/{run["id"]}', headers=self.hub).json()
        self.assertEqual(after['status'], 'waiting')
        pending = self.client.get('/api/automations/approvals', headers=self.hub).json()['approvals']
        self.assertEqual(len(pending), 1)
        decided = self.client.post(
            f'/api/automations/runs/{run["id"]}/approvals/{pending[0]["gateKey"]}',
            headers=self.hub, json={'approved': True, 'comment': ''})
        self.assertEqual(decided.status_code, 200, decided.text)
        finished = self.wait_for(run['id'])
        self.assertEqual(finished['status'], 'succeeded', finished.get('error'))
        self.assertEqual(finished['revision'], run['revision'])

    @NEEDS_WORKER
    def test_a_saved_graph_runs_on_the_server_and_its_history_survives_a_restart(self):
        workflow = self.create('Greeting')
        self.save(workflow, TEXT_FLOW)
        started = self.client.post(f'/api/automations/{workflow["id"]}/runs', headers=self.hub,
                                   json={})
        self.assertEqual(started.status_code, 202, started.text)
        run = self.wait_for(started.json()['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('error'))
        self.assertEqual(run['revision'], 2)
        self.assertEqual(run['runtime']['worker']['tramo'], WORKER['provenance']['installed']['@tramo/runtime'])
        kinds = [event['type'] for event in run['events']]
        self.assertEqual(kinds[0], 'run-start')
        self.assertEqual(kinds[-1], 'run-end')
        logged = [event for event in run['events'] if event['type'] == 'node-log'
                  and event['nodeId'] == 'note']
        self.assertEqual(logged[0]['data'], 'Hello Vela')
        # A step's own output is described, not copied, unless it is the log step.
        success = [event for event in run['events'] if event['type'] == 'node-success'
                   and event['nodeId'] == 'say'][0]
        self.assertEqual(success['output'], {'out': {'type': 'text', 'length': 10}})

        self.stack.__exit__(None, None, None)
        self.stack = TestClient(create_app(self.config))
        self.client = self.stack.__enter__()
        self.hub = {'Authorization': 'Bearer ' + self.client.get(
            '/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
        after = self.client.get(f'/api/automations/runs/{run["id"]}', headers=self.hub).json()
        self.assertEqual(after['status'], 'succeeded')
        self.assertEqual(len(after['events']), len(run['events']))

    @NEEDS_WORKER
    def test_a_branch_routes_without_evaluating_anything(self):
        workflow = self.create('Branch')
        doc = document(
            node('start', 'manual-trigger', payload='{"status": "ready"}'),
            node('check', 'vela-condition', subject='status', operator='equals', value='ready',
                 valueType='string'),
            node('yes', 'log', level='info', prefix='matched'),
            node('no', 'log', level='info', prefix='missed'),
            edges=[edge('e1', 'start', 'check'),
                   edge('e2', 'check', 'yes', source_handle='true'),
                   edge('e3', 'check', 'no', source_handle='false')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('error'))
        ran = {event['nodeId'] for event in run['events'] if event['type'] == 'node-success'}
        self.assertIn('yes', ran)
        self.assertNotIn('no', ran)

    @NEEDS_WORKER
    def test_a_failing_step_fails_the_run_with_a_readable_reason(self):
        workflow = self.create('Notify without a server')
        doc = document(node('start', 'manual-trigger', payload='{}'),
                       node('tell', 'vela-notify', title='Hi', message='There', priority=3),
                       edges=[edge('e1', 'start', 'tell')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'failed')
        errors = [event for event in run['events'] if event['type'] == 'node-error']
        self.assertIn('notifications in Vela', errors[0]['error'])

    @NEEDS_WORKER
    def test_a_granted_action_writes_once_and_a_repeat_run_is_a_separate_write(self):
        self.install('notes')
        workflow = self.create('Note maker')
        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Dinner", "body": "Pasta"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}', 'input.body': '{{body}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)

        denied = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                                headers=self.hub, json={}).json()['id'])
        self.assertEqual(denied['status'], 'failed')
        self.assertIn('not allowed to use Notes', denied['error'] or
                      json.dumps(denied['events']))
        self.assertIsNone(self.notes()['value'])

        review = detail['grants'][0]
        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': True,
                              'requestContract': review['requestContract'],
                              'targetContract': review['targetContract']})
        first = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                               headers=self.hub, json={}).json()['id'])
        self.assertEqual(first['status'], 'succeeded', first.get('error'))
        saved = self.notes()['value']
        self.assertEqual(len(saved['notes']), 1)
        self.assertEqual(saved['notes'][0]['body'], 'Pasta')

        # Running it again is a new intention, so it writes a second note.
        second = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                                headers=self.hub, json={}).json()['id'])
        self.assertEqual(second['status'], 'succeeded')
        self.assertEqual(len(self.notes()['value']['notes']), 2)

        # Removing permission stops the next call immediately.
        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': False})
        blocked = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                                 headers=self.hub, json={}).json()['id'])
        self.assertEqual(blocked['status'], 'failed')
        self.assertEqual(len(self.notes()['value']['notes']), 2)

    @NEEDS_WORKER
    def test_the_same_effect_in_one_run_is_never_written_twice(self):
        self.install('notes')
        workflow = self.create('Note maker')
        doc = document(
            node('start', 'manual-trigger', payload='{"title": "Once", "body": "Only"}'),
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': '{{title}}', 'input.body': '{{body}}'}},
            edges=[edge('e1', 'start', 'write')])
        detail = self.save(workflow, doc)
        review = detail['grants'][0]
        self.client.put(f'/api/automations/{workflow["id"]}/grants', headers=self.hub,
                        json={'app': 'notes', 'action': 'create-note', 'allow': True,
                              'requestContract': review['requestContract'],
                              'targetContract': review['targetContract']})
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'succeeded')

        # Replaying the identical effect key must return the stored receipt
        # rather than appending a second note.
        automations = self.client.app.state.automations
        again = automations.actions.invoke_for_automation(
            workflow['id'], 'notes', 'create-note', {'title': 'Once', 'body': 'Only'},
            retry_key(run['id'], 'write'), lambda manifest, identity: None)
        self.assertTrue(again['replayed'])
        self.assertEqual(len(self.notes()['value']['notes']), 1)

    @NEEDS_WORKER
    def test_an_approval_pauses_the_run_and_resumes_the_version_it_started_on(self):
        workflow = self.create('Approved note')
        doc = document(
            node('start', 'manual-trigger', payload='{"name": "Vela"}'),
            node('gate', 'approval-gate', message='Send the greeting?', timeoutSec=0),
            node('note', 'log', level='info', prefix='after approval'),
            edges=[edge('e1', 'start', 'gate'),
                   edge('e2', 'gate', 'note', source_handle='approved')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'waiting')
        self.assertEqual(run['approvals'][0]['message'], 'Send the greeting?')

        pending = self.client.get('/api/automations/approvals', headers=self.hub).json()
        self.assertEqual(len(pending['approvals']), 1)

        # Editing the draft while a run waits must not change what resumes.
        edited = copy.deepcopy(doc)
        edited['nodes'][2]['config']['prefix'] = 'changed'
        self.save(workflow, edited, revision=2)

        decided = self.client.post(
            f'/api/automations/runs/{run["id"]}/approvals/{run["approvals"][0]["gateKey"]}',
            headers=self.hub, json={'approved': True, 'comment': 'go ahead'})
        self.assertEqual(decided.status_code, 200, decided.text)
        finished = self.wait_for(run['id'])
        self.assertEqual(finished['status'], 'succeeded', finished.get('error'))
        self.assertEqual(finished['revision'], 2)
        logged = [event for event in finished['events']
                  if event['type'] == 'node-log' and event['nodeId'] == 'note']
        self.assertEqual(logged[-1]['message'], 'after approval')

    @NEEDS_WORKER
    def test_one_automation_runs_at_a_time(self):
        workflow = self.create('Waiting')
        doc = document(
            node('start', 'manual-trigger', payload='{}'),
            node('gate', 'approval-gate', message='Proceed?', timeoutSec=0),
            edges=[edge('e1', 'start', 'gate')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'waiting')
        second = self.client.post(f'/api/automations/{workflow["id"]}/runs', headers=self.hub,
                                  json={})
        self.assertEqual(second.status_code, 409)
        self.assertIn('waiting for your decision', second.json()['detail'])

        # A schedule that comes due meanwhile is skipped and recorded, not
        # stacked up behind a run that may never be decided.
        automations = self.client.app.state.automations
        automations.store.save_schedule(
            workflow['id'], 'start', {'every': 1, 'unit': 'hours'}, 'UTC',
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        automations.store.set_status(workflow['id'], 'active', active_revision=run['revision'])
        automations._dispatch_due()
        occurrences = automations.store.recent_occurrences(workflow['id'])
        self.assertEqual(len(occurrences), 1)
        self.assertIn('waiting for a decision', occurrences[0]['outcome'])
        self.assertEqual(len(self.client.get(f'/api/automations/runs?workflowId={workflow["id"]}',
                                             headers=self.hub).json()['runs']), 1)

    @NEEDS_WORKER
    def test_a_rejected_approval_takes_the_other_branch(self):
        workflow = self.create('Rejected')
        doc = document(
            node('start', 'manual-trigger', payload='{}'),
            node('gate', 'approval-gate', message='Proceed?', timeoutSec=0),
            node('no', 'log', level='info', prefix='rejected'),
            edges=[edge('e1', 'start', 'gate'),
                   edge('e2', 'gate', 'no', source_handle='rejected')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'waiting')
        self.client.post(
            f'/api/automations/runs/{run["id"]}/approvals/{run["approvals"][0]["gateKey"]}',
            headers=self.hub, json={'approved': False, 'comment': 'no'})
        finished = self.wait_for(run['id'])
        self.assertEqual(finished['status'], 'succeeded')
        self.assertIn('rejected', [event.get('message') for event in finished['events']
                                   if event['type'] == 'node-log'])

    @NEEDS_WORKER
    def test_cancelling_a_waiting_run_ends_it_and_says_what_it_did_not_undo(self):
        workflow = self.create('Cancelled')
        doc = document(
            node('start', 'manual-trigger', payload='{}'),
            node('gate', 'approval-gate', message='Proceed?', timeoutSec=0),
            edges=[edge('e1', 'start', 'gate')])
        self.save(workflow, doc)
        run = self.wait_for(self.client.post(f'/api/automations/{workflow["id"]}/runs',
                                             headers=self.hub, json={}).json()['id'])
        self.assertEqual(run['status'], 'waiting')
        self.client.post(f'/api/automations/runs/{run["id"]}/cancel', headers=self.hub)
        ended = self.wait_for(run['id'])
        self.assertEqual(ended['status'], 'cancelled')
        self.assertIn('not undone', ended['error'])
        self.assertEqual(self.client.post(f'/api/automations/runs/{run["id"]}/cancel',
                                          headers=self.hub).status_code, 409)

    @NEEDS_WORKER
    def test_a_run_interrupted_by_a_restart_is_reported_honestly(self):
        workflow = self.create('Slow')
        doc = document(node('start', 'manual-trigger', payload='{}'),
                       node('wait', 'delay', ms=120000),
                       edges=[edge('e1', 'start', 'wait')])
        self.save(workflow, doc)
        started = self.client.post(f'/api/automations/{workflow["id"]}/runs', headers=self.hub,
                                   json={}).json()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = self.client.get(f'/api/automations/runs/{started["id"]}',
                                      headers=self.hub).json()
            if current['status'] == 'running':
                break
            time.sleep(0.2)
        self.assertEqual(current['status'], 'running')

        self.stack.__exit__(None, None, None)
        self.stack = TestClient(create_app(self.config))
        self.client = self.stack.__enter__()
        self.hub = {'Authorization': 'Bearer ' + self.client.get(
            '/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
        after = self.client.get(f'/api/automations/runs/{started["id"]}', headers=self.hub).json()
        self.assertEqual(after['status'], 'interrupted')
        self.assertIn('not undone', after['error'])


class WorkerProtocolTests(unittest.TestCase):
    """The worker is supervised: an unusable one is reported, not pretended away."""

    def test_availability_explains_itself_rather_than_failing_silently(self):
        state = availability()
        self.assertIn('available', state)
        if not state['available']:
            self.assertTrue(state['detail'])
        else:
            self.assertTrue(state['node'])
            self.assertEqual(state['provenance']['installed']['@tramo/spec'],
                             state['provenance']['installed']['@tramo/runtime'])

    @NEEDS_WORKER
    def test_the_worker_starts_hidden_answers_a_health_check_and_stops(self):
        import asyncio
        from vela.automations.worker import Worker

        async def exercise():
            worker = Worker(on_event=lambda *_: None, on_effect=lambda *_: None)
            info = await worker.start()
            self.assertEqual(info.protocol, 1)
            self.assertIn('manual-trigger', info.nodes)
            self.assertNotIn('js-transform', info.nodes)
            self.assertTrue(await worker.ping())
            await worker.stop()
            self.assertFalse(worker.running)

        asyncio.run(exercise())


class ContractDigestTests(unittest.TestCase):
    def test_the_reviewed_contract_covers_the_steps_not_their_wording(self):
        base_doc = document(
            {'id': 'write', 'type': 'vela-app-action:notes:create-note',
             'config': {'input.title': 'Hello', 'input.body': 'There'}})
        reworded = copy.deepcopy(base_doc)
        reworded['nodes'][0]['config']['input.title'] = 'Something else entirely'
        self.assertEqual(request_contract(base_doc, 'notes', 'create-note'),
                         request_contract(reworded, 'notes', 'create-note'))

        extra_field = copy.deepcopy(base_doc)
        del extra_field['nodes'][0]['config']['input.body']
        self.assertNotEqual(request_contract(base_doc, 'notes', 'create-note'),
                            request_contract(extra_field, 'notes', 'create-note'))

        extra_step = copy.deepcopy(base_doc)
        extra_step['nodes'].append({'id': 'write2', 'type': 'vela-app-action:notes:create-note',
                                    'config': {'input.title': 'x', 'input.body': 'y'}})
        self.assertNotEqual(request_contract(base_doc, 'notes', 'create-note'),
                            request_contract(extra_step, 'notes', 'create-note'))


if __name__ == '__main__':
    unittest.main()
