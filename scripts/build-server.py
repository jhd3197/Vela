"""Build a portable server for the current OS; the frontend must already be built."""
import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela import __version__


def main():
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Build the dashboard first: npm --prefix web ci && npm --prefix web run build')
    output = ROOT / '.local/server-build'
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'Vela',
        '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
        '--specpath', str(output), '--paths', str(ROOT),
        '--hidden-import', 'vela.api', '--collect-submodules', 'uvicorn',
        '--add-data', f'{ROOT / "web/dist"}:web/dist',
        '--add-data', f'{ROOT / "vela/assets"}:vela/assets',
        str(ROOT / 'scripts/server-entry.py'),
    ], cwd=ROOT, check=True)
    bundle = output / 'dist/Vela'
    shutil.copy2(ROOT / 'docs/SERVER.md', bundle / 'README.md')
    shutil.copy2(ROOT / 'LICENSE', bundle / 'LICENSE')
    system = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}[platform.system()]
    machine = platform.machine().lower()
    architecture = {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64'}.get(machine, machine)
    artifacts = ROOT / '.local/releases'
    artifacts.mkdir(parents=True, exist_ok=True)
    name = artifacts / f'vela-server-{__version__}-{system}-{architecture}'
    archive = Path(shutil.make_archive(str(name), 'zip' if system == 'windows' else 'gztar',
                                      root_dir=bundle.parent, base_dir=bundle.name))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'\nServer bundle: {bundle}\nRelease archive: {archive}')


if __name__ == '__main__':
    main()
