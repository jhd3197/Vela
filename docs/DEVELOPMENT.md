# Develop Vela

This setup is for changing Vela's source. Normal users run the packaged server.

Requires Python 3.10+ and Node.js 22. Clone the hub and prepare a Python virtual
environment using your platform's activation command:

```bash
git clone https://github.com/jhd3197/vela.git
cd vela
python -m venv .venv
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. On macOS/Linux,
use `source .venv/bin/activate`. Then:

```bash
pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
python -m vela --open-browser
```

For live frontend development, leave the server running and run
`npm --prefix web run dev` in another terminal. Open http://localhost:5173.

Run the hub checks:

```bash
python -m unittest discover -s tests
node --test tests/bridge.test.mjs
npm --prefix web run build
```

See [the repository guide](REPOSITORIES.md) for sibling app development.

## Build a server download

From the activated environment, after building the dashboard:

```bash
pip install -r scripts/requirements-build.txt
python scripts/build-server.py
python scripts/test-server-bundle.py
```

The archive and SHA-256 file appear under `.local/releases/`. The bundle
contains the Python runtime, backend, SDK/schema snapshots and built dashboard.
No sibling repositories are required. The existing `__file__`-relative asset
paths work inside the bundle as described in the
[PyInstaller runtime documentation](https://pyinstaller.org/en/stable/runtime-information.html).

Build each OS download on that OS. The **Build server downloads** GitHub Actions
workflow builds and smoke-tests Windows, Linux and macOS artifacts on their native runners.
It can run manually or when a `v*` tag is pushed. It uploads workflow artifacts;
it does not publish a GitHub Release. Review the artifacts before attaching
the archives and hashes to a release in `jhd3197/vela`.

The first distributions are portable archives. OS installers, code signing,
macOS notarization, background services and automatic updates are not implemented.
