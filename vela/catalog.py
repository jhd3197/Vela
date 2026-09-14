"""Explicitly configured, digest-pinned catalog with last-known-good offline cache."""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import httpx
from .manifest import validate_manifest, ManifestError
from .app_storage import AppServiceError
from .package_files import MAX_BYTES, write_json


def fetch_bytes(source, maximum=MAX_BYTES):
    if source.startswith('https://'):
        if urlsplit(source).username or urlsplit(source).password:
            raise AppServiceError(422, 'Credential-bearing catalog URLs are unsupported')
        try:
            started = time.monotonic()
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', source) as response:
                    if response.status_code != 200: raise AppServiceError(502, 'Release source did not return HTTP 200')
                    result = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 15: raise AppServiceError(504, 'Release download exceeded its deadline')
                        result.extend(chunk)
                        if len(result) > maximum: raise AppServiceError(413, 'Release source exceeds size limit')
                    return bytes(result)
        except httpx.HTTPError as exc: raise AppServiceError(502, 'Release source is unavailable') from exc
    path = Path(source)
    if not path.is_file(): raise AppServiceError(404, 'Release source file is unavailable')
    if path.stat().st_size > maximum: raise AppServiceError(413, 'Release source exceeds size limit')
    return path.read_bytes()


class Catalog:
    def __init__(self, config):
        self.config = config
        self.source = config.catalog_source
        self.path = config.data_dir / 'catalog-cache.json'
        self.entries, self.error, self.cached = {}, None, False
        if self.source:
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if saved['source'] == self.source and saved['pin'] == config.catalog_sha256:
                    self.entries = self.validate(saved['index'])
                    self.cached = True
            except (OSError, ValueError, KeyError, TypeError, AttributeError, AppServiceError, ManifestError): pass
            # Local indexes are cheap; remote refresh is always an explicit action.
            if not self.source.startswith('https://'):
                self.refresh()

    def validate(self, index):
        if not isinstance(index, dict) or set(index) != {'schemaVersion', 'releases'} or index['schemaVersion'] != 1 or not isinstance(index['releases'], list) or len(index['releases']) > 1000:
            raise AppServiceError(422, 'Invalid catalog index')
        result = {}
        for entry in index['releases']:
            if not isinstance(entry, dict) or set(entry) != {'manifest', 'publisher', 'archive', 'sha256'}:
                raise AppServiceError(422, 'Invalid catalog release fields')
            raw = entry['manifest']
            manifest = validate_manifest(raw, raw.get('id', ''), self.config.data_dir / 'catalog-metadata' / raw.get('id', 'invalid'))
            if manifest.schema_version != 2 or not isinstance(entry['publisher'], str) or not entry['publisher']:
                raise AppServiceError(422, 'Catalog releases need a v2 manifest and publisher')
            import re
            if not isinstance(entry['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', entry['sha256']) or not isinstance(entry['archive'], str):
                raise AppServiceError(422, 'Release requires a SHA-256 pin and archive')
            if manifest.id in result: raise AppServiceError(422, 'Duplicate catalog app identity')
            archive = entry['archive']
            if self.source.startswith('https://'):
                archive = urljoin(self.source, archive)
                parsed = urlsplit(archive)
                if parsed.scheme != 'https' or parsed.netloc != urlsplit(self.source).netloc or parsed.username or parsed.fragment:
                    raise AppServiceError(422, 'Remote release must remain on the pinned catalog HTTPS origin')
            else:
                if '://' in archive or Path(archive).is_absolute(): raise AppServiceError(422, 'Local catalog archives must be relative files')
                archive = (Path(self.source).parent / archive).resolve()
                if not archive.is_relative_to(Path(self.source).parent.resolve()): raise AppServiceError(422, 'Archive escapes catalog directory')
                archive = str(archive)
            result[manifest.id] = {**entry, 'archive': archive, 'parsed': manifest}
        return result

    def refresh(self):
        try:
            if not self.source: raise AppServiceError(404, 'No catalog configured')
            if self.source.startswith('https://') and not self.config.catalog_sha256:
                raise AppServiceError(422, 'Remote catalogs require an operator-configured SHA-256 trust pin')
            data = fetch_bytes(self.source, 2 * 1024 * 1024)
            if self.config.catalog_sha256 and hashlib.sha256(data).hexdigest() != self.config.catalog_sha256:
                raise AppServiceError(422, 'Catalog digest does not match its trust pin')
            index = json.loads(data)
            entries = self.validate(index)
            write_json(self.path, {'source': self.source, 'pin': self.config.catalog_sha256, 'index': index})
            self.entries, self.error, self.cached = entries, None, False
        except (AppServiceError, ManifestError, OSError, ValueError, TypeError, AttributeError) as exc:
            self.error = str(exc)
            self.cached = bool(self.entries)
        return self.status()

    def status(self):
        return {'source': self.source, 'cached': self.cached, 'error': self.error, 'releases': [
            {'id': key, 'version': value['manifest']['version'], 'publisher': value['publisher'], 'sha256': value['sha256']}
            for key, value in self.entries.items()]}

    def archive(self, app_id):
        entry = self.entries.get(app_id)
        if not entry: raise AppServiceError(404, 'No pinned release for this app')
        path = self.config.data_dir / 'release-cache' / (entry['sha256'] + '.zip')
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']: return path, entry
        data = fetch_bytes(entry['archive'])
        if hashlib.sha256(data).hexdigest() != entry['sha256']: raise AppServiceError(422, 'Release archive digest mismatch')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp'); temporary.write_bytes(data); temporary.replace(path)
        return path, entry
