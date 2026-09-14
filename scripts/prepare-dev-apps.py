"""Copy sibling app sources into an ignored development collection; never touch installed apps."""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--destination', type=Path, default=ROOT / '.local/apps')
args = parser.parse_args()
target = args.destination.resolve()
sources = []
for repo in sorted(ROOT.parent.glob('vela-*')):
    for manifest in repo.glob('*/app.json'):
        sources.append(manifest.parent)
if not sources:
    parser.error('No sibling app sources found. Clone app repositories beside vela first.')
if target.exists():
    parser.error(f'{target} already exists; choose a fresh --destination to avoid stale files or overwrites.')
target.mkdir(parents=True)
for source in sources:
    shutil.copytree(source, target / source.name)
print(f'Prepared {len(sources)} apps in {target}')
print(f'PowerShell: $env:VELA_APPS_DIR = "{target}"')
print('Then run: python -m vela')
