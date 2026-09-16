"""Verify branded EXEs, the real tray process, and an isolated installer lifecycle."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import winreg

import pefile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela import __version__


def check_branding(executable):
    with pefile.PE(str(executable)) as pe:
        groups = [entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries if entry.id == 14]
        assert groups, f'No Windows icon in {executable.name}'
        icon = (ROOT / '.local/server-build/vela.ico').read_bytes()
        count = struct.unpack_from('<H', icon, 4)[0]
        _, _, _, _, _, _, size, offset = struct.unpack_from('<BBBBHHII', icon, 6 + (count - 1) * 16)
        image_bytes = icon[offset:offset + size]
        embedded = []
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if entry.id == 3:  # RT_ICON
                for name in entry.directory.entries:
                    for language in name.directory.entries:
                        data = language.data.struct
                        embedded.append(pe.get_data(data.OffsetToData, data.Size))
        assert image_bytes in embedded, f'{executable.name} does not contain the Vela logo'
        info = {}
        for file_info in pe.FileInfo:
            for entry in file_info:
                for table in getattr(entry, 'StringTable', []):
                    info.update(table.entries)
        assert info[b'ProductName'].strip() == b'Vela Server', info
        assert info[b'ProductVersion'].decode().strip() == __version__, info


def stop_tray(process):
    """Send pystray's normal stop message only to windows owned by our test process."""
    api = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    api.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    api.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    found = []

    @callback_type
    def visit(window, _):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(window, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(256)
        api.GetClassNameW(window, name, len(name))
        if owner.value == process.pid and name.value.startswith('Vela') and name.value.endswith('SystemTrayIcon'):
            found.append(window)
        return True

    api.EnumWindows(visit, 0)
    assert found, 'Packaged app did not create its Windows tray controls'
    for window in found:
        api.PostMessageW(window, 0x0400 + 10, 0, 0)  # WM_STOP in pinned pystray 0.19.5
    process.wait(timeout=25)
    assert process.returncode == 0, f'Tray exited with {process.returncode}'


def tray_smoke(executable, data, work):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('VELA_', 'PYTHON', 'VIRTUAL_ENV'))}
    env['VELA_DATA_DIR'] = str(data)
    opener = build_opener(ProxyHandler({}))
    with (work / 'tray-output.log').open('w', encoding='utf-8') as output:
        process = subprocess.Popen([str(executable), '--no-open-browser', '--port', str(port)],
                                   cwd=work, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(200):
                assert process.poll() is None, 'Tray server exited before startup'
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                        assert json.load(response)['status'] == 'ok'
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.15)
            else:
                raise AssertionError('Tray server failed to start')
            stop_tray(process)
            assert (data / 'logs/server.log').is_file(), 'Missing server log'
            with socket.socket() as connection:
                assert connection.connect_ex(('127.0.0.1', port)) != 0, 'Tray left the server running'
        except Exception:
            output.flush()
            print((work / 'tray-output.log').read_text(encoding='utf-8'))
            log = data / 'logs/server.log'
            if log.is_file():
                print(log.read_text(encoding='utf-8'))
            raise
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def check_update_flags(installer):
    """The flags Vela's updater uses are the ones this check proves work.

    `main` below installs and upgrades with an explicit silent command line. If
    the apply script the updater writes ever drifts from it, an update would be
    running an installer invocation nothing has verified. Comparing them here
    keeps the two in step.
    """
    from vela.updates import windows_installer_script

    script = windows_installer_script(
        pid=1234, setup=installer, updates_dir=installer.parent,
        relaunch='"{}" --no-open-browser'.format(installer.parent / 'Vela.exe'))
    for flag in ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'):
        assert flag in script, f'The updater no longer passes {flag}'
    assert '/CLOSEAPPLICATIONS' in script, (
        'The updater must let the installer close a running Vela')
    assert str(installer) in script, 'The apply script does not run the setup it downloaded'
    # It waits for this process to exit before touching the files it is using.
    assert 'PID eq 1234' in script and 'goto wait' in script
    print('PASS: the updater installs with the same silent flags this check verifies')


def main():
    # The test installer has a separate AppId and never creates real shortcuts/startup entries.
    assert sys.platform == 'win32', 'Run this verification on Windows'
    portable = ROOT / '.local/server-build/dist/Vela/Vela.exe'
    installer = ROOT / f'.local/releases/vela-server-{__version__}-windows-x64-setup.exe'
    check_branding(portable)
    check_branding(installer)
    check_update_flags(installer)
    subprocess.run([sys.executable, str(ROOT / 'scripts/build-windows-installer.py'), '--test-build'], check=True)
    test_installer = ROOT / f'.local/installer-test/vela-server-{__version__}-windows-x64-setup.exe'
    uninstall_key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Vela.Server.SmokeTest_is1'
    startup_key = r'Software\Microsoft\Windows\CurrentVersion\Run'
    startup_value = 'Vela Server SmokeTest'
    with tempfile.TemporaryDirectory(prefix='vela-installer-smoke-') as temporary:
        work = Path(temporary)
        destination = (work / 'installed').resolve()
        assert destination.is_relative_to(work.resolve())
        data = work / 'data'
        data.mkdir()
        sentinel = data / 'keep-my-data.txt'
        sentinel.write_text('user data must survive updates and uninstall', encoding='utf-8')
        tray_smoke(portable, data, work)
        try:
            command = [str(test_installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                       '/NOICONS', '/TASKS=', f'/DIR={destination}', f'/LOG={work / "install.log"}']
            # Install and upgrade using the same identity/directory; neither launches the server.
            for iteration in range(2):
                subprocess.run(command, check=True, timeout=90)
                assert (destination / 'Vela.exe').is_file()
                assert (destination / '_internal/web/dist/index.html').is_file()
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
                    assert winreg.QueryValueEx(key, 'DisplayName')[0] == 'Vela Server'
                # Model enabling startup from the tray after an opt-out installation.
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, startup_key) as key:
                    if iteration == 0:
                        try:
                            winreg.QueryValueEx(key, startup_value)
                            raise AssertionError('Installer enabled startup without opting in')
                        except FileNotFoundError:
                            pass
                        winreg.SetValueEx(key, startup_value, 0, winreg.REG_SZ,
                                          f'"{destination / "Vela.exe"}" --no-open-browser')
                    else:
                        assert winreg.QueryValueEx(key, startup_value)[0].endswith('--no-open-browser')
            tray_smoke(destination / 'Vela.exe', data, work)
        finally:
            uninstaller = destination / 'unins000.exe'
            if uninstaller.is_file():
                subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                f'/LOG={work / "uninstall.log"}'], check=True, timeout=90)
                for _ in range(100):
                    if not uninstaller.exists():
                        break
                    time.sleep(0.1)
                # Inno's temporary cleanup helper can outlive its original process.
                # Wait for it to release the test log before TemporaryDirectory cleanup.
                log = work / 'uninstall.log'
                for _ in range(300):
                    try:
                        log.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        time.sleep(0.1)
                else:
                    raise AssertionError('Uninstaller cleanup helper did not finish')
        assert not (destination / 'Vela.exe').exists(), 'Uninstall left the program installed'
        assert sentinel.read_text(encoding='utf-8') == 'user data must survive updates and uninstall'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key) as key:
            try:
                winreg.QueryValueEx(key, startup_value)
                raise AssertionError('Uninstall left the sign-in startup entry')
            except FileNotFoundError:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key):
                raise AssertionError('Uninstall registration was not removed')
        except FileNotFoundError:
            pass
    print('PASS: branded executables, portable and installed tray startup/shutdown, install/upgrade/uninstall, data preservation, updater flag parity')


if __name__ == '__main__':
    main()
