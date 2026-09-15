"""Quick unlock: enrollment, enforcement, throttling and recovery.

Everything here uses a disposable data directory and a disposable password.
Enrollment lives in the engine's memory, so nothing in these tests can reach a
real installation.
"""
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import test_app_contract as base
from vela.access import (
    build_verifier,
    normalize_pattern,
    normalize_secret,
    set_password,
    verify_secret,
)
from vela.api import create_app
from vela.config import Config

PASSWORD = 'fixture-password'
ORIGIN = 'https://192.168.1.20:7702'


class PatternRuleTests(unittest.TestCase):
    """The rules the client mirrors in `web/src/pattern.js`."""

    def test_midpoints_are_inserted_once_and_only_when_unvisited(self):
        self.assertEqual(normalize_pattern([0, 2, 6, 8]), [0, 1, 2, 4, 6, 7, 8])
        self.assertEqual(normalize_pattern([0, 1, 2, 5, 8]), [0, 1, 2, 5, 8])
        # 4 is already used, so 3 -> 5 is allowed to jump over it.
        self.assertEqual(normalize_pattern([1, 4, 3, 5]), [1, 4, 3, 5])
        # A knight's move has no midpoint to insert.
        self.assertEqual(normalize_pattern([0, 5, 6, 1]), [0, 5, 6, 1])

    def test_short_repeated_and_out_of_range_patterns_are_refused(self):
        for bad in ([0, 2], [6, 2], [0, 1, 2, 1], [0, 1, 2, 9], [0], [], 'abc',
                    [0, 1, 2, True], list(range(10))):
            self.assertIsNone(normalize_pattern(bad), bad)

    def test_pin_keeps_leading_zeros_and_refuses_other_shapes(self):
        self.assertEqual(normalize_secret('pin', '000123'), b'pin:000123')
        self.assertNotEqual(normalize_secret('pin', '000123'), normalize_secret('pin', '123000'))
        for bad in ('12345', '1234567', '12345a', '', '１２３４５６', 123456):
            self.assertIsNone(normalize_secret('pin', bad), bad)
        self.assertIsNone(normalize_secret('face', '123456'))

    def test_verifier_is_salted_and_matches_only_the_enrolled_secret(self):
        salt, digest = build_verifier('pattern', [0, 1, 2, 5])
        again = build_verifier('pattern', [0, 1, 2, 5])
        self.assertNotEqual(digest, again[1])
        self.assertTrue(verify_secret('pattern', [0, 1, 2, 5], salt, digest))
        # The same drawn shape normalizes to the same dots and still matches.
        self.assertTrue(verify_secret('pattern', [0, 2, 5], salt, digest))
        self.assertFalse(verify_secret('pattern', [0, 1, 2, 5, 8], salt, digest))
        self.assertFalse(verify_secret('pin', '012345', salt, digest))


class QuickUnlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vela-security-')
        root = Path(self.temp.name)
        self.config = Config(root, root / 'apps', base.ROOT / 'web/dist')
        self.config.ensure_dirs()
        set_password(self.config.data_dir / 'access.json', PASSWORD)
        self.app = create_app(self.config)
        self.auth = self.app.state.phone_access.auth
        self.auth.phone_origin = ORIGIN
        self.local = TestClient(self.app)
        self.phone = TestClient(self.app, base_url=ORIGIN, client=('192.168.1.30', 1234))
        self.token = self.phone.post(
            '/api/login', headers={'X-Vela-Bootstrap': '1'}, json={'password': PASSWORD},
        ).json()['token']
        self.phone.cookies.set('__Host-vela-session', self.token)
        self.headers = {'Authorization': f'Bearer {self.token}'}

    def tearDown(self):
        self.local.close()
        self.phone.close()
        self.temp.cleanup()

    def enroll(self, method='pin', secret='013759', password=PASSWORD):
        return self.phone.post('/api/security/enroll', headers=self.headers,
                               json={'password': password, 'method': method, 'secret': secret})

    def status(self):
        return self.phone.get('/api/security', headers=self.headers).json()

    # ------------------------------------------------------------ boundaries

    def test_the_computers_bootstrap_session_is_left_alone(self):
        local = {'Authorization': 'Bearer ' + self.auth.hub_token}
        self.assertEqual(self.local.get('/api/security', headers=local).json()['available'], False)
        refused = self.local.post('/api/security/enroll', headers=local,
                                  json={'password': PASSWORD, 'method': 'pin', 'secret': '013759'})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.auth.quick, {})

    def test_enrollment_needs_the_vela_password_and_an_acceptable_secret(self):
        self.assertEqual(self.enroll(password='wrong-password-x').status_code, 401)
        self.assertEqual(self.enroll(secret='12345').status_code, 422)
        self.assertEqual(self.enroll(method='pattern', secret=[0, 2]).status_code, 422)
        self.assertFalse(self.status()['enrolled'])
        body = self.enroll().json()
        self.assertEqual((body['enrolled'], body['method'], body['timeout'], body['locked']),
                         (True, 'pin', 300, False))

    def test_the_secret_is_never_stored_on_disk(self):
        self.enroll(method='pattern', secret=[0, 1, 2, 5, 8])
        for path in self.config.data_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'0-1-2-5-8', path.read_bytes(), path)
                self.assertNotIn(PASSWORD.encode(), path.read_bytes(), path)

    # ------------------------------------------------------------ enforcement

    def test_locking_stops_protected_routes_app_tokens_and_streams(self):
        self.enroll()
        self.auth.sessions['fixture-app'] = {
            'app_id': 'fixture', 'owner': self.token, 'capabilities': [],
            'expires': time.monotonic() + 60, 'installationId': 'fixture', 'schemaVersion': 1,
            'quota': 1024,
        }
        app_headers = {'Authorization': 'Bearer fixture-app'}
        self.assertEqual(self.phone.get('/api/apps', headers=self.headers).status_code, 200)

        self.assertTrue(self.phone.post('/api/security/lock', headers=self.headers).json()['locked'])
        self.assertEqual(self.phone.get('/api/apps', headers=self.headers).status_code, 423)
        self.assertEqual(self.phone.post('/api/chat', headers=self.headers,
                                         json={'messages': []}).status_code, 423)
        # An app token issued before the lock stops with it; its stream ends.
        self.assertEqual(self.phone.get('/api/app/storage', headers=app_headers).status_code, 401)
        # Reloading the page, another tab and polling all see the same lock.
        self.assertTrue(self.status()['locked'])
        self.assertEqual(self.phone.get('/api/session',
                                        headers={'X-Vela-Bootstrap': '1'}).status_code, 200)
        self.assertEqual(self.phone.get('/api/health').status_code, 200)

    def test_inactivity_locks_and_only_real_activity_defers_it(self):
        self.enroll()
        self.phone.patch('/api/security', headers=self.headers,
                         json={'password': PASSWORD, 'timeout': 60})
        self.auth.quick[self.token]['activity'] = time.monotonic() - 30
        # Polling must not count as activity, so the deadline does not move.
        self.phone.get('/api/apps', headers=self.headers)
        self.assertLess(self.auth.quick[self.token]['activity'], time.monotonic() - 20)
        # A deliberate action does.
        self.phone.get('/api/apps', headers={**self.headers, 'X-Vela-Activity': '1'})
        self.assertGreater(self.auth.quick[self.token]['activity'], time.monotonic() - 5)

        self.auth.quick[self.token]['activity'] = time.monotonic() - 61
        self.assertTrue(self.status()['locked'])
        self.assertEqual(self.phone.get('/api/apps', headers=self.headers).status_code, 423)
        # Returning from the background cannot reset the timer by itself.
        self.phone.post('/api/security/activity', headers=self.headers)
        self.assertTrue(self.status()['locked'])

    def test_a_bad_timeout_is_refused_and_needs_the_password(self):
        self.enroll()
        self.assertEqual(self.phone.patch('/api/security', headers=self.headers,
                                          json={'password': PASSWORD, 'timeout': 7}).status_code, 422)
        self.assertEqual(self.phone.patch('/api/security', headers=self.headers,
                                          json={'password': 'nope-nope-nope', 'timeout': 60}).status_code, 401)
        self.assertEqual(self.status()['timeout'], 300)

    # -------------------------------------------------------- unlock, recovery

    def test_unlock_throttles_by_session_then_falls_back_to_the_password(self):
        self.enroll()
        self.phone.post('/api/security/lock', headers=self.headers)
        for remaining in (4, 3, 2, 1, 0):
            response = self.phone.post('/api/security/unlock', headers=self.headers,
                                       json={'secret': '999999'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(self.status()['attemptsRemaining'], remaining)
        # A reload, a second tab or a new address cannot reset the counter: it
        # belongs to the enrolled session, not to a cookie or an IP.
        other = TestClient(self.app, base_url=ORIGIN, client=('192.168.1.99', 5555))
        other.cookies.set('__Host-vela-session', self.token)
        self.assertTrue(other.get('/api/security', headers=self.headers).json()['passwordRequired'])
        # The correct PIN no longer helps; the Vela password still does.
        self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                         json={'secret': '013759'}).status_code, 403)
        body = self.phone.post('/api/security/unlock', headers=self.headers,
                               json={'password': PASSWORD}).json()
        self.assertEqual((body['locked'], body['passwordRequired'], body['attemptsRemaining']),
                         (False, False, 5))
        self.assertEqual(self.phone.get('/api/apps', headers=self.headers).status_code, 200)
        other.close()

    def test_the_password_recovery_path_is_throttled_too(self):
        self.enroll()
        self.phone.post('/api/security/lock', headers=self.headers)
        for _ in range(4):
            self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                             json={'password': 'not-the-password'}).status_code, 401)
        fifth = self.phone.post('/api/security/unlock', headers=self.headers,
                                json={'password': 'not-the-password'})
        self.assertEqual(fifth.status_code, 401)
        # The next attempt waits, and waiting is not reset by a new request.
        held = self.phone.post('/api/security/unlock', headers=self.headers,
                               json={'password': PASSWORD})
        self.assertEqual(held.status_code, 429)
        self.assertTrue(self.status()['locked'])
        # Clearing the cooldown the way time would lets the real password work.
        self.auth.quick[self.token]['cooldown'] = 0
        self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                         json={'password': PASSWORD}).status_code, 200)

    def test_unlock_needs_exactly_one_credential(self):
        self.enroll()
        for body in ({}, {'secret': '013759', 'password': PASSWORD}):
            self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                             json=body).status_code, 422)

    def test_a_pattern_unlocks_by_its_normalized_dots(self):
        self.enroll(method='pattern', secret=[0, 1, 2, 5, 8])
        self.phone.post('/api/security/lock', headers=self.headers)
        self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                         json={'secret': [0, 2, 5, 8]}).status_code, 200)
        self.assertFalse(self.status()['locked'])

    def test_disabling_and_changing_need_a_fresh_password(self):
        self.enroll()
        refused = self.phone.request('DELETE', '/api/security', headers=self.headers,
                                     json={'password': 'not-the-password'})
        self.assertEqual(refused.status_code, 401)
        self.assertTrue(self.status()['enrolled'])
        self.assertEqual(self.enroll(secret='224466').status_code, 200)
        self.phone.post('/api/security/lock', headers=self.headers)
        self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                         json={'secret': '013759'}).status_code, 401)
        self.assertEqual(self.phone.post('/api/security/unlock', headers=self.headers,
                                         json={'secret': '224466'}).status_code, 200)
        self.assertEqual(self.phone.request('DELETE', '/api/security', headers=self.headers,
                                            json={'password': PASSWORD}).status_code, 200)
        self.assertFalse(self.status()['enrolled'])

    def test_signing_out_and_losing_enrollment_return_to_password_sign_in(self):
        self.enroll()
        self.phone.post('/api/logout', headers={'X-Vela-Bootstrap': '1'})
        self.assertEqual(self.auth.quick, {})
        self.assertEqual(self.phone.get('/api/security', headers=self.headers).status_code, 401)
        # A restart is the same thing: nothing was written down to restore.
        self.assertFalse(any('quick' in path.name for path in self.config.data_dir.rglob('*')))
