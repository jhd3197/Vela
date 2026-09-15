# Testing Vela

Use disposable installations and data directories. The hub's automated checks
use temporary fixtures; do not point them at your personal Vela data.

## Core checks

After [developer setup](DEVELOPMENT.md), run from the repository root:

```bash
npm --prefix web run check
```

The same command runs in CI: lint, formatting verification, Node tests, Python
tests, then the dashboard build. It stops at the first failure. Use
`npm --prefix web run format` to fix formatting, or run individual checks while
iterating: `npm --prefix web run lint`, `node --test tests/bridge.test.mjs
tests/resource.test.mjs`, `python -m unittest discover -s tests`, and
`npm --prefix web run build`. Browser acceptance remains a separate step.

The Python suite covers manifests, authentication, scoped storage, migrations,
connections, app actions, releases, launcher behavior and automations. Node checks
cover the host bridge, service-worker boundaries, shared request behavior
(polling, overlapping refreshes, errors, and cleanup) and the automation worker's
protocol. No sibling checkout is needed.

The automation checks cover document validation and the excluded step types,
revision conflicts, imports that must stay drafts without permissions, permission
grants and their invalidation by an app update, idempotent retries, durable runs
and their history across a restart, cancellation, interrupted outcomes, schedule
arithmetic through both clock changes, one-claim-per-occurrence dispatch, webhook
authentication and replay, and approvals resumed against the revision they
started on. The ones that execute a workflow need the automation runtime; without
it they skip with a reason rather than failing. Install it with
`python scripts/setup-automation-worker.py`. The worker's own checks run the real
process over its real protocol, including its watchdog: a force-killed Vela must
not leave the runtime running.

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
node web/scripts/test-connected-apps.mjs
node web/scripts/test-shared-ui.mjs
node web/scripts/test-rail.mjs
node web/scripts/test-dashboard.mjs
node web/scripts/test-settings.mjs
node web/scripts/test-chat.mjs
node web/scripts/test-automations.mjs
node web/scripts/test-phone-setup.mjs
```

Run browser suites sequentially because some use the same fixture server port.
The shared UI suite uses an isolated Vite fixture without API calls to check
form semantics, field labels, stale responses, retries, and action submission.
It also verifies one shared engine request across consumers and page changes,
shared error/retry state, polling cleanup, nested modal focus restoration,
keyboard containment, and pending Escape/backdrop guards at desktop/phone widths.
The rail suite uses an isolated Vite fixture with disposable app records to check
the narrow rail: an empty installation, many apps, long and duplicate names,
stable ordering, keyboard focus and selection, a short window, and the phone
navigation drawer's focus handling. Screenshots go to `docs/screenshots/rail/`.
The dashboard suite checks all seven routes at desktop and phone widths in both
themes, and saves screenshots under `docs/screenshots/shared-foundations/`.
The settings suite checks popup navigation, retained page and form drafts,
preference saving and rollback, keyboard focus, deep links and narrow layouts
with disposable API responses. Screenshots are saved under `docs/screenshots/settings/`.
The chat suite serves the built dashboard with disposable app records, a
controlled response stream and a stand-in conversation store. It checks the
bottom composer, app mentions, keyboard input, formatted responses, stop/retry,
scrolling and offline recovery, then the durable conversation behavior: creating
and switching conversations, restoring one from its route after a reload, a
follow-up staying bound to its conversation, per-conversation drafts, search,
rename, archive/restore, permanent deletion, an unavailable conversation id, a
one-time legacy transcript import that is never repeated, a stale stream that
must not write into another conversation, and history being turned off.
Screenshots are saved under `docs/screenshots/chat/`.
The automations suite serves a disposable engine with the pinned Notes fixture
installed and builds an automation the way a person would: the empty state with
no invented activity, creating one, the step picker offering only vetted steps
with no reachable Model Context Protocol import, the generated app-action form,
the permission review with its Allow and Remove controls, a run that creates
exactly one note in Notes, and 320/390/768/1024/1920 layouts in both themes for
the list and the editor. It runs the workflow for real when the automation
runtime is installed and says so when it is not. Screenshots are saved under
`docs/screenshots/automations/`.
The phone setup suite checks the welcome popup, dismissal and Settings shortcut,
Wi-Fi enable/disable, local and hosted QR handoffs, iPhone/Android instructions,
certificate guidance, clipboard and storage fallback, installed mode and narrow layouts. It uses the
built UI with disposable API responses and writes screenshots under
`docs/screenshots/phone-setup/`. Browser detection is emulated; verify the QR and
Home Screen installation on a physical iPhone before claiming device acceptance.
The Python phone-access tests run HTTP and HTTPS listeners on disposable loopback
ports, verify the generated certificate chain, restrict public bootstrap routes,
exercise password login and local-token rejection, and check restart/disable.
They do not change OS certificate trust or expose a test server on real Wi-Fi.

The connection suite covers HTTPS, migration and an Ollama connection.
It also requires OpenSSL.
The connected-web-app suite also requires OpenSSL and uses a disposable HTTPS
service. It checks add/edit/remove, persistence, its own login and browser storage,
host isolation, cross-origin redirect blocking, frame-policy fallback and
desktop/phone layouts. It never connects to a user's installed services.
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
dashboard and SDK assets, installs a fixture app, verifies storage, and runs a
real automation on the bundled Node runtime with Node removed from `PATH`. Run
`python scripts/fetch-node-runtime.py` before building, or the download ships
without automations and the smoke test says so. Build and test on each target OS;
a Windows success is not macOS/Linux acceptance.

## Documentation changes

Check Markdown links, referenced images and `git status`. Public docs must not
link to ignored plans or generated screenshots that will be missing from a
fresh clone. Keep `CHANGELOG.md` current for user/contributor-visible changes.
