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
node --test tests/bridge.test.mjs tests/resource.test.mjs
npm --prefix web run build
```

See [the repository guide](REPOSITORIES.md) for sibling app development.

## Dashboard structure

The dashboard lives in `web/src/`. Reuse these foundations when adding a feature:

| Location | Responsibility |
| --- | --- |
| `styles/main.scss` | Stylesheet entry point; ordered Sass `@use` modules |
| `styles/_tokens.scss` | Shared colors, fonts, radii, shadows and light/dark theme variables |
| `styles/layout/`, `styles/components/`, `styles/pages/` | Shell styles, reusable UI styles, and feature-specific styles |
| `components/ui/` | `Button`, `PageHeader`, `FormField`, `LoadingState`, `EmptyState` |
| `components/` | Vela-specific pieces such as app rows, the shell, and release reviews |
| `hooks/` | Shared resource loading, polling, and user-triggered action state |
| `navigation.js` | Dashboard routes, page components, labels, icons and mobile visibility |
| `pages/` | Page composition and feature-specific behavior |
| `api.js`, `store.jsx`, `bridge/` | Authenticated host requests, shared app state, and the host/app boundary |

Run `npm --prefix web ci` after pulling dependency changes. Vite compiles SCSS
during development and builds; server users need no Sass installation.

Keep theme values as CSS custom properties (`var(--text)`, `var(--bg-card)`,
etc.) so light/dark switching works at runtime. Put new styles in their owning
module and load new modules from `styles/main.scss`. Its current order preserves
the existing cascade, including responsive overrides; do not alphabetize it.
Prefer existing classes and tokens. Add Sass mixins when a repeated styling
pattern needs one, and keep selector nesting shallow.

### Add a dashboard page

For example, a read-only system-status page can use the existing engine API:

```jsx
// web/src/pages/SystemStatus.jsx
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import Button from '../components/ui/Button.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';

export default function SystemStatus() {
  const { data, error, loading, refreshing, refresh } = useResource(api.getEngine, {
    intervalMs: 10000,
  });
  return <div className="page-inner">
    <PageHeader title="System status" description="Current engine activity."
      actions={<Button pending={refreshing} onClick={() => refresh()}>Refresh</Button>} />
    {error && <p role="alert">{error.message}</p>}
    {loading ? <LoadingState>Loading engine status…</LoadingState> : data ? (
      <section className="panel">
        <h2>Local engine</h2>
        <p>{data.apps_running ?? 0} apps running.</p>
      </section>
    ) : <EmptyState title="Status unavailable" description="Try refreshing when the engine is reachable." />}
  </div>;
}
```

Import the page in `navigation.js` and add an entry to `dashboardPages`, for
example `{ to: '/system-status', label: 'Status', icon: HardDrives,
component: SystemStatus, tabHidden: true }`. `HardDrives` is already imported
there. The shell and router both use this definition. Keep the phone bar small
with `tabHidden`; embedded `/app/:id` routes remain outside this list.

`Button` defaults to `type="button"`; form submissions explicitly use
`type="submit"`. Use `variant="primary"`, `variant="danger"`, or
`variant="ghost"`, `size="small"`, and `pending` as needed. Navigation uses
React Router's `Link` with existing button classes. `PageHeader.actions` accepts
buttons, filters, or other page controls.

Wrap one input, select, or textarea in `FormField` to connect its label and
optional help/error text. Keep its existing layout wrapper and own its value:

```jsx
<div className="field">
  <FormField label="Server" hint="Address reachable from this engine." error={error}>
    <input value={endpoint} onChange={event => setEndpoint(event.target.value)} />
  </FormField>
</div>
```

Import `FormField` from `../components/ui/FormField.jsx`. It preserves an
explicit input ID, generates one otherwise, and connects help/errors through
`aria-describedby`. It does not perform validation or save the field.

### Share behavior where the semantics match

`useResource(loader, { enabled, intervalMs })` fetches on mount and returns
`data`, `error`, `loading`, `refreshing`, and `refresh`. Polling is optional and
waits for the previous request to finish. Concurrent refreshes share the same
request. Errors retain the last successful data; the caller chooses how to
display that state. Cleanup aborts the request and ignores late responses.
It does not cache across components.

Use a stable API method as the loader, or `useCallback` for an ID-specific
loader such as `options => api.getStatus(id, options)`, with `[id]` as its
dependencies. Pass the supplied options through to support abort signals.
Changing the loader clears the old resource; `enabled: false` stops it and
clears the displayed result. Do not pass an inline loader that changes on
every render. See `useAppStatus` in `store.jsx` for a working example.

`useAsyncAction()` returns `run`, `pending`, and `error` for user-triggered
work. `await run(() => api.someAction())` returns `{ value }` on success or
`undefined` on failure, a duplicate submission, or unmount. Check that result
before updating local state. It handles errors and prevents duplicate pending
submissions; it never retries an action automatically. See `AppConnection.jsx`
and `ReleaseImport.jsx` for real consumers. Key an app-specific form by app ID
so switching apps also ends its old action scope.

Keep app lifecycle operations in the existing app provider. Keep permission
reviews, grants and migration decisions explicit in their feature components.
New HTTP endpoints should call Vela's existing Python domain services; add
domain routers when route growth warrants them. Independent apps and their SDK
remain in sibling repositories, as described in [REPOSITORIES.md](REPOSITORIES.md).

Run the relevant [checks and browser suites](TESTING.md), including both themes
and desktop/phone layouts for shared styles or controls. Add an Unreleased
changelog entry for user behavior or contributor workflow changes.

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

On Windows x64, install [Inno Setup 6](https://jrsoftware.org/isdl.php) to build
the installer from the same bundle:

```powershell
python scripts/build-windows-installer.py
python scripts/test-windows-distribution.py
```

Set `ISCC_PATH` if the compiler is outside its normal installation directory.
The setup EXE and checksum also appear in `.local/releases/`. Windows uses the
existing `docs/images/logo.png`, converted into a multi-resolution ICO during
packaging. Its tray dependencies are included by `requirements-build.txt`.
The PyInstaller console is hidden on double-click and retained when launched
from a terminal; `--no-tray` provides the foreground server mode.

The Windows distribution check verifies embedded logo/version resources, real
tray startup and graceful shutdown, installation, upgrade, sign-in cleanup and
data preservation on uninstall. It uses disposable data, a separate installer
identity and startup value, and no Start/desktop shortcuts.

## Development and automatic releases

1. Push work to `dev` (or merge a feature PR into `dev`). Keep user-facing notes
   under `Unreleased` in `CHANGELOG.md`.
2. Open a PR from `dev` into `main`. Review the changes and let Checks pass.
3. Merge the PR. The push to `main` automatically runs **Release Vela Server**.
4. After all three platform builds and smoke tests pass, the workflow publishes
   a GitHub Release with a Windows setup EXE, all three portable archives and
   their SHA-256 files attached. Windows installation checks must also pass.

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

Windows has a per-user installer and tray controls with optional start at sign in.
macOS/Linux currently use portable archives. Code signing, macOS notarization,
background system services and automatic updates are not implemented.
