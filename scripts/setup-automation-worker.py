"""Install the automation worker's pinned dependencies and record their provenance.

The worker needs `@tramo/runtime` (which pulls `@tramo/spec`). A release must use
reproducible package artifacts, so the registry is the default source. A local
Tramo checkout is a development convenience and is always recorded as such.

    python scripts/setup-automation-worker.py
    python scripts/setup-automation-worker.py --tramo-source ../tramo

Both `node_modules/` and the generated `provenance.json` are ignored by Git; the
latter ships inside a server download so a packaged Vela can say what it carries.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / 'scripts/automation-worker'
PROVENANCE = WORKER / 'provenance.json'
PACKAGES = ('spec', 'runtime')


def npm() -> str:
    for name in ('npm.cmd', 'npm') if platform.system() == 'Windows' else ('npm',):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit('npm was not found on PATH. Install Node.js 22.13+ (or 24+) first.')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack_local(source: Path, destination: Path) -> dict[str, str]:
    """Build tarballs from a Tramo checkout without modifying or publishing it."""
    destination.mkdir(parents=True, exist_ok=True)
    folders = {'spec': 'packages/spec', 'runtime': 'packages/runtime'}
    artifacts = {}
    for name, folder in folders.items():
        package = source / folder
        if not (package / 'dist').is_dir():
            raise SystemExit(
                f'{package} has no dist/. Build Tramo first: npm run build:spec && npm run build:runtime'
            )
        subprocess.run([npm(), 'pack', '--pack-destination', str(destination)],
                       cwd=package, check=True, capture_output=True, text=True)
        version = json.loads((package / 'package.json').read_text(encoding='utf-8'))['version']
        artifacts[name] = str(destination / f'tramo-{name}-{version}.tgz')
    return artifacts


def registry_available(version: str) -> bool:
    try:
        result = subprocess.run([npm(), 'view', f'@tramo/runtime@{version}', 'version'],
                                capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and version in result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tramo-source', default=os.environ.get('VELA_TRAMO_SOURCE'),
                        help='Path to a local Tramo checkout to use when the registry version is unavailable.')
    parser.add_argument('--offline', action='store_true', help='Skip the registry check and use the local source.')
    arguments = parser.parse_args()

    manifest = json.loads((WORKER / 'package.json').read_text(encoding='utf-8'))
    pinned = manifest['dependencies']['@tramo/runtime']

    if not arguments.offline and registry_available(pinned):
        # An exact-version install with its own lockfile: `@tramo/runtime` pins
        # `@tramo/spec` exactly, so the tree is one fixed pair of packages.
        subprocess.run([npm(), 'install', '--omit=dev', '--no-audit', '--no-fund'],
                       cwd=WORKER, check=True)
        source = {'kind': 'registry', 'version': pinned,
                  'lockfile': (WORKER / 'package-lock.json').is_file()}
    else:
        if not arguments.tramo_source:
            raise SystemExit(
                f'@tramo/runtime@{pinned} is not available from the npm registry and no local Tramo\n'
                'checkout was given. Publish that version, or pass --tramo-source <path to tramo>.\n'
                'A build that used a local checkout is development-only and is recorded as such.'
            )
        checkout = Path(arguments.tramo_source).expanduser().resolve()
        if not (checkout / 'packages/runtime/package.json').is_file():
            raise SystemExit(f'{checkout} does not look like a Tramo checkout.')
        staging = ROOT / '.local/tramo-packages'
        artifacts = pack_local(checkout, staging)
        subprocess.run([npm(), 'install', '--omit=dev', '--no-audit', '--no-fund', '--no-save',
                        artifacts['spec'], artifacts['runtime']], cwd=WORKER, check=True)
        try:
            revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                                      capture_output=True, text=True, check=True).stdout.strip()
            dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=checkout,
                                        capture_output=True, text=True, check=True).stdout.strip())
        except (subprocess.SubprocessError, OSError):
            revision, dirty = 'unknown', True
        source = {
            'kind': 'local-checkout',
            'version': pinned,
            'path': str(checkout),
            'commit': revision,
            'uncommittedChanges': dirty,
            'artifacts': {name: {'file': Path(path).name, 'sha256': digest(Path(path))}
                          for name, path in artifacts.items()},
            'note': 'Development build. A release must install the published package.',
        }

    installed = {}
    for name in PACKAGES:
        package = WORKER / f'node_modules/@tramo/{name}/package.json'
        installed[f'@tramo/{name}'] = json.loads(package.read_text(encoding='utf-8'))['version'] \
            if package.is_file() else None
    try:
        node_version = subprocess.run([shutil.which('node') or 'node', '--version'],
                                      capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        node_version = 'unknown'
    PROVENANCE.write_text(
        json.dumps({'source': source, 'installed': installed, 'installedWithNode': node_version},
                   indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'Automation worker dependencies installed from {source["kind"]}.')
    for name, version in installed.items():
        print(f'  {name} {version}')
    if source['kind'] == 'local-checkout':
        print('\nThis is a development install. Publish @tramo/runtime '
              f'{pinned} before building a release download.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
