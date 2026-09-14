"""Explicit cross-app action permissions, atomic writes and repeat-safe execution."""
import json
import shutil
import unittest
from scripts.fixture_apps import APPS as FIXTURE_APPS
from concurrent.futures import ThreadPoolExecutor
import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app


class ActionTests(unittest.TestCase):
    tearDown = base.ApiBoundaryTests.tearDown
    session = base.ApiBoundaryTests.session

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        for app in ('notes', 'meals'):
            shutil.copytree(FIXTURE_APPS / app, self.apps / app)
        _, self.meals = self.session('meals')
        _, self.notes = self.session('notes')
        self.input = {'title': 'Dinner plan', 'body': 'Mon: Pasta\nTue: Soup'}

    def grant(self, allow=True):
        status = self.client.get('/api/apps/meals/actions', headers=self.hub).json()['requests'][0]
        return self.client.put('/api/apps/meals/actions/grant', headers=self.hub, json={'app': 'notes', 'action': 'create-note', 'allow': allow, 'sourceContract': status['sourceContract'], 'targetContract': status['targetContract']})

    def invoke(self, key='request-key-123', value=None, headers=None):
        return self.client.post('/api/app/actions/invoke', headers=headers or self.meals, json={'app': 'notes', 'action': 'create-note', 'input': value or self.input, 'key': key})

    def test_action_requires_explicit_grant_and_receipt_is_repeat_safe(self):
        self.assertEqual(self.invoke().status_code, 403)
        self.assertEqual(self.grant().status_code, 200)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.invoke(), range(2)))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(results[0].json()['executionId'], results[1].json()['executionId'])
        saved = self.client.get('/api/app/storage', headers=self.notes).json()
        self.assertEqual(len(saved['value']['notes']), 1)
        self.assertEqual(saved['value']['notes'][0]['body'], self.input['body'])
        self.assertEqual(self.invoke(value={'title': 'Different', 'body': 'Input'}).status_code, 409)
        events = self.client.get('/api/apps/meals/actions/history', headers=self.hub).json()['executions']
        self.assertEqual(sum(event['status'] == 'succeeded' for event in events), 1)
        self.assertNotIn('Pasta', json.dumps(events))
        self.grant(False)
        self.assertEqual(self.invoke().status_code, 403)

    def test_broker_cannot_be_used_to_read_or_choose_target_storage(self):
        self.grant()
        injected = {**self.input, 'id': 'chosen-id'}
        self.assertEqual(self.invoke(value=injected).status_code, 422)
        denied = self.client.put('/api/apps/meals/actions/grant', headers=self.meals, json={'app': 'notes', 'action': 'create-note', 'allow': True})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(self.invoke(headers=self.notes).status_code, 403)
        data = self.client.get('/api/app/storage', headers=self.meals).json()
        self.assertIsNone(data['value'])
        self.assertEqual(self.client.post('/api/app/actions/invoke', headers=self.meals, json={'app':'health','action':'create-note','input':self.input,'key':'request-key-123'}).status_code, 403)

    def test_notes_stale_save_cannot_erase_action_and_failures_do_not_write(self):
        self.client.put('/api/app/storage', headers=self.notes, json={'value': {'notes': []}, 'revision': 0})
        self.grant(); self.assertEqual(self.invoke().status_code, 200)
        stale = self.client.put('/api/app/storage', headers=self.notes, json={'value': {'notes': []}, 'revision': 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.invoke('oversized-request', {'title': 'X', 'body': 'x' * 33000}).status_code, 413)
        self.assertEqual(len(self.client.get('/api/app/storage', headers=self.notes).json()['value']['notes']), 1)

    def test_restart_keeps_receipts_but_updates_and_reinstalls_require_regrant(self):
        self.grant(); first = self.invoke().json()
        self.client.close(); self.client = TestClient(create_app(self.config))
        self.hub = {'Authorization': 'Bearer ' + self.client.get('/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
        _, self.meals = self.session('meals'); _, self.notes = self.session('notes')
        self.assertEqual(self.invoke().json()['executionId'], first['executionId'])
        path = self.config.installed_dir / 'notes/app.json'; raw = json.loads(path.read_text()); raw['version'] = '1.2.0'; path.write_text(json.dumps(raw))
        self.assertEqual(self.invoke().status_code, 403)
        self.grant(); self.assertEqual(self.invoke().status_code, 200)
        self.client.delete('/api/apps/notes', headers=self.hub); _, self.notes = self.session('notes')
        self.assertEqual(self.invoke('after-reinstall').status_code, 403)
        self.grant()
        self.assertEqual(self.invoke().json()['executionId'], first['executionId'])
        self.assertEqual(len(self.client.get('/api/app/storage', headers=self.notes).json()['value']['notes']), 1)

    def test_changed_grant_review_and_failed_output_validation_are_atomic(self):
        review = self.client.get('/api/apps/meals/actions', headers=self.hub).json()['requests'][0]
        path = self.config.installed_dir / 'notes/app.json'
        raw = json.loads(path.read_text()); raw['version'] = '1.2.0'; path.write_text(json.dumps(raw))
        self.assertEqual(self.client.put('/api/apps/meals/actions/grant', headers=self.hub, json={'app':'notes','action':'create-note','allow':True,'sourceContract':review['sourceContract'],'targetContract':review['targetContract']}).status_code, 409)
        output = self.config.installed_dir / 'notes/create-note.output.json'
        output.write_text(json.dumps({'type':'object','required':['unsupported-output']}))
        self.grant()
        self.assertEqual(self.invoke().status_code, 422)
        self.assertIsNone(self.client.get('/api/app/storage', headers=self.notes).json()['value'])

    def test_migrate_browser_notes_and_meals_preserves_originals(self):
        note = {'id':'old-note','title':'Old note','body':'Keep me','updated':1}
        migrated = self.client.post('/api/apps/notes/migration', headers=self.hub, json={'value':[note],'revision':0})
        self.assertEqual(migrated.status_code, 200, migrated.text)
        bundle = {'favorites':{'salmon-bowl':True}, 'plan':['salmon-bowl'] * 7}
        imported = self.client.post('/api/apps/meals/migration', headers=self.hub, json={'value':bundle,'revision':0})
        self.assertEqual(imported.status_code, 200, imported.text)
        value = imported.json()['value']
        self.assertEqual(value['imports'][0]['plan'], bundle['plan'])
        self.assertEqual(value['favorites'], {})  # Import stores a copy; applying it is separate.
        self.assertTrue(self.client.post('/api/apps/meals/migration', headers=self.hub, json={'value':bundle,'revision':0}).json()['alreadyImported'])
        self.grant(); self.assertEqual(self.invoke().status_code, 200)
        saved = self.client.get('/api/app/storage', headers=self.notes).json()['value']['notes']
        self.assertEqual(len(saved), 2)
        self.assertIn(note, saved)


if __name__ == '__main__': unittest.main()
