"""Extract pinned test apps without depending on sibling source repositories."""
import atexit
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_temporary = tempfile.TemporaryDirectory(prefix='vela-fixture-apps-')
atexit.register(_temporary.cleanup)
APPS = Path(_temporary.name)
with zipfile.ZipFile(ROOT / 'tests/fixtures/apps.zip') as archive:
    archive.extractall(APPS)
