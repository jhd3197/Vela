"""Host-owned links to existing web services. Never fetch or manage upstreams.

These records are not app packages and cannot acquire SDK sessions or grants.
The double-hyphen ID namespace cannot collide with a valid package manifest.
"""
import ipaddress
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

from .app_storage import AppServiceError


def web_address(value, hub_origin=None):
    """Accept unambiguous HTTPS addresses, with no embedded credentials."""
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError()
        if any(ord(char) < 33 or ord(char) > 126 for char in value) or '\\' in value:
            raise ValueError()
        url = urlsplit(value)
        host = url.hostname
        if url.scheme != 'https' or not host or url.username is not None or url.password is not None:
            raise ValueError()
        port = url.port or 443
        if url.port == 0 or not 1 <= port <= 65535 or url.netloc.endswith(':'):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
            if address.is_unspecified or address.is_multicast:
                raise ValueError()
            host = f'[{address}]' if address.version == 6 else str(address)
        except ValueError:
            # Reject alternate numeric IP spellings interpreted differently by browsers.
            if ':' in host or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
                raise ValueError()
            labels = host.split('.')
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in labels):
                raise ValueError()
            if len(host) > 253 or re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', labels[-1]):
                raise ValueError()
        origin = f'https://{host}' + (f':{port}' if port != 443 else '')
        if hub_origin and urlsplit(origin).hostname == urlsplit(hub_origin).hostname:
            raise AppServiceError(422, 'Use a different hostname from Vela. Cookies are shared across ports on the same hostname.')
        return urlunsplit(('https', origin[8:], url.path or '/', url.query, url.fragment))
    except (ValueError, TypeError):
        raise AppServiceError(422, 'Enter an HTTPS address without credentials, spaces or an ambiguous hostname.')


class ConnectedApps:
    def __init__(self, storage):
        self.storage = storage
        with storage.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS connected_web_apps (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                color TEXT NOT NULL, revision INTEGER NOT NULL
            )''')

    @staticmethod
    def owns(app_id):
        return app_id.startswith('web--')

    @staticmethod
    def summary(row):
        return {
            **dict(row), 'kind': 'connected-web', 'schemaVersion': None,
            'description': 'An existing web service. Its account and data stay with that service.',
            'category': 'connected', 'author': '', 'version': None,
            'installed': True, 'running': False, 'supported': True,
            'runtime': 'connected', 'runtimes': ['connected'], 'isolation': 'cross-origin',
            'view': {'surface': 'connected', 'chrome': 'compact', 'url': row['url']},
            'capabilities': [], 'unavailableCapabilities': [],
        }

    def list_apps(self):
        with self.storage.connection() as db:
            return [self.summary(row) for row in db.execute('SELECT * FROM connected_web_apps ORDER BY name, id')]

    def get(self, app_id):
        with self.storage.connection() as db:
            row = db.execute('SELECT * FROM connected_web_apps WHERE id=?', (app_id,)).fetchone()
        if not row:
            raise AppServiceError(404, 'Connected web app not found.')
        return self.summary(row)

    def save(self, name, url, color, hub_origin, *, app_id=None, revision=None):
        name = name.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise AppServiceError(422, 'Enter an app name between 1 and 80 characters.')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise AppServiceError(422, 'Choose a six-digit icon color.')
        url = web_address(url.strip(), hub_origin)
        with self.storage.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if app_id:
                row = db.execute('SELECT revision FROM connected_web_apps WHERE id=?', (app_id,)).fetchone()
                if not row:
                    raise AppServiceError(404, 'Connected web app not found.')
                if row['revision'] != revision:
                    raise AppServiceError(409, 'This connection changed. Close and reopen its settings before saving.')
                db.execute('UPDATE connected_web_apps SET name=?, url=?, color=?, revision=revision+1 WHERE id=?', (name, url, color, app_id))
            else:
                if db.execute('SELECT count(*) FROM connected_web_apps').fetchone()[0] >= 200:
                    raise AppServiceError(409, 'The limit is 200 connected web apps. Remove one before adding another.')
                app_id = 'web--' + uuid.uuid4().hex
                db.execute('INSERT INTO connected_web_apps VALUES (?, ?, ?, ?, 1)', (app_id, name, url, color))
        return self.get(app_id)

    def remove(self, app_id, revision):
        with self.storage.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM connected_web_apps WHERE id=?', (app_id,)).fetchone()
            if not row:
                raise AppServiceError(404, 'Connected web app not found.')
            if row['revision'] != revision:
                raise AppServiceError(409, 'This connection changed. Close and reopen its settings before removing it.')
            db.execute('DELETE FROM connected_web_apps WHERE id=?', (app_id,))
        return {'removed': True}
