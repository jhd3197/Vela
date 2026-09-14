"""Release sequencing and retry tests; Git remotes and downloads are disposable."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import release


def project(root):
    (root / 'vela').mkdir(parents=True)
    (root / 'web').mkdir()
    (root / 'vela/__init__.py').write_text('__version__ = "0.1.0"\n', encoding='utf-8')
    (root / 'web/package.json').write_text('{"name":"vela-web","version":"0.1.0"}', encoding='utf-8')
    (root / 'web/package-lock.json').write_text('{"version":"0.1.0","packages":{"":{"version":"0.1.0"}}}', encoding='utf-8')
    (root / 'README.md').write_text('version-0.1.0-blue\n', encoding='utf-8')
    (root / 'CHANGELOG.md').write_text('# Changelog\n\n## Unreleased\n\n### Added\n\n- First server.\n', encoding='utf-8')


def downloads(root, version='0.1.0'):
    root.mkdir()
    for platform in ('windows-x64.zip', 'linux-x64.tar.gz', 'macos-arm64.tar.gz'):
        path = root / f'vela-server-{version}-{platform}'
        path.write_bytes(platform.encode())
        path.with_name(path.name + '.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')


class ReleaseAutomationTests(unittest.TestCase):
    def test_version_selection_and_invalid_inputs(self):
        self.assertEqual(release.next_version('0.1.0', []), '0.1.0')
        self.assertEqual(release.next_version('0.1.0', ['v0.9.9', 'v0.10.2', 'agent-v8.0.0']), '0.10.3')
        self.assertEqual(release.next_version('1.0.0', ['v0.10.2']), '1.0.0')
        self.assertEqual(release.next_version('0.1.0', ['v0.1.0'], '0.2.0'), '0.2.0')
        for requested in ('0.1.0', '0.0.9', 'v0.2.0', '0.02.0', '0.2.0; echo bad'):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                release.next_version('0.1.0', ['v0.1.0'], requested)

    def test_stamp_keeps_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project(root)
            release.stamp(root, '0.1.0', '2026-09-14')
            before = {name: (root / name).read_bytes() for name in release.RELEASE_FILES}
            release.stamp(root, '0.1.0', '2026-09-15')
            self.assertEqual(before, {name: (root / name).read_bytes() for name in release.RELEASE_FILES})
            self.assertIn('## 0.1.0 - 2026-09-14', (root / 'CHANGELOG.md').read_text())
            release.stamp(root, '0.1.1', '2026-09-15')
            text = (root / 'CHANGELOG.md').read_text()
            self.assertIn('## 0.1.0 - 2026-09-14', text)
            self.assertIn('## 0.1.1 - 2026-09-15', text)
            self.assertEqual(json.loads((root / 'web/package-lock.json').read_text())['packages']['']['version'], '0.1.1')

    def test_all_platforms_and_hashes_required(self):
        with tempfile.TemporaryDirectory() as temp:
            assets = Path(temp) / 'assets'
            downloads(assets)
            self.assertEqual(len(release.validate_assets(assets, '0.1.0')), 6)
            archive = assets / 'vela-server-0.1.0-windows-x64.zip'
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Checksum'):
                release.validate_assets(assets, '0.1.0')
            archive.unlink()
            with self.assertRaisesRegex(ValueError, 'three'):
                release.validate_assets(assets, '0.1.0')

    def test_retry_after_tag_push_does_not_bump_or_replace_published_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            remote = Path(temp) / 'remote.git'
            project(root)
            real_run = subprocess.run
            def command(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
            command('init', '-b', 'main')
            command('config', 'user.name', 'Fixture')
            command('config', 'user.email', 'fixture@example.test')
            command('add', '.')
            command('commit', '-m', 'Initial fixture')
            command('init', '--bare', str(remote))
            command('remote', 'add', 'origin', str(remote))
            command('push', '-u', 'origin', 'main')
            source = command('rev-parse', 'HEAD')
            assets = Path(temp) / 'assets'
            downloads(assets)
            calls = []
            def fake_gh(args, **kwargs):
                if args[0] == 'gh':
                    calls.append(args)
                    return subprocess.CompletedProcess(args, 0)
                return real_run(args, **kwargs)
            with patch.object(release, 'ROOT', root), patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': 'fixture/repo'}), patch.object(release, 'release_state', return_value=None), patch.object(release.subprocess, 'run', side_effect=fake_gh):
                release.stamp(root, '0.1.0', '2026-09-14')
                release.publish('0.1.0', source, assets)
                result = release.prepare()
                self.assertEqual(result['version'], '0.1.0')
                self.assertEqual(result['source'], source)
                self.assertEqual(result['date'], '2026-09-14')
                self.assertEqual(result['build_ref'], command('rev-parse', 'v0.1.0^{commit}'))
                self.assertEqual([call[2] for call in calls], ['create', 'upload', 'edit'])
                self.assertIn('--draft', calls[0])
                self.assertIn('--draft=false', calls[2])
                self.assertIn('[skip ci]', command('log', '-1', '--format=%s'))
                calls.clear()
                with patch.object(release, 'release_state', return_value={'isDraft': False, 'url': 'https://example.test/release'}):
                    release.publish('0.1.0', source, assets)
                self.assertEqual(calls, [])
                self.assertEqual(command('rev-list', '--count', 'HEAD'), '2')

    def test_dev_cannot_publish(self):
        with patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/dev'}), self.assertRaisesRegex(ValueError, 'main'):
            release.publish('0.1.0', 'a' * 40, Path('unused'))
