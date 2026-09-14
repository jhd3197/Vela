"""Independent release installation and transactional failure acceptance."""
import copy
import hashlib
import io
import json
import shutil
import sqlite3
import zipfile
from unittest.mock import patch
import unittest
from scripts.fixture_apps import APPS as FIXTURE_APPS

import test_app_contract as base
from vela.api import create_app
from vela.config import Config
from vela.releases import Releases
from vela.app_storage import AppServiceError
from fastapi.testclient import TestClient


class ReleaseTests(unittest.TestCase):
    setUp = base.ApiBoundaryTests.setUp
    tearDown = base.ApiBoundaryTests.tearDown

    def source(self, version='1.0.0', schema=1):
        folder = self.root / ('source-' + version)
        shutil.copytree(FIXTURE_APPS / 'health', folder)
        raw = json.loads((folder / 'app.json').read_text())
        raw['version'] = version
        raw['data']['schemaVersion'] = schema
        (folder / 'app.json').write_text(json.dumps(raw))
        return folder

    def review(self, folder):
        response = self.client.post('/api/releases/prepare', headers=self.hub, json={'folder': str(folder)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def commit(self, review):
        return self.client.post('/api/releases/' + review['review'] + '/commit', headers=self.hub, json={'capabilities': review['capabilities'], 'operations': review['operations']})

    def app_token(self):
        result = self.client.post('/api/apps/health/session', headers=self.hub)
        self.assertEqual(result.status_code, 200, result.text)
        return {'Authorization': 'Bearer ' + result.json()['token']}

    def write(self, value, revision=0):
        response = self.client.put('/api/app/storage', headers=self.app_token(), json={'value': value, 'revision': revision})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update_schema(self, folder, operations):
        raw = json.loads((folder / 'app.json').read_text())
        raw['data']['migrations'] = [{'from': 1, 'to': 2, 'file': 'migration.json'}]
        (folder / 'app.json').write_text(json.dumps(raw))
        schema = json.loads((folder / 'data.schema.json').read_text())
        schema['properties']['label'] = {'type': 'string'}
        schema['required'].append('label')
        (folder / 'data.schema.json').write_text(json.dumps(schema))
        (folder / 'migration.json').write_text(json.dumps(operations))

    def test_independent_install_update_migration_and_matching_rollback(self):
        first = self.source()
        self.assertEqual(self.commit(self.review(first)).status_code, 200)
        original = {'habits': []}
        self.write(original)
        old_token = self.app_token()
        update = self.source('1.1.0', 2)
        self.update_schema(update, [{'op': 'add', 'path': '/label', 'value': 'Migrated'}])
        review = self.review(update)
        shutil.rmtree(update)  # Installation depends only on reviewed staged bytes.
        result = self.commit(review)
        self.assertEqual(result.status_code, 200, result.text)
        saved = self.client.get('/api/app/storage', headers=self.app_token()).json()
        self.assertEqual(saved['value'], {'habits': [], 'label': 'Migrated'})
        self.assertEqual(saved['schemaVersion'], 2)
        self.assertEqual(self.client.get('/api/app/storage', headers=old_token).status_code, 401)
        self.write({'habits': [], 'label': 'Later edits'}, saved['revision'])
        rollback = self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health', 'rollback': result.json()['release']})
        self.assertEqual(rollback.status_code, 200, rollback.text)
        self.assertEqual(self.commit(rollback.json()).status_code, 200)
        restored = self.client.get('/api/app/storage', headers=self.app_token()).json()
        self.assertEqual(restored['value'], original)
        self.assertEqual(restored['schemaVersion'], 1)
        self.assertGreater(restored['revision'], saved['revision'])
        self.assertEqual(self.client.get('/api/apps/health', headers=self.hub).json()['version'], '1.0.0')
        self.assertEqual(self.client.get('/apps/health/').status_code, 200)

    def test_review_guards_data_permissions_and_invalid_migration(self):
        self.commit(self.review(self.source()))
        self.write({'habits': []})
        update = self.source('1.1.0')
        review = self.review(update)
        denied = self.client.post('/api/releases/' + review['review'] + '/commit', headers=self.hub, json={'capabilities': [], 'operations': []})
        self.assertEqual(denied.status_code, 403)
        self.write({'habits': []}, 1)
        self.assertEqual(self.commit(review).status_code, 409)
        failing = self.source('1.2.0', 2)
        self.update_schema(failing, [{'op': 'remove', 'path': '/absent'}])
        self.assertEqual(self.client.post('/api/releases/prepare', headers=self.hub, json={'folder': str(failing)}).status_code, 422)
        self.assertEqual(self.client.get('/api/apps/health', headers=self.hub).json()['version'], '1.0.0')
        self.assertEqual(self.client.get('/api/app/storage', headers=self.app_token()).json()['revision'], 2)

    def test_failure_after_swap_restores_package_and_data(self):
        self.commit(self.review(self.source()))
        self.write({'habits': []})
        review = self.review(self.source('1.1.0'))
        original = Releases._check_package
        def fail_at_target(manager, folder, **kwargs):
            if folder == self.config.installed_dir / 'health': raise AppServiceError(422, 'Injected readiness failure')
            return original(manager, folder, **kwargs)
        with patch.object(Releases, '_check_package', fail_at_target):
            self.assertEqual(self.commit(review).status_code, 422)
        self.assertEqual(self.client.get('/api/apps/health', headers=self.hub).json()['version'], '1.0.0')
        self.assertEqual(self.client.get('/api/app/storage', headers=self.app_token()).json()['revision'], 1)
        self.assertEqual(self.client.get('/apps/health/').status_code, 200)

    def test_archive_rejects_traversal_links_duplicates_and_missing_entry(self):
        for names in ([('../escape.txt', b'x')], [('C:/escape', b'x')], [('a', b'x'), ('A', b'y')]):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name, data in names: archive.writestr(name, data)
            response = self.client.post('/api/releases/upload', headers={**self.hub, 'Content-Type': 'application/zip'}, content=stream.getvalue())
            self.assertEqual(response.status_code, 422, response.text)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            member = zipfile.ZipInfo('link'); member.external_attr = 0o120777 << 16
            archive.writestr(member, '../outside')
        self.assertEqual(self.client.post('/api/releases/upload', headers=self.hub, content=stream.getvalue()).status_code, 422)
        source = self.source(); (source / 'index.html').unlink()
        self.assertEqual(self.client.post('/api/releases/prepare', headers=self.hub, json={'folder': str(source)}).status_code, 422)

    def test_catalog_digest_offline_cache_and_source_independence(self):
        source = self.source()
        catalog_dir = self.root / 'pinned'; catalog_dir.mkdir()
        archive = catalog_dir / 'health.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for file in source.iterdir(): zipped.write(file, file.name)
        index = catalog_dir / 'index.json'
        entry = {'manifest': json.loads((source / 'app.json').read_text()), 'publisher': 'test-publisher', 'archive': 'health.zip', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        index.write_text(json.dumps({'schemaVersion': 1, 'releases': [entry]}))
        config = Config(self.config.data_dir, self.apps, base.ROOT / 'web/dist', catalog_source=str(index))
        self.client.close(); self.client = TestClient(create_app(config))
        self.hub = {'Authorization': 'Bearer ' + self.client.get('/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
        archive.write_bytes(b'tampered')
        self.assertEqual(self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health'}).status_code, 422)
        with zipfile.ZipFile(archive, 'w') as zipped:
            for file in source.iterdir(): zipped.write(file, file.name)
        review = self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health'})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(self.commit(review.json()).status_code, 200)
        index.unlink(); archive.unlink(); shutil.rmtree(source)
        self.assertTrue(self.client.post('/api/catalog/refresh', headers=self.hub).json()['cached'])
        self.client.delete('/api/apps/health', headers=self.hub)
        cached = self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health'})
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertEqual(self.commit(cached.json()).status_code, 200)
        self.client.close(); self.client = TestClient(create_app(config))
        self.assertEqual(self.client.get('/apps/health/').status_code, 200)

    def test_uncommitted_journal_is_recovered_on_restart(self):
        self.commit(self.review(self.source()))
        record = self.config.data_dir / 'releases' / 'crash-fixture'
        shutil.copytree(self.config.installed_dir / 'health', record / 'previous')
        (record / 'journal.json').write_text(json.dumps({'app_id': 'health', 'previous': True}))
        (self.config.installed_dir / 'health/index.html').write_text('partially swapped content')
        self.client.close(); self.client = TestClient(create_app(self.config))
        self.assertNotIn('partially swapped content', self.client.get('/apps/health/').text)
        self.assertFalse((record / 'journal.json').exists())

    def test_retained_legacy_rollback_and_integrity_guard(self):
        source = self.source()
        old = json.loads((source / 'app.json').read_text())
        old = {key: old[key] for key in ('id', 'name', 'version', 'description', 'category', 'author')}
        old['platforms'] = {'web': {'entry': 'index.html'}}
        target = self.config.installed_dir / 'health'
        shutil.copytree(source, target); (target / 'app.json').write_text(json.dumps(old))
        updated = self.commit(self.review(self.source('1.1.0'))).json()
        review = self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health', 'rollback': updated['release']})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertTrue(review.json()['trustedLegacy'])
        self.assertEqual(self.commit(review.json()).status_code, 200)
        self.assertEqual(self.client.get('/api/apps/health', headers=self.hub).json()['schemaVersion'], 1)
        (self.config.data_dir / 'releases' / updated['release'] / 'previous/index.html').write_text('damaged')
        self.assertEqual(self.client.post('/api/releases/prepare', headers=self.hub, json={'app_id': 'health', 'rollback': updated['release']}).status_code, 422)

    def test_remote_catalog_trust_pin_and_last_good_index(self):
        from vela.catalog import Catalog
        raw = json.loads((self.source() / 'app.json').read_text())
        data = json.dumps({'schemaVersion': 1, 'releases': [{'manifest': raw, 'publisher': 'publisher', 'archive': 'health.zip', 'sha256': 'a' * 64}]}).encode()
        config = Config(self.config.data_dir, self.apps, base.ROOT / 'web/dist', catalog_source='https://catalog.example/index.json', catalog_sha256=hashlib.sha256(data).hexdigest())
        catalog = Catalog(config)
        with patch('vela.catalog.fetch_bytes', return_value=data):
            self.assertIsNone(catalog.refresh()['error'])
        with patch('vela.catalog.fetch_bytes', return_value=data + b' '):
            self.assertTrue(catalog.refresh()['cached'])
            self.assertEqual(len(catalog.entries), 1)
        no_pin = Catalog(Config(self.root / 'unpinned', self.apps, base.ROOT / 'web/dist', catalog_source='https://catalog.example/index.json'))
        with patch('vela.catalog.fetch_bytes') as download:
            self.assertIn('trust pin', no_pin.refresh()['error'])
            download.assert_not_called()

    def test_committed_journal_keeps_new_package_and_app_tokens_cannot_import(self):
        self.commit(self.review(self.source()))
        token = self.app_token()
        self.assertEqual(self.client.post('/api/releases/prepare', headers=token, json={'folder': str(self.root)}).status_code, 401)
        result = self.commit(self.review(self.source('1.1.0'))).json()
        record = self.config.data_dir / 'releases' / result['release']
        (record / 'journal.json').write_text(json.dumps({'app_id': 'health', 'previous': True}))
        self.client.close(); self.client = TestClient(create_app(self.config))
        manifest = json.loads((self.config.installed_dir / 'health/app.json').read_text())
        self.assertEqual(manifest['version'], '1.1.0')
        self.assertFalse((record / 'journal.json').exists())


if __name__ == '__main__': unittest.main()
