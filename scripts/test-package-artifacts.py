"""Pack first; verify public artifacts and generate apps outside this repository."""
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vela.manifest import load_manifest

with tempfile.TemporaryDirectory(prefix='vela-artifacts-') as temporary:
    temp = Path(temporary)
    for name in ('sdk', 'contracts', 'create-app'):
        repository = ROOT.parent / {'sdk':'vela-sdk', 'contracts':'vela-contracts', 'create-app':'vela-create-app'}[name]
        version = json.loads((repository / 'package.json').read_text())['version']
        archive = repository / 'dist' / f'vela-{name}-{version}.tgz'
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            assert not any('host.js' in name or 'node_modules' in name for name in names)
            tar.extractall(temp / name, filter='data')
    cli = temp / 'create-app/package/cli.mjs'
    for mode in ('hub', 'compact', 'seamless'):
        output = temp / ('app-' + mode)
        subprocess.run(['node', str(cli), output.name, str(output), mode], check=True)
        manifest = load_manifest(output)
        assert manifest.view['chrome'] == mode
        assert (output / manifest.web.entry).is_file()
        result = subprocess.run(['node', str(cli), output.name, str(output)], capture_output=True)
        assert result.returncode != 0, 'Existing directories must never be overwritten'
    schema = json.loads((temp / 'contracts/package/manifest-v2.schema.json').read_text())
    assert schema['properties']['schemaVersion']['const'] == 2
print('PASS: SDK contains no host bridge; contract and CLI tarballs work independently; all three starters validate')
