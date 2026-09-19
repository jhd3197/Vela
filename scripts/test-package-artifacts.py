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
from vela.managed.contract import validate_managed_manifest

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
    # The managed-web-app starter, generated the same way. It is a package to
    # fill in rather than one to install, so what is checked is that it is a
    # shape this engine recognises and that its own validator still refuses it
    # while the release checksums are placeholders.
    hosted = temp / 'app-managed'
    subprocess.run(['node', str(cli), 'my-service', str(hosted), 'managed'], check=True)
    manifest = validate_managed_manifest(
        json.loads((hosted / 'app.json').read_text(encoding='utf-8')),
        folder='my-service', path=hosted)
    assert manifest.id == 'my-service' and manifest.service['trust'] == 'trusted-native'
    assert manifest.raw['integration'] == {'sdk': False, 'agent': False}
    checked = subprocess.run(['node', str(hosted / 'validate.mjs'), str(hosted)],
                             capture_output=True, text=True)
    assert checked.returncode != 0, 'A generated managed package validated while still a template'
    assert 'placeholder' in checked.stderr, checked.stderr
    for name in ('manifest-v2.schema.json', 'manifest-v3.schema.json'):
        schema = json.loads((temp / 'contracts/package' / name).read_text())
        assert schema['properties']['schemaVersion']['const'] == int(name[10])
print('PASS: SDK contains no host bridge; contract and CLI tarballs work independently; '
      'all three SDK starters validate; the managed-web-app starter generates and still '
      'asks its author for the release checksums')
