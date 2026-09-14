"""Explicitly vendor reviewed SDK/schema versions from sibling repositories."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
assets = ROOT / 'vela/assets'
records = json.loads((assets / 'versions.json').read_text())
sources = {}
for name, record in records.items():
    repo = ROOT.parent / record['repository']
    source = repo / record['source']
    sources[name] = source.read_bytes()
    record['version'] = json.loads((repo / 'package.json').read_text())['version']
    record['sha256'] = hashlib.sha256(sources[name]).hexdigest()
for name, data in sources.items():
    (assets / name).write_bytes(data)
(assets / 'versions.json').write_text(json.dumps(records, indent=2) + '\n')
print('Updated runtime snapshots. Review the diff and run the hub contract tests.')
