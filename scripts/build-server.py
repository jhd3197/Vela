"""Build a portable server for the current OS; the frontend must already be built."""
import hashlib
import json
from importlib.metadata import distribution
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela import __version__
from vela.router_registry import ROUTERS
from vela.updates import platform_architecture
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


def browser_assets():
    """The agent desktop runtime, or an explanation of why it is not shipping.

    The worker and `playwright-core` go into the download; the Chromium build
    does not. That is a deliberate split rather than an oversight: the browser is
    a few hundred megabytes and lives in a per-user cache the runtime manages, so
    shipping a copy inside every Vela download would multiply the size of the
    download for something most people already have one of.

    What the download does carry is the pinned version and the revision it needs,
    so a Vela without the browser says exactly which one to fetch and never
    downloads "whatever Chromium is current" during somebody's task.
    """
    options, notes = [], []
    worker = ROOT / 'scripts/browser-worker'
    if not (worker / 'node_modules/playwright-core').is_dir():
        notes.append('the browser engine package is not installed '
                     '(run python scripts/setup-browser-worker.py)')
        return options, notes
    if not (worker / 'provenance.json').is_file():
        notes.append('the runtime has no provenance record '
                     '(run python scripts/setup-browser-worker.py)')
        return options, notes
    options += ['--add-data', f'{worker}:scripts/browser-worker']
    return options, notes


def router_imports():
    """Name every router module `create_app` loads by string at runtime.

    `vela/router_registry.py` holds the mount order as data and imports each
    module with `import_module`, which PyInstaller's static analysis cannot
    follow. Reading the same list here keeps the bundle complete when a router
    is added or renamed, instead of failing at startup with ModuleNotFoundError.
    """
    options = []
    for module in sorted({spec.module for spec in ROUTERS}):
        options += ['--hidden-import', module]
    return options


def browser_notices(bundle):
    """Licence texts for what the agent desktop runtime brings with it."""
    source = ROOT / 'scripts/browser-worker/node_modules/playwright-core'
    if not source.is_dir():
        return
    destination = bundle / 'third-party/playwright-core'
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('LICENSE', 'NOTICE', 'ThirdPartyNotices.txt'):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    version = json.loads((source / 'package.json').read_text(encoding='utf-8'))['version']
    (destination / 'README.txt').write_text(
        f'Agent desktops render in a Chromium that playwright-core {version} starts and stops.\n'
        'playwright-core is Apache-2.0; its licence and notices are beside this file.\n'
        'The browser build itself is not in this download. Vela records which revision it\n'
        'needs and says so rather than downloading one during a task.\n'
        'Vela source remains MIT licensed.\n', encoding='utf-8')


def main():
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Build the dashboard first: npm --prefix web ci && npm --prefix web run build')
    automation_options, automation_notes = automation_assets()
    browser_options, browser_notes = browser_assets()
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
        # `create_app` imports its routers by name from the registry, so
        # PyInstaller cannot follow them; name them from the same list.
        *router_imports(),
        # psutil is imported lazily by vela/system_metrics.py, so PyInstaller
        # cannot see it by following imports.
        '--hidden-import', 'psutil',
        # Schedules name IANA timezones; Windows has no system database.
        *(['--collect-data', 'tzdata'] if importlib.util.find_spec('tzdata') else []),
        '--add-data', f'{ROOT / "web/dist"}:web/dist',
        '--add-data', f'{ROOT / "vela/assets"}:vela/assets',
        *automation_options,
        *browser_options,
        *windows_options,
        str(ROOT / 'scripts/server-entry.py'),
    ], cwd=ROOT, check=True)
    bundle = output / 'dist/Vela'
    shutil.copy2(ROOT / 'docs/SERVER.md', bundle / 'README.md')
    shutil.copy2(ROOT / 'LICENSE', bundle / 'LICENSE')
    browser_notices(bundle)
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
    architecture = platform_architecture()
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
    if browser_notes:
        print('This download cannot run agent desktops: ' + '; '.join(browser_notes) + '.')
    else:
        record = json.loads((ROOT / 'scripts/browser-worker/provenance.json').read_text('utf-8'))
        print('Agent desktops included: playwright-core '
              f'{(record.get("installed") or {}).get("playwright-core")}. '
              'The browser build is fetched on the computer that runs Vela.')


if __name__ == '__main__':
    main()
