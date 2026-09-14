"""Tray lifecycle tests do not require a desktop or touch real startup settings."""
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler

import uvicorn
from vela.__main__ import BrowserServer
from vela.desktop import ServerController, WindowsMutex, startup_command


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('Timed out waiting for server state')


class DesktopTests(unittest.TestCase):
    def test_server_can_stop_and_restart_without_a_duplicate_thread(self):
        ready = threading.Event()
        runs = []

        class Server:
            should_exit = False

            def __init__(self, callback):
                self.callback = callback

            def run(self):
                runs.append(self)
                self.callback()
                ready.set()
                while not self.should_exit:
                    time.sleep(0.01)

        controller = ServerController(Server)
        try:
            controller.start()
            self.assertTrue(ready.wait(2))
            controller.start()
            self.assertEqual(len(runs), 1)
            controller.stop()
            self.assertEqual(controller.state, 'Stopped')
            self.assertFalse(controller.thread.is_alive())
            ready.clear()
            controller.start()
            self.assertTrue(ready.wait(2))
            self.assertEqual(len(runs), 2)
            self.assertEqual(controller.state, 'Running')
        finally:
            controller.stop()

    def test_failed_bind_stays_retryable_and_never_reports_running(self):
        class FailedServer:
            def run(self):
                raise SystemExit(1)
        controller = ServerController(lambda _: FailedServer())
        with self.assertLogs('vela.desktop', level='ERROR'):
            controller.start()
            controller.thread.join(timeout=2)
        self.assertEqual(controller.state, 'Failed')
        self.assertFalse(controller.thread.is_alive())

    def test_real_server_releases_its_port_and_restarts(self):
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200})
            await send({'type': 'http.response.body', 'body': b'ready'})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        config = uvicorn.Config(app, host='127.0.0.1', port=port, lifespan='off', log_config=None)
        controller = ServerController(lambda ready: BrowserServer(config, on_started=ready))
        opener = build_opener(ProxyHandler({}))
        try:
            for _ in range(2):
                controller.start()
                wait_for(lambda: controller.state == 'Running')
                with opener.open(f'http://127.0.0.1:{port}', timeout=2) as response:
                    self.assertEqual(response.read(), b'ready')
                controller.stop()
                self.assertEqual(controller.state, 'Stopped')
                with socket.socket() as connection:
                    self.assertNotEqual(connection.connect_ex(('127.0.0.1', port)), 0)
        finally:
            controller.stop()

    def test_sign_in_command_quotes_executable_and_keeps_launch_options(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', r'C:\Program Files\Vela\Vela.exe'), \
             patch.object(sys, 'argv', ['Vela.exe', '--port', '8800', '--open-browser']):
            self.assertEqual(startup_command(), '"C:\\Program Files\\Vela\\Vela.exe" --port 8800 --no-open-browser')

    def test_default_startup_command_matches_the_installer_without_spaces(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', r'C:\Vela\Vela.exe'), \
             patch.object(sys, 'argv', ['Vela.exe']):
            self.assertEqual(startup_command(), '"C:\\Vela\\Vela.exe" --no-open-browser')

    @unittest.skipUnless(os.name == 'nt', 'Windows named mutex')
    def test_duplicate_mutex_is_released_when_all_handles_close(self):
        name = 'Vela-Test-' + str(os.getpid())
        first = WindowsMutex(name)
        second = WindowsMutex(name)
        try:
            self.assertFalse(first.exists)
            self.assertTrue(second.exists)
        finally:
            second.close()
            first.close()
        third = WindowsMutex(name)
        try:
            self.assertFalse(third.exists)
        finally:
            third.close()
