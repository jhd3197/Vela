# Develop Vela

This setup is for changing Vela's source. Normal users run the packaged server.

Requires Python 3.10+ and Node.js 22. Clone the hub and prepare a Python virtual
environment using your platform's activation command:

```bash
git clone https://github.com/jhd3197/vela.git
cd vela
git switch dev
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

Build each OS download on that OS. The **Release Vela Server** GitHub Actions
workflow builds and smoke-tests Windows, Linux and macOS on native runners.

## Development and automatic releases

1. Push work to `dev` (or merge a feature PR into `dev`). Keep user-facing notes
   under `Unreleased` in `CHANGELOG.md`.
2. Open a PR from `dev` into `main`. Review the changes and let Checks pass.
3. Merge the PR. The push to `main` automatically runs **Release Vela Server**.
4. After all three platform builds and smoke tests pass, the workflow publishes
   a GitHub Release with executable archives and their SHA-256 files attached.

No manual tag or asset upload is needed. The first release uses the current
server version; later releases increment the latest patch unless a larger
version is set in source. A manual workflow run on **main** can provide an
explicit `MAJOR.MINOR.PATCH` version, or leave it blank for automatic selection.
A manual run from another branch cannot publish a release.

Version metadata and Unreleased notes are stamped consistently into all builds.
After successful builds, the workflow commits that metadata to `main`, creates
the annotated version tag, uploads the complete asset set to a draft, and makes
the release public. The bot commit skips CI to prevent a release loop. Sync
`dev` with `main` after a release to pick up the version and changelog update.

Failed platform builds do not create a public release. Retry a failed run to
resume the same version after a tag was created; already-published assets are
left unchanged. If `main` advances during the build, that stale run refuses to
update it; use the run for current `main`. The workflow serializes release runs.

Only the publishing job requests `contents: write`; no personal access token
is required. If branch rules disallow the release bot's metadata commit to
`main`, the push will fail explicitly. Configure those rules deliberately—do
not disable branch protection as a workaround in the workflow.

The first distributions are portable archives. OS installers, code signing,
macOS notarization, background services and automatic updates are not implemented.
