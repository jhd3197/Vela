"""The desktop launcher must never open a dashboard before successful startup."""
import unittest
from unittest.mock import AsyncMock, patch
import uvicorn
from vela.__main__ import BrowserServer


class LauncherTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_opens_only_after_successful_startup(self):
        server = BrowserServer(uvicorn.Config('vela.api:app'), 'http://127.0.0.1:7700')
        async def start(*args, **kwargs):
            server.started = True
        with patch.object(uvicorn.Server, 'startup', side_effect=start), patch('vela.__main__.webbrowser.open') as browser:
            await server.startup()
            browser.assert_called_once_with('http://127.0.0.1:7700')

    async def test_failed_or_headless_startup_does_not_open_browser(self):
        for url, started in [('http://127.0.0.1:7700', False), (None, True)]:
            server = BrowserServer(uvicorn.Config('vela.api:app'), url)
            server.started = started
            with patch.object(uvicorn.Server, 'startup', new=AsyncMock()), patch('vela.__main__.webbrowser.open') as browser:
                await server.startup()
                browser.assert_not_called()
