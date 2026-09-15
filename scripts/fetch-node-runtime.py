"""Download the pinned Node runtime that ships inside a Vela server download.

Automations execute in a small Node worker. A person who installs Vela should
not have to install Node as well, so the release bundle carries one verified
copy of the official binary.

Only the `node` executable is kept: the official builds are self-contained, so
the rest of the archive (npm, headers, documentation) is not shipped.

    python scripts/fetch-node-runtime.py

The artifact digests below are the official nodejs.org SHASUMS256 values for this
exact version, pinned here so a build verifies against something committed rather
than against a file fetched at the same moment. To move to a new Node version,
replace the version and all five digests together from
https://nodejs.org/dist/vX.Y.Z/SHASUMS256.txt and note the change in CHANGELOG.
"""
import hashlib
import shutil
import tarfile
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / '.local/node-runtime'

NODE_VERSION = '22.17.0'
BASE_URL = f'https://nodejs.org/dist/v{NODE_VERSION}'

#: (system, machine) -> (archive name, sha256, path of the binary inside it)
ARTIFACTS = {
    ('Windows', 'x64'): (
        f'node-v{NODE_VERSION}-win-x64.zip',
        '721ab118a3aac8584348b132767eadf51379e0616f0db802cc1e66d7f0d98f85',
        f'node-v{NODE_VERSION}-win-x64/node.exe',
    ),
    ('Linux', 'x64'): (
        f'node-v{NODE_VERSION}-linux-x64.tar.xz',
        '325c0f1261e0c61bcae369a1274028e9cfb7ab7949c05512c5b1e630f7e80e12',
        f'node-v{NODE_VERSION}-linux-x64/bin/node',
    ),
    ('Linux', 'arm64'): (
        f'node-v{NODE_VERSION}-linux-arm64.tar.xz',
        '140aee84be6774f5fb3f404be72adbe8420b523f824de82daeb5ab218dab7b18',
        f'node-v{NODE_VERSION}-linux-arm64/bin/node',
    ),
    ('Darwin', 'x64'): (
        f'node-v{NODE_VERSION}-darwin-x64.tar.gz',
        'c39c8ec3cdadedfcc75de0cb3305df95ae2aecebc5db8d68a9b67bd74616d2ad',
        f'node-v{NODE_VERSION}-darwin-x64/bin/node',
    ),
    ('Darwin', 'arm64'): (
        f'node-v{NODE_VERSION}-darwin-arm64.tar.gz',
        '615dda58b5fb41fad2be43940b6398ca56554cbe05800953afadc724729cb09e',
        f'node-v{NODE_VERSION}-darwin-arm64/bin/node',
    ),
}


def current_target() -> tuple[str, str]:
    machine = platform.machine().lower()
    architecture = {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64'}.get(machine, machine)
    return platform.system(), architecture


def binary_path() -> Path:
    system, _ = current_target()
    return TARGET / ('node.exe' if system == 'Windows' else 'node')


def fetch(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as response, destination.open('wb') as output:
        shutil.copyfileobj(response, output)


def extract(archive: Path, member: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as bundle, destination.open('wb') as output:
            shutil.copyfileobj(bundle.open(member), output)
        return
    with tarfile.open(archive) as bundle:
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit(f'{member} is missing from {archive.name}')
        with destination.open('wb') as output:
            shutil.copyfileobj(source, output)
    destination.chmod(0o755)


def main() -> int:
    target = current_target()
    if target not in ARTIFACTS:
        raise SystemExit(
            f'No pinned Node runtime for {target[0]} {target[1]}. Add its official digest to '
            'scripts/fetch-node-runtime.py, or build on a supported platform.')
    name, expected, member = ARTIFACTS[target]
    destination = binary_path()
    if destination.is_file():
        print(f'Node runtime already present: {destination}')
        return 0

    archive = ROOT / '.local/node-downloads' / name
    if not archive.is_file():
        print(f'Downloading {BASE_URL}/{name}')
        fetch(f'{BASE_URL}/{name}', archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        archive.unlink(missing_ok=True)
        raise SystemExit(
            f'{name} does not match its pinned checksum.\n  expected {expected}\n  found    {digest}\n'
            'The download was discarded. Check your connection, then retry.')
    extract(archive, member, destination)
    (TARGET / 'VERSION').write_text(
        f'Node.js v{NODE_VERSION}\nSource: {BASE_URL}/{name}\nsha256: {expected}\n',
        encoding='utf-8')
    print(f'Node runtime ready: {destination} ({destination.stat().st_size / 1_048_576:.0f} MiB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
