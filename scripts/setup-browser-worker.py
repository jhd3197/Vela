"""Install the browser worker's pinned runtime and record what was installed.

An agent desktop renders its app views in a managed Chromium that Vela starts and
stops. Two things have to match: the pinned `playwright-core` package and the
browser build that version expects. Installing "whatever Chromium is current"
during a task is exactly the behavior the plan forbids, so the revision is
resolved here, once, and written into `provenance.json` for the packaging step
to carry.

    python scripts/setup-browser-worker.py
    python scripts/setup-browser-worker.py --check

`node_modules/` and `provenance.json` are ignored by Git; the latter ships inside
a server download so a packaged Vela can say which browser it carries.
"""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / 'scripts/browser-worker'
PROVENANCE = WORKER / 'provenance.json'


def npm() -> str:
    for name in ('npm.cmd', 'npm') if platform.system() == 'Windows' else ('npm',):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit('npm was not found on PATH. Install Node.js 22.13+ (or 24+) first.')


def node() -> str:
    return shutil.which('node') or 'node'


def probe() -> dict:
    """Ask the installed runtime where its browser is and whether it is there.

    Run in the worker's own directory so `playwright-core` resolves from the
    worker's dependency tree rather than whatever else is on this machine.
    """
    script = (
        "import { existsSync } from 'node:fs';"
        "import { chromium } from 'playwright-core';"
        "let path = null, error = null;"
        'try { path = chromium.executablePath(); } catch (e) { error = e.message.split("\\n")[0]; }'
        "process.stdout.write(JSON.stringify({ path, error, present: Boolean(path) && existsSync(path) }));"
    )
    try:
        result = subprocess.run([node(), '--input-type=module', '-e', script],
                                cwd=WORKER, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as error:
        return {'path': None, 'present': False, 'error': str(error)}
    if result.returncode != 0:
        return {'path': None, 'present': False, 'error': result.stderr.strip()[:400]}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {'path': None, 'present': False, 'error': 'the runtime did not report a browser path'}


def source_digest() -> str:
    """One digest over every file the worker actually runs.

    Recorded at install time and checked before the worker is started. What it
    catches is a bundle whose Python half was updated and whose worker half was
    not — two versions of a protocol talking past each other, which produces a
    failure that reads like a bug in whatever the agent was doing rather than
    like a broken download.
    """
    digest = hashlib.sha256()
    for path in sorted((WORKER / 'src').rglob('*.mjs')):
        digest.update(path.relative_to(WORKER).as_posix().encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def installed_version() -> str | None:
    package = WORKER / 'node_modules/playwright-core/package.json'
    if not package.is_file():
        return None
    return json.loads(package.read_text(encoding='utf-8'))['version']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='Report what is installed without changing anything.')
    parser.add_argument('--skip-browser', action='store_true',
                        help='Install the package only. The worker stays unavailable until the browser is fetched.')
    arguments = parser.parse_args()

    manifest = json.loads((WORKER / 'package.json').read_text(encoding='utf-8'))
    pinned = manifest['dependencies']['playwright-core']

    if arguments.check:
        current = probe()
        print(f'playwright-core pinned at {pinned}, installed: {installed_version() or "no"}')
        print(f'browser: {current.get("path") or "unknown"}')
        print(f'sources: {source_digest()[:16]}')
        print(f'present: {"yes" if current.get("present") else "no"}')
        if current.get('error'):
            print(f'reason: {current["error"]}')
        return 0 if current.get('present') else 1

    subprocess.run([npm(), 'install', '--omit=dev', '--no-audit', '--no-fund'],
                   cwd=WORKER, check=True)

    if not arguments.skip_browser:
        # `playwright-core` downloads nothing on install, which is the point:
        # the browser arrives here, deliberately, and only for Chromium.
        subprocess.run([npm(), 'exec', '--', 'playwright-core', 'install', 'chromium'],
                       cwd=WORKER, check=True)

    current = probe()
    try:
        node_version = subprocess.run([node(), '--version'], capture_output=True, text=True,
                                      check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        node_version = 'unknown'

    PROVENANCE.write_text(
        json.dumps(
            {
                'source': {'kind': 'registry', 'package': 'playwright-core', 'version': pinned,
                           'lockfile': (WORKER / 'package-lock.json').is_file()},
                'installed': {'playwright-core': installed_version()},
                'browser': {'name': 'chromium', 'executable': current.get('path'),
                            'present': current.get('present', False)},
                'platform': {'system': platform.system(), 'machine': platform.machine()},
                'installedWithNode': node_version,
                # Checked before the worker is launched. See `source_digest`.
                'sources': {'algorithm': 'sha256', 'digest': source_digest()},
            },
            indent=2, sort_keys=True,
        ) + '\n',
        encoding='utf-8',
    )

    print(f'Browser worker installed: playwright-core {installed_version()}')
    print(f'  chromium: {current.get("path") or "not installed"}')
    if not current.get('present'):
        if arguments.skip_browser:
            # The browser was skipped on purpose. The package and the provenance
            # record are exactly what this run promised, and both are now in
            # place — which is what packaging ships: a worker that says which
            # browser to fetch, not the browser. So this is a success, and the
            # message says what is still missing rather than failing over it.
            print('\nThe browser was skipped on request, so agent desktops stay unavailable '
                  'until it is fetched. The package and its provenance are installed.')
            return 0
        print('\nThe browser is not on this machine yet, so agent desktops stay unavailable.')
        print('Run this script again without --skip-browser once the download can succeed.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
