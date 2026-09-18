"""The committed API surface must match the served one.

Follows ServerKit's `backend/tests/test_api_surface_inventory.py` (MIT, same
owner). ServerKit snapshots a Flask blueprint spec; this reads FastAPI's
`app.openapi()`. Either way the contract is the same: a route rename, removal
or method change is a visible diff in `docs/API_SURFACE.md` in the commit that
made it, rather than something an app or the dashboard discovers at runtime.

Deliberately method and path only. Summaries and schemas churn without
breaking a caller; the surface is what a caller dials.

Regenerate (repository root, PowerShell):

    $env:VELA_UPDATE_API_SURFACE=1; python -m unittest tests.test_api_surface

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""
import atexit
import os
import tempfile
import unittest
from pathlib import Path

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-surface-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from vela.api import create_app
from vela.config import Config

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "API_SURFACE.md"

HEADER = """# Vela API surface

Generated from the served OpenAPI spec — do not edit by hand.

Regenerate from the repository root:

```powershell
$env:VELA_UPDATE_API_SURFACE=1; python -m unittest tests.test_api_surface
```

`tests/test_api_surface.py` fails when this file and the served spec differ.
A route change belongs in the same commit as the regenerated list.

"""

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def measured_surface(app) -> list[str]:
    """Sorted `METHOD /path` lines for every operation the app serves."""
    spec = app.openapi()
    lines = set()
    for path, operations in (spec.get("paths") or {}).items():
        for method in operations:
            if method.upper() in _METHODS:
                lines.add(f"{method.upper()} {path}")
    return sorted(lines)


def _disposable_app():
    directory = tempfile.TemporaryDirectory(prefix="vela-surface-")
    atexit.register(directory.cleanup)
    root = Path(directory.name)
    config = Config(root / "data", root / "apps", ROOT / "web/dist")
    config.ensure_dirs()
    return create_app(config)


class ApiSurfaceTests(unittest.TestCase):
    def test_committed_surface_matches_served_surface(self):
        measured = measured_surface(_disposable_app())
        self.assertGreater(
            len(measured), 150,
            f"only {len(measured)} operations measured — the spec or the "
            f"extraction broke; fix that before trusting this gate",
        )
        rendered = HEADER + "\n".join(f"- `{line}`" for line in measured) + "\n"

        if os.environ.get("VELA_UPDATE_API_SURFACE") == "1":
            DOC.parent.mkdir(parents=True, exist_ok=True)
            DOC.write_text(rendered, encoding="utf-8", newline="\n")

        self.assertTrue(
            DOC.is_file(),
            "docs/API_SURFACE.md is missing — generate it with "
            "$env:VELA_UPDATE_API_SURFACE=1; python -m unittest tests.test_api_surface",
        )
        committed = DOC.read_text(encoding="utf-8")
        if committed == rendered:
            return
        committed_routes = {
            line.strip().strip("-").strip().strip("`")
            for line in committed.splitlines()
            if line.startswith("- `")
        }
        added = sorted(set(measured) - committed_routes)
        removed = sorted(committed_routes - set(measured))
        self.fail(
            "the served API surface differs from docs/API_SURFACE.md.\n"
            f"served but not committed: {added or 'none'}\n"
            f"committed but not served: {removed or 'none'}\n"
            "A deliberate route change regenerates the doc in the same commit; "
            "an accidental one is a defect in the route."
        )


if __name__ == "__main__":
    unittest.main()
