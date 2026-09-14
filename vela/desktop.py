"""Windows tray controls. The server and all shutdown requests stay in this process."""
import ctypes
from ctypes import wintypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser

LOG = logging.getLogger(__name__)
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'Vela Server'
APP_MUTEX = 'VelaServerRunning'


class ServerController:
    """Own one server thread, including failed binds and stop/start transitions."""

    def __init__(self, factory, changed=lambda: None):
        self.factory = factory
        self.changed = changed
        self.state = 'Stopped'
        self.server = None
        self.thread = None
        self.lock = threading.RLock()

    def _ready(self):
        with self.lock:
            if self.state == 'Starting':
                self.state = 'Running'
        self.changed()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.state = 'Starting'
            self.server = self.factory(self._ready)
            self.thread = threading.Thread(target=self._run, name='vela-server', daemon=True)
            self.thread.start()
        self.changed()

    def _run(self):
        failed = False
        try:
            self.server.run()
        except (Exception, SystemExit):
            failed = True
            LOG.exception('Vela could not run. Check whether another server is using this port.')
        finally:
            with self.lock:
                self.state = 'Failed' if failed else 'Stopped'
            self.changed()

    def stop(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                return
            self.state = 'Stopping'
            self.server.should_exit = True
            thread = self.thread
        self.changed()
        # Keep the tray responsive and do not terminate an active database write.
        thread.join(timeout=15)
        if thread.is_alive():
            LOG.warning('Waiting for Vela to finish shutting down')


class WindowsMutex:
    """A named handle prevents duplicate tray servers and protects installer upgrades."""

    def __init__(self, name):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def startup_command():
    # Preserve port/TLS arguments; signing in never opens a browser.
    args = [arg for arg in sys.argv[1:] if arg not in ('--open-browser', '--no-open-browser')]
    for index, arg in enumerate(args):
        if arg in ('--cert', '--key') and index + 1 < len(args):
            args[index + 1] = str(Path(args[index + 1]).resolve())
        elif arg.startswith(('--cert=', '--key=')):
            name, value = arg.split('=', 1)
            args[index] = name + '=' + str(Path(value).resolve())
    prefix = [] if getattr(sys, 'frozen', False) else ['-m', 'vela']
    # Match Inno's quoted command even when the install path contains no spaces.
    return '"' + sys.executable + '" ' + subprocess.list2cmdline([*prefix, *args, '--no-open-browser'])


def starts_at_login():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value == startup_command()
    except FileNotFoundError:
        return False


def set_start_at_login(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def run_tray(factory, dashboard_url, config, host, port):
    import pystray
    from PIL import Image

    identity = f'{config.data_dir.resolve()}|{host}|{port}'.casefold().encode()
    instance = WindowsMutex('VelaServer-' + hashlib.sha256(identity).hexdigest()[:24])
    application = None
    try:
        if instance.exists:
            # Do not start a second engine against the same files and port.
            ctypes.windll.user32.MessageBoxW(None,
                'Vela is already open. Use the Vela icon beside the clock to open the dashboard or start the server.',
                'Vela Server', 0x40)
            return
        application = WindowsMutex(APP_MUTEX)
        log_path = config.logs_dir / 'server.log'
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        controller = ServerController(factory)
        icon = None

        def changed():
            if icon:
                icon.title = 'Vela Server — ' + controller.state
                icon.update_menu()

        controller.changed = changed

        def stop_server(_icon, _item):
            threading.Thread(target=controller.stop, daemon=True).start()

        def quit_server(_icon, _item):
            def finish():
                controller.stop()
                if controller.thread:
                    controller.thread.join()  # Never exit while shutdown is writing user data.
                icon.stop()
            threading.Thread(target=finish, daemon=True).start()

        def toggle_login(_icon, _item):
            try:
                set_start_at_login(not starts_at_login())
            except OSError:
                LOG.exception('Could not change start-at-sign-in preference')
                icon.notify('Could not change sign-in startup. See Open logs for details.', 'Vela Server')

        def setup(tray):
            tray.visible = True
            controller.start()

        artwork = Path(__file__).resolve().parent / 'assets' / 'vela.ico'
        # Source checkouts use the same existing logo as the packaging conversion.
        if not artwork.is_file():
            artwork = Path(__file__).resolve().parent.parent / 'docs/images/logo.png'
        with Image.open(artwork) as logo:
            item = pystray.MenuItem
            icon = pystray.Icon('Vela', logo.copy(), 'Vela Server — Starting', pystray.Menu(
                item(lambda _: 'Server: ' + controller.state, None, enabled=False),
                item('Open dashboard', lambda: webbrowser.open(dashboard_url), default=True,
                     enabled=lambda _: controller.state == 'Running'),
                item('Start server', lambda: controller.start(),
                     enabled=lambda _: controller.state in ('Stopped', 'Failed')),
                item('Stop server', stop_server, enabled=lambda _: controller.state == 'Running'),
                pystray.Menu.SEPARATOR,
                item('Start at sign in', toggle_login, checked=lambda _: starts_at_login(),
                     enabled=bool(getattr(sys, 'frozen', False)) and 'VELA_DATA_DIR' not in os.environ),
                item('Open logs', lambda: os.startfile(str(log_path))),
                pystray.Menu.SEPARATOR,
                item('Quit Vela', quit_server)))
            try:
                icon.run(setup)
            finally:
                controller.stop()
                if controller.thread:
                    controller.thread.join()
                logging.getLogger().removeHandler(handler)
                handler.close()
    finally:
        instance.close()
        if application:
            application.close()
