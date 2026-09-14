"""Version, validate and publish Vela's tested server downloads."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
RELEASE_FILES = ('vela/__init__.py', 'web/package.json', 'web/package-lock.json', 'README.md', 'CHANGELOG.md')


def version_tuple(value):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Version must be MAJOR.MINOR.PATCH, without a v prefix or suffix')
    return tuple(map(int, value.split('.')))


def next_version(current, tags, requested=''):
    version_tuple(current)
    versions = [version_tuple(tag[1:]) for tag in tags if re.fullmatch(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', tag)]
    latest = max(versions, default=None)
    if requested:
        chosen = version_tuple(requested)
        if latest is not None and chosen <= latest:
            raise ValueError('A new release version must be greater than existing release tags')
        return requested
    if latest is None or version_tuple(current) > latest:
        return current
    return '.'.join(map(str, (*latest[:2], latest[2] + 1)))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, encoding='utf-8').strip()


def tag_source(tag):
    message = git('for-each-ref', f'refs/tags/{tag}', '--format=%(contents)')
    match = re.search(r'^Source-Commit: ([a-f0-9]{40})$', message, re.M)
    return match[1] if match else None


def prepare(requested=''):
    source = git('rev-parse', 'HEAD')
    tags = [tag for tag in git('tag', '--list', 'v*').splitlines()
            if re.fullmatch(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', tag)]
    matches = [tag for tag in tags if tag_source(tag) == source or git('rev-parse', f'{tag}^{{commit}}') == source]
    if requested:
        version_tuple(requested)
        matches = [tag for tag in matches if tag == 'v' + requested]
    if matches:
        # A retry uses the original immutable release commit and version.
        tag = max(matches, key=lambda item: version_tuple(item[1:]))
        build_ref = git('rev-parse', f'{tag}^{{commit}}')
        source = tag_source(tag) or source
        changelog = git('show', f'{build_ref}:CHANGELOG.md')
        match = re.search(r'^## ' + re.escape(tag[1:]) + r' - (\d{4}-\d{2}-\d{2})$', changelog, re.M)
        if not match:
            raise ValueError('Existing tag has no matching release changelog; refusing to replace it')
        result = {'version': tag[1:], 'tag': tag, 'source': source, 'build_ref': build_ref, 'date': match[1]}
    else:
        current = re.search(r'^__version__ = "([^"]+)"$', (ROOT / 'vela/__init__.py').read_text(encoding='utf-8'), re.M)[1]
        version = next_version(current, tags, requested)
        result = {'version': version, 'tag': 'v' + version, 'source': source, 'build_ref': source,
                  'date': datetime.now(timezone.utc).date().isoformat()}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key, value in result.items():
                output.write(f'{key}={value}\n')
    print(json.dumps(result))
    return result


def stamp(root, version, release_date):
    version_tuple(version)
    date.fromisoformat(release_date)
    path = root / 'vela/__init__.py'
    text, count = re.subn(r'^__version__ = "[^"]+"$', f'__version__ = "{version}"', path.read_text(encoding='utf-8'), flags=re.M)
    if count != 1:
        raise ValueError('Expected exactly one Vela version declaration')
    path.write_text(text, encoding='utf-8', newline='\n')
    for name in ('web/package.json', 'web/package-lock.json'):
        path = root / name
        data = json.loads(path.read_text(encoding='utf-8'))
        data['version'] = version
        if name.endswith('package-lock.json'):
            data['packages']['']['version'] = version
        path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    path = root / 'README.md'
    text = re.sub(r'version-\d+\.\d+\.\d+-', f'version-{version}-', path.read_text(encoding='utf-8'))
    path.write_text(text, encoding='utf-8', newline='\n')
    path = root / 'CHANGELOG.md'
    text = path.read_text(encoding='utf-8')
    if re.search(r'^## ' + re.escape(version) + r' - ', text, re.M):
        return  # Rebuilding an existing release must not move newer Unreleased notes.
    match = re.search(r'^## Unreleased\s*\n', text, re.M)
    if not match:
        raise ValueError('CHANGELOG.md must have an Unreleased section')
    rest = text[match.end():]
    following = re.search(r'^## ', rest, re.M)
    body = rest[:following.start()].strip() if following else rest.strip()
    history = rest[following.start():] if following else ''
    if not body:
        body = 'No additional release notes were provided.'
    text = text[:match.start()] + f'## Unreleased\n\n## {version} - {release_date}\n\n{body}\n\n' + history
    path.write_text(text.rstrip() + '\n', encoding='utf-8', newline='\n')


def validate_assets(directory, version):
    version_tuple(version)
    pattern = re.compile(r'vela-server-' + re.escape(version) + r'-(windows-x64\.zip|windows-x64-setup\.exe|linux-x64\.tar\.gz|macos-(?:x64|arm64)\.tar\.gz)')
    archives = sorted(path for path in directory.iterdir() if path.is_file() and not path.name.endswith('.sha256'))
    platforms = set()
    if len(archives) != 4:
        raise ValueError('Release needs three platform archives and one Windows installer')
    for archive in archives:
        match = pattern.fullmatch(archive.name)
        if not match:
            raise ValueError('Unexpected release artifact: ' + archive.name)
        platforms.add('installer' if match[1].endswith('.exe') else match[1].split('-')[0])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar = archive.with_name(archive.name + '.sha256')
        expected = sidecar.read_text(encoding='utf-8').split()
        if expected != [digest, archive.name]:
            raise ValueError('Checksum or filename mismatch: ' + archive.name)
    if platforms != {'windows', 'linux', 'macos', 'installer'}:
        raise ValueError('Release is missing a platform or the Windows installer')
    expected_names = {path.name + suffix for path in archives for suffix in ('', '.sha256')}
    if {path.name for path in directory.iterdir()} != expected_names:
        raise ValueError('Unexpected files in release artifact directory')
    return sorted(directory.iterdir())


def release_state(repo, tag):
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'isDraft,url'],
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode == 0:
        return json.loads(result.stdout)
    if 'release not found' in result.stderr.lower() or 'HTTP 404' in result.stderr:
        return None
    raise RuntimeError(result.stderr.strip())


def publish(version, source, directory):
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Releases may only be published from main')
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not re.fullmatch('[a-f0-9]{40}', source):
        raise ValueError('Invalid repository or source commit')
    assets = validate_assets(directory, version)
    tag = 'v' + version
    git('fetch', 'origin', 'main', '--tags')
    existing = tag in git('tag', '--list', tag).splitlines()
    if existing:
        if tag_source(tag) != source:
            raise ValueError('Existing tag belongs to another source commit; refusing to move it')
        if git('rev-parse', 'HEAD') != git('rev-parse', f'{tag}^{{commit}}'):
            raise ValueError('Retry must use the existing release commit')
    else:
        if git('rev-parse', 'origin/main') != source:
            raise ValueError('main advanced during the build; run the workflow on current main')
        git('config', 'user.name', 'github-actions[bot]')
        git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        git('add', *RELEASE_FILES)
        git('commit', '-m', f'chore(release): {tag} [skip ci]')
        git('tag', '-a', tag, '-m', f'Vela {tag}\n\nSource-Commit: {source}')
        # Atomically update both refs; never force main or move an existing tag.
        git('push', '--atomic', 'origin', 'HEAD:main', f'refs/tags/{tag}')
    state = release_state(repo, tag)
    if state and not state['isDraft']:
        print('Already published; leaving release assets unchanged: ' + state['url'])
        return
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(r'^## ' + re.escape(version) + r' - [^\n]+\n(.*?)(?=^## |\Z)', changelog, re.M | re.S)
    if not match:
        raise ValueError('Release changelog section is missing')
    notes = ROOT / '.local/release-notes.md'
    notes.parent.mkdir(exist_ok=True)
    downloads = '\n'.join(f'- [{asset.name}](https://github.com/{repo}/releases/download/{tag}/{asset.name})'
                          for asset in assets if not asset.name.endswith('.sha256'))
    notes.write_text(f'# Vela Server {tag}\n\n{match[1].strip()}\n\n## Downloads\n\n{downloads}\n\n'
                     '**Windows:** download `windows-x64-setup.exe` to install Vela, or extract the Windows ZIP '
                     'and open `Vela.exe` without installing. Vela runs beside the clock; use its tray menu '
                     'to open the dashboard, start/stop the server, or enable start at sign in.\n\n'
                     '**macOS/Linux:** extract the archive and run `./Vela` in a terminal. '
                     'Keep the `_internal` folder beside the executable in every portable download. '
                     'No Python or Node setup is required.\n\n'
                     'Downloads are unsigned. '
                     'SHA-256 files are attached for download verification.\n', encoding='utf-8')
    def gh(*args):
        subprocess.run(['gh', 'release', *args, '--repo', repo], check=True)
    if state is None:
        gh('create', tag, '--verify-tag', '--draft', '--title', f'Vela Server {tag}', '--notes-file', str(notes))
    gh('upload', tag, *map(str, assets), '--clobber')  # Only a draft can reach this point.
    gh('edit', tag, '--notes-file', str(notes), '--draft=false', '--latest')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare').add_argument('--version', default='')
    stamp_parser = commands.add_parser('stamp')
    stamp_parser.add_argument('--version', required=True)
    stamp_parser.add_argument('--date', required=True)
    publish_parser = commands.add_parser('publish')
    publish_parser.add_argument('--version', required=True)
    publish_parser.add_argument('--source', required=True)
    publish_parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.version)
    elif args.command == 'stamp':
        stamp(ROOT, args.version, args.date)
    else:
        publish(args.version, args.source, args.artifacts)


if __name__ == '__main__':
    main()
