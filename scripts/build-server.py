"""Build a portable server for the current OS; the frontend must already be built."""
import hashlib
from importlib.metadata import distribution
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela import __version__
import importlib.util


def automation_assets():
    """The automation runtime, or an explanation of why it is not being shipped.

    A download without it still installs and runs; its automations page says the
    runtime is missing rather than failing a run halfway. Building a release
    always includes it.
    """
    options, notes = [], []
    worker = ROOT / 'scripts/automation-worker'
    runtime = ROOT / '.local/node-runtime'
    node = runtime / ('node.exe' if platform.system() == 'Windows' else 'node')
    if (worker / 'node_modules/@tramo/runtime').is_dir():
        options += ['--add-data', f'{worker}:scripts/automation-worker']
    else:
        notes.append('the workflow engine is not installed '
                     '(run python scripts/setup-automation-worker.py)')
    if node.is_file():
        options += ['--add-data', f'{runtime}:node-runtime']
    else:
        notes.append('the Node runtime has not been downloaded '
                     '(run python scripts/fetch-node-runtime.py)')
    return options, notes


def main():
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Build the dashboard first: npm --prefix web ci && npm --prefix web run build')
    automation_options, automation_notes = automation_assets()
    output = ROOT / '.local/server-build'
    output.mkdir(parents=True, exist_ok=True)
    windows_options = []
    if platform.system() == 'Windows':
        from PIL import Image
        # Convert the existing logo to Windows' multi-resolution icon format.
        icon = output / 'vela.ico'
        with Image.open(ROOT / 'docs/images/logo.png') as logo:
            logo.save(icon, format='ICO', sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
        version = output / 'version-info.txt'
        numbers = tuple(map(int, __version__.split('.'))) + (0,)
        version.write_text(
            f'VSVersionInfo(ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, '
            'mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), '
            "kids=[StringFileInfo([StringTable('040904B0', ["
            "StringStruct('CompanyName', 'Vela contributors'), "
            "StringStruct('FileDescription', 'Vela Server'), "
            f"StringStruct('FileVersion', '{__version__}'), "
            "StringStruct('InternalName', 'Vela'), StringStruct('OriginalFilename', 'Vela.exe'), "
            "StringStruct('ProductName', 'Vela Server'), "
            f"StringStruct('ProductVersion', '{__version__}')])]), "
            "VarFileInfo([VarStruct('Translation', [1033, 1200])])])", encoding='utf-8')
        windows_options = ['--icon', str(icon), '--version-file', str(version),
                           '--hide-console', 'hide-early', '--hidden-import', 'pystray._win32',
                           '--add-data', f'{icon}:vela/assets']
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'Vela',
        '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
        '--specpath', str(output), '--paths', str(ROOT),
        '--hidden-import', 'vela.api', '--collect-submodules', 'uvicorn',
        # Schedules name IANA timezones; Windows has no system database.
        *(['--collect-data', 'tzdata'] if importlib.util.find_spec('tzdata') else []),
        '--add-data', f'{ROOT / "web/dist"}:web/dist',
        '--add-data', f'{ROOT / "vela/assets"}:vela/assets',
        *automation_options,
        *windows_options,
        str(ROOT / 'scripts/server-entry.py'),
    ], cwd=ROOT, check=True)
    bundle = output / 'dist/Vela'
    shutil.copy2(ROOT / 'docs/SERVER.md', bundle / 'README.md')
    shutil.copy2(ROOT / 'LICENSE', bundle / 'LICENSE')
    if platform.system() == 'Windows':
        # Ship the tray library's source and notices with the frozen application.
        third_party = bundle / 'third-party'
        for package in ('pystray', 'Pillow'):
            metadata = distribution(package)
            for entry in metadata.files:
                if entry.name.startswith(('LICENSE', 'COPYING')):
                    destination = third_party / package / entry.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(metadata.locate_file(entry), destination)
        shutil.copytree(distribution('pystray').locate_file('pystray'), third_party / 'pystray/source',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (third_party / 'README.txt').write_text(
            'The Windows tray uses pystray 0.19.5 under LGPL-3.0 and Pillow under its included license.\n'
            'Unmodified pystray source and license texts are provided here.\n'
            'To replace or modify it, build Vela from source after installing your modified pystray:\n'
            'https://github.com/jhd3197/Vela/blob/main/docs/DEVELOPMENT.md\n'
            'Vela source remains MIT licensed.\n', encoding='utf-8')
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
    if automation_notes:
        print('This download cannot run automations: ' + '; '.join(automation_notes) + '.')
    else:
        print('Automations included: bundled Node runtime and workflow engine.')


if __name__ == '__main__':
    main()
