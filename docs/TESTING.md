# Testing Vela

Use disposable installations and data directories. The hub's automated checks
use temporary fixtures; do not point them at your personal Vela data.

## Core checks

After [developer setup](DEVELOPMENT.md), run from the repository root:

```bash
python -m unittest discover -s tests
node --test tests/bridge.test.mjs tests/resource.test.mjs
npm --prefix web run build
```

The Python suite covers manifests, authentication, scoped storage, migrations,
connections, app actions, releases and launcher behavior. Node checks cover
the host bridge, service-worker boundaries, and shared request behavior (polling,
overlapping refreshes, errors, and cleanup). No sibling checkout is needed.

## Browser acceptance

After building the dashboard, make Playwright available in `web/` and install
its Chromium browser. A local-only installation can use
`npm --prefix web install --no-save --package-lock=false playwright` followed by
`web/node_modules/.bin/playwright install chromium` (use the `.cmd` launcher on
Windows). This does not add Playwright to the published runtime dependencies.

Run from the hub root:

```bash
node web/scripts/test-app-contract.mjs
node web/scripts/test-releases.mjs
node web/scripts/test-actions.mjs
node web/scripts/test-connections.mjs
node web/scripts/test-shared-ui.mjs
node web/scripts/test-dashboard.mjs
```

Run browser suites sequentially because some use the same fixture server port.
The shared UI suite uses an isolated Vite fixture without API calls to check
form semantics, field labels, stale responses, retries, and action submission.
The dashboard suite checks all seven routes at desktop and phone widths in both
themes, and saves screenshots under `docs/screenshots/shared-foundations/`.

The connection suite covers HTTPS, migration and an Ollama connection.
It also requires OpenSSL.
Browser scripts launch temporary fixture servers and save screenshots under
ignored `docs/screenshots/` paths. `VELA_TEST_PYTHON` overrides the default
`.venv` Python; `VELA_BROWSER_CHANNEL` can select an installed Chrome browser.

Phone-size browser checks are not physical iOS/Android verification. Test a
real phone with a trusted certificate and a real Ollama service before claiming
that complete setup has been accepted. Some suites use a mock upstream.

## Server downloads

```bash
python scripts/build-server.py
python scripts/test-server-bundle.py
```

Install the maintainer build requirements first, as described in
[the developer guide](DEVELOPMENT.md#build-a-server-download). The smoke test
copies the built server outside the checkout, uses fresh data, checks the
dashboard and SDK assets, installs a fixture app and verifies storage. Build
and test on each target OS; a Windows success is not macOS/Linux acceptance.

## Documentation changes

Check Markdown links, referenced images and `git status`. Public docs must not
link to ignored plans or generated screenshots that will be missing from a
fresh clone. Keep `CHANGELOG.md` current for user/contributor-visible changes.
