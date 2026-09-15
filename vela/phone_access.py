"""Opt-in Wi-Fi listeners sharing the running engine, with a public trust guide.

The HTTP listener serves only installation instructions and a public certificate.
Apps and credentials stay on the HTTPS listener and use the engine's auth layer.
"""
import asyncio
import ipaddress
import json
import logging
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from .access import set_password
from .app_storage import AppServiceError

LOG = logging.getLogger(__name__)
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class PhoneServer(uvicorn.Server):
    # The main Vela listener owns process signals and shutdown.
    @contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self):
        pass

    async def serve(self, sockets=None):
        try:
            await super().serve(sockets=sockets)
        except SystemExit as exc:
            raise RuntimeError('The Wi-Fi listener could not start') from exc


def private_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
        return any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


def local_addresses():
    """Only report addresses assigned to this host, not guessed subnet peers."""
    addresses = set()
    try:
        addresses.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    preferred = None
    try:
        # UDP connect selects an interface locally; it sends no packet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            preferred = probe.getsockname()[0]
            addresses.add(preferred)
    except OSError:
        pass
    return sorted((ip for ip in addresses if private_ipv4(ip)), key=lambda ip: (ip != preferred, ip))


def certificates(directory, address):
    directory.mkdir(parents=True, exist_ok=True)
    ca_path, ca_key_path = directory / 'ca.pem', directory / 'ca.key'
    now = datetime.now(timezone.utc)
    if ca_path.exists() and ca_key_path.exists():
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), None)
        if ca.not_valid_after_utc <= now + timedelta(days=2):
            raise AppServiceError(409, 'The Wi-Fi certificate has expired. Remove the old Vela phone certificates and reset Wi-Fi access.')
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Vela Wi-Fi ' + socket.gethostname()[:48])])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1825))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
              # This CA cannot certify public websites. DNS certificates are
              # restricted to the reserved .invalid namespace.
              .add_extension(x509.NameConstraints(
                  permitted_subtrees=[*(x509.IPAddress(net) for net in PRIVATE_NETWORKS), x509.DNSName('.invalid')],
                  excluded_subtrees=None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        ca_key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ca_key_path.chmod(0o600)
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Vela Wi-Fi')]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=365), ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .sign(ca_key, hashes.SHA256()))
    cert_path, key_path = directory / 'server.pem', directory / 'server.key'
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_path.chmod(0o600)
    (directory / 'vela-phone.cer').write_bytes(ca.public_bytes(serialization.Encoding.DER))
    return ca.fingerprint(hashes.SHA256()).hex().upper()


class PhoneAccess:
    def __init__(self, app, config, auth):
        self.app, self.config, self.auth = app, config, auth
        self.directory = config.data_dir / 'phone-access'
        self.servers, self.tasks, self.sockets = [], [], []
        self.address = self.origin = self.setup_url = self.fingerprint = None
        self.error = None
        self.lock = asyncio.Lock()

    def status(self):
        if self.config.remote_access:
            return {'enabled': True, 'managed': False, 'setup_url': self.config.public_origin + '/setup'}
        return {'enabled': bool(self.origin), 'managed': True, 'setup_url': self.setup_url,
                'secure_url': self.origin + '/setup' if self.origin else None,
                'addresses': local_addresses(), 'address': self.address,
                'needs_password': not self.auth.password_file.is_file(),
                'fingerprint': self.fingerprint, 'error': self.error}

    def public_app(self, address, origin, fingerprint, setup_port):
        public = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        root = self.config.web_dist.resolve()

        @public.middleware('http')
        async def boundary(request, call_next):
            if (request.headers.get('host') != f'{address}:{setup_port}' or
                    not private_ipv4(request.client.host)):
                return JSONResponse({'detail': 'Use the Wi-Fi setup address'}, status_code=403)
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
            return response

        @public.get('/phone-bootstrap')
        def bootstrap():
            return {'secure_url': origin + '/setup', 'certificate_url': '/vela-phone.cer', 'fingerprint': fingerprint}

        @public.get('/vela-phone.cer')
        def certificate():
            return FileResponse(self.directory / 'vela-phone.cer', media_type='application/x-x509-ca-cert', filename='vela-phone.cer')

        @public.get('/{asset:path}')
        def static(asset):
            if asset in ('', 'setup'):
                return FileResponse(root / 'index.html')
            candidate = (root / asset).resolve()
            if (asset == 'vela-mark.png' or asset.startswith(('assets/', 'icons/'))) and candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            return JSONResponse({'detail': 'Open the secure Vela address to use your apps'}, status_code=404)

        return public

    async def start(self, address, password='', *, ports=None):
        async with self.lock:
            if self.config.remote_access:
                raise AppServiceError(409, 'This server already has a configured HTTPS address')
            if self.origin:
                return self.status()
            if address not in local_addresses():
                raise AppServiceError(422, 'Choose a current Wi-Fi address for this computer')
            if not self.auth.password_file.is_file() and not 12 <= len(password) <= 256:
                raise AppServiceError(422, 'Choose a Vela password between 12 and 256 characters')
            if not (self.config.web_dist / 'index.html').is_file():
                raise AppServiceError(409, 'The dashboard build is required for phone access')
            self.error = None
            try:
                # Bind before changing auth or persisting enabled state. Stable
                # ports survive restarts; never silently change installed URLs.
                for port in ports or (7701, 7702):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.sockets.append(sock)
                    sock.bind((address, port))
                    sock.listen(128)
                    sock.setblocking(False)
                setup_port, secure_port = [sock.getsockname()[1] for sock in self.sockets]
                fingerprint = await asyncio.to_thread(certificates, self.directory, address)
                if not self.auth.password_file.is_file():
                    await asyncio.to_thread(set_password, self.auth.password_file, password)
                    # A new access password invalidates any quick-unlock
                    # enrollment made under the previous one.
                    self.auth.reset_quick_unlock()
                origin = f'https://{address}:{secure_port}'
                public = self.public_app(address, origin, fingerprint, setup_port)

                async def secured(scope, receive, send):
                    if scope['type'] == 'http':
                        headers = dict(scope.get('headers', []))
                        if (scope.get('scheme') != 'https' or
                                headers.get(b'host') != f'{address}:{secure_port}'.encode() or
                                not private_ipv4(scope.get('client', ('',))[0])):
                            await JSONResponse({'detail': 'Use the secure Wi-Fi address'}, status_code=403)(scope, receive, send)
                            return
                    await self.app(scope, receive, send)

                self.auth.phone_origin = origin
                for index, asgi in enumerate((public, secured)):
                    tls = {'ssl_certfile': str(self.directory / 'server.pem'), 'ssl_keyfile': str(self.directory / 'server.key')} if index else {}
                    server = PhoneServer(uvicorn.Config(asgi, lifespan='off', proxy_headers=False,
                        access_log=False, log_config=None, timeout_graceful_shutdown=3, **tls))
                    self.servers.append(server)
                    self.tasks.append(asyncio.create_task(server.serve(sockets=[self.sockets[index]])))
                for _ in range(100):
                    if all(server.started for server in self.servers): break
                    if any(task.done() for task in self.tasks):
                        raise RuntimeError('Wi-Fi listener could not start')
                    await asyncio.sleep(.05)
                else:
                    raise RuntimeError('Wi-Fi listener did not become ready')
                self.address, self.origin, self.fingerprint = address, origin, fingerprint
                self.setup_url = f'http://{address}:{setup_port}/setup'
                record = {'address': address, 'ports': [setup_port, secure_port]}
                temporary = self.directory / 'enabled.tmp'
                temporary.write_text(json.dumps(record), encoding='utf-8')
                temporary.replace(self.directory / 'enabled.json')
                return self.status()
            except asyncio.CancelledError:
                await self._stop()
                raise
            except Exception as exc:
                await self._stop()
                self.error = 'Could not start Wi-Fi access. Check that ports 7701 and 7702 are free and this computer is connected to Wi-Fi.'
                LOG.exception('Wi-Fi access failed')
                if isinstance(exc, AppServiceError): raise
                raise AppServiceError(409, self.error) from exc

    async def _stop(self):
        self.auth.disable_phone_access()
        for server in self.servers:
            server.should_exit = True
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for sock in self.sockets:
            sock.close()
        self.servers, self.tasks, self.sockets = [], [], []
        self.address = self.origin = self.setup_url = self.fingerprint = None

    async def stop(self, *, disable=False):
        async with self.lock:
            if disable:
                (self.directory / 'enabled.json').unlink(missing_ok=True)
            await self._stop()

    async def restore(self):
        if self.config.remote_access: return
        try:
            record = json.loads((self.directory / 'enabled.json').read_text(encoding='utf-8'))
            await self.start(record['address'], ports=record['ports'])
        except FileNotFoundError:
            pass
        except Exception:
            self.error = 'Wi-Fi access could not resume. Reopen phone setup and enable it with this computer’s current address.'
            LOG.exception('Could not resume Wi-Fi access')
