"""Package the tested Windows bundle with Inno Setup 6 (ISCC_PATH may override it)."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela import __version__


def compiler():
    candidates = [os.environ.get('ISCC_PATH'), shutil.which('ISCC.exe'),
                  str(ROOT / '.local/tools/innosetup/ISCC.exe'),
                  r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                  r'C:\Program Files\Inno Setup 6\ISCC.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit('Install Inno Setup 6 or set ISCC_PATH to ISCC.exe. See docs/DEVELOPMENT.md.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-build', action='store_true', help='Isolated installer identity for smoke tests')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build the x64 installer on Windows x64')
    bundle = ROOT / '.local/server-build/dist/Vela'
    if not (bundle / 'Vela.exe').is_file():
        raise SystemExit('Build and smoke-test the portable server first')
    output = ROOT / ('.local/installer-test' if args.test_build else '.local/releases')
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([compiler(), '/Qp', f'/DAppVersion={__version__}', f'/DSourceRoot={ROOT}',
                    f'/DBundleDirectory={bundle}', f'/DOutputDirectory={output}',
                    f'/DIconFile={ROOT / ".local/server-build/vela.ico"}',
                    *(['/DAppIdentity=Vela.Server.SmokeTest', '/DStartupValue=Vela Server SmokeTest'] if args.test_build else []),
                    str(ROOT / 'scripts/windows/installer.iss')], check=True)
    installer = output / f'vela-server-{__version__}-windows-x64-setup.exe'
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    installer.with_name(installer.name + '.sha256').write_text(
        f'{digest}  {installer.name}\n', encoding='utf-8')
    print(f'Windows installer: {installer}')


if __name__ == '__main__':
    main()
