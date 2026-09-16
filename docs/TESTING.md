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
connections, app actions, releases, launcher behavior, automations, the log
store, the health checks, the error record, the support bundle, backups
with their schedule and restore, and the update check with its
apply path — downloads, checksum refusal, the journal, rollback decisions,
automatic-mode gating and the generated apply scripts. Nothing in the suite
contacts GitHub or replaces anything on the machine running it. Node checks
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
node web/scripts/test-launchpad.mjs
node web/scripts/test-rail.mjs
node web/scripts/test-dashboard.mjs
node web/scripts/test-system.mjs
node web/scripts/test-desk.mjs
node web/scripts/test-settings.mjs
node web/scripts/test-security.mjs
node web/scripts/test-chat.mjs
node web/scripts/test-automations.mjs
node web/scripts/test-phone-setup.mjs
node web/scripts/test-mobile-layout.mjs
```

Run browser suites sequentially because some use the same fixture server port.
The shared UI suite uses an isolated Vite fixture without API calls to check
form semantics, field labels, stale responses, retries, and action submission.
It also verifies one shared engine request across consumers and page changes,
shared error/retry state, polling cleanup, nested modal focus restoration,
keyboard containment, and pending Escape/backdrop guards at desktop/phone widths.
The Launchpad suite uses an isolated Vite fixture with disposable app records to
check the full-screen app grid: the Open, Apps, Vela and Get more apps sections
and their contents, an uninstalled app staying out of them, running and
attention dots, the hero search filtering the grid live with Enter opening the
first match and a "no match" state, the tile context menu opened by right-click
and Shift+F10 with focus returned to the tile, Escape leaving, `Ctrl+Space`
toggling the Launchpad from another route, and no horizontal overflow at 320,
390, 768 and 1440 pixels. Screenshots go to `docs/screenshots/launchpad/`.
The rail suite uses an isolated Vite fixture with disposable app records to check
the narrow rail: an empty installation with the default pins, many apps, long and
duplicate names, stable ordering, keyboard focus and selection, pin, unpin and
reorder from the rail and the app-window menus, the Launchpad as a rail
destination, no secondary menu or All apps control, the shortcut sheet opening
with `?` and `Ctrl+1` opening the first pinned app, a short window, every page
keeping the rail on a phone with no hamburger and without covering its content,
and an app workspace keeping one rail beside itself at 390 and 320 pixels.
Screenshots go to `docs/screenshots/rail/`.
The dashboard suite checks the default destinations at desktop and phone widths
in both themes, the desk's navigation across reload, Back, rotation and
scrolling, the seeded board over its wallpaper at six widths, the supported
add-app sources, Settings › Desk refusing a folder that does not exist, and the
`/environments` deep link explaining itself and offering an explicit enable
action while developer tools are off. It saves screenshots under
`docs/screenshots/shared-foundations/`.
The system suite uses an isolated Vite fixture with disposable log files to
check System: developer tools off explaining itself with no logs shown, the
Overview and Logs tabs with the engine card, the file list grouped by what
wrote each log with a rotated copy under its base, the newest lines with
warnings and errors marked, search narrowing and highlighting with a "no match"
state, `/` reaching the search box, the line-count choice, the auto-refresh
switch, choosing another log, an authenticated download, Clear asking first and
only emptying the log on confirmation, and no sideways overflow at 390 pixels.
It also covers the Errors tab — one row per failure with its repeat count,
source chips, the traceback behind a disclosure, search, resolve, reopen and
delete — and building a support bundle from Overview. Screenshots go to
`docs/screenshots/system/`.
The desk suite drives the board against a disposable engine: adding a widget
from the library, moving and resizing it with the keyboard and with a pointer,
undo and redo, saving and finding the same arrangement after a reload, Cancel
putting an edit back, the prompt when leaving with unsaved changes, removing a
widget, the phone board staying its own board, long-press reaching Arrange mode,
the health widget reporting the last sweep and running one when asked,
and no sideways overflow while arranging at 320 and 390 pixels. It switches the
settle transition off so geometry is never measured mid-animation.
The settings suite checks the wide popup and the phone screens: category
navigation, the edge-to-edge list, one section at a time with Back, Escape
stepping through the same screens, retained page and form drafts,
preference saving and rollback, keyboard focus and deep links, with disposable
API responses. The two compositions are checked at 320, 390, 430, 768, 860, 861
and 1440 pixels, in a short landscape window, at 200% zoom, with reduced motion
and with a stand-in open keyboard, keeping one unsent form draft through all of
them and across the crossover between them. It also checks Settings › Updates — the privacy copy naming the one anonymous
request, the switch that stops it, release notes rendered with images stripped
and links opening in a new tab — Settings › Backups & storage — the protection summary, turning
the daily schedule on and changing what it keeps, and a restore that stays
disabled until the backup's name is typed and then reports the safety copy it
took — and Settings › Health: opening the section runs nothing, Run now
lists the failing check first with a skipped one only counted, and Repair fixes
the row it was pressed on without a second sweep. It also covers the
developer-tools preference:
off by default, switching without reloading or losing an unsent message,
persisting across a reload, following another tab on the same origin, explaining
itself when a bookmark lands on a hidden section, and falling back to the
session when the browser refuses to store it. Screenshots are saved under
`docs/screenshots/settings/`.
The security suite drives app lock against a stand-in engine that models the
contract the real one enforces. It covers setting a PIN up, a refused password
and a mismatched repeat, Back through the setup steps, Lock now, wrong attempts
and the fall back to the Vela password, enrolling a pattern by dragging and by
choosing dots one at a time, a locked answer from any request restoring the lock
screen, and a short landscape window. Screenshots are saved under
`docs/screenshots/security/`. The engine's own enforcement — enrollment,
throttling, previously issued app tokens, streams and the local trust boundary —
is covered by `tests/test_security.py`, and the pattern rules the browser mirrors
are pinned by both that file and `tests/pattern.test.mjs`.
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

Bots and rooms are covered by the Python suite (`tests/test_bots_rooms.py`) with
a deterministic fake model stream, so they need no model server. It covers the
migration of a pre-bots database and repeating that migration, profile isolation
between concurrent requests, a tool revoked between snapshot and call, a private
chat staying out of a room, bot deletion keeping its attribution, ambiguous and
out-of-room mentions, roundtable ordering, duplicate sends, cancellation and
restart, per-bot failure and retry, and history-off and purge behavior.

Check these in the browser by hand, at desktop and phone widths in both themes,
because no automated browser suite covers them yet:

1. **Bots tab** — create a bot from a starter, edit it, duplicate it and confirm
   the copy has no tools selected, archive and restore it, then delete it and
   confirm an existing chat still shows its name on the old answers.
2. **Assisted drafting** — draft instructions from a description, edit the
   result, and confirm that creating a bot still works with the model stopped.
3. **Preview** — try a bot before saving and confirm no conversation appears in
   the Chats tab afterwards.
4. **Direct chats** — two bots with different instructions answer differently in
   their own chats, each showing its own name and model.
5. **Rooms** — create a three-bot roundtable, confirm each answers once in order,
   then use mention mode and `@all`. Check the composer says who the message
   will reach, and that the `@` picker separates Bots from Apps.
6. **Stop and retry** — Stop mid-run and confirm queued bots never answer and
   partial text is kept; make one bot fail and confirm only that bot retries.
7. **Repair** — delete a bot that is in a room and confirm the room refuses to
   run and offers Edit room.
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

The mobile-layout suite uses an isolated Vite fixture with the real shell,
composer, dialog and drawer at a phone size that reports a coarse pointer. It
checks that every touch field renders at 16 CSS pixels or more while larger
inherited text is preserved, that the viewport stays scalable and selection and
context menus are untouched, that each scrolling region owns its scrolling and
contains only vertical chaining, that a dialog and a drawer scroll their own
body while the page behind keeps its position, that a dialog and a conversation
composer stay clear of an on-screen keyboard without moving the reader away from
what they were reading, that pinch zoom is not mistaken for a keyboard, that
every page keeps its navigation at narrow, short-landscape and tablet split-screen
sizes, and that nothing overflows horizontally at 320px or at 200% zoom.
Screenshots go to `docs/screenshots/mobile/`.

A headless browser has no on-screen keyboard, so the fixture drives a stand-in
visual viewport: the measurement service, its geometry and every layout rule
that reads it are real, but the phone hardware is not. Physical keyboard,
rotation and Home Screen behaviour still have to be accepted on a device, as
described under the acceptance matrix below.

The connection suite covers HTTPS, migration and an Ollama connection.
It also requires OpenSSL.
The connected-web-app suite also requires OpenSSL and uses a disposable HTTPS
service. It checks add/edit/remove, persistence, its own login and browser storage,
host isolation, cross-origin redirect blocking, frame-policy fallback and
desktop/phone layouts. It never connects to a user's installed services.
Browser scripts launch temporary fixture servers and save screenshots under
ignored `docs/screenshots/` paths. `VELA_TEST_PYTHON` overrides the default
`.venv` Python; `VELA_BROWSER_CHANNEL` can select an installed Chrome browser.

## Phone acceptance

Browser suites catch regressions in Vela's own layout code. They do not
reproduce a physical keyboard, a browser toolbar that hides as you scroll, or an
installed Home Screen app. Accept these on hardware, recording the device, OS
and browser version you used, with equal weight for both:

| Device  | Modes                          |
| ------- | ------------------------------ |
| iPhone  | Safari tab and Home Screen app |
| Android | Chrome tab and installed app   |

On each, exercise: opening the desk in a fresh browser state and reaching a ready
app in one tap with no menu step; keyboard open, close and dismissal; a long
draft with caret movement, selection, copy and paste; rotation with the keyboard
open; a browser toolbar appearing and disappearing; returning from another app;
scrolling to the end of a long list and inside a dialog; 200% zoom and a larger
system text size; navigation Back; and editing inside an app frame. Check a
tablet split-screen layout, a hardware keyboard where you have one, and the
workflow canvas gestures separately from page scrolling.

Record what you could not test rather than implying it passed.

Phone-size browser checks are not physical iOS/Android verification. Test a
real phone with a trusted certificate and a real Ollama service before claiming
that complete setup has been accepted. Some suites use a mock upstream.

## Server downloads

```bash
python scripts/build-server.py
python scripts/test-server-bundle.py
```

`test-server-bundle.py` starts with two update checks that need no bundle and
run in seconds: it executes the real swap script against throwaway directories
on this OS, asserting the install directory is replaced and the previous one
kept, and it runs the whole download/verify/back-up path against a local asset
server over HTTP, asserting a mismatched `.sha256` is refused *before* anything
is backed up. Neither touches a real install or a real release.

`test-windows-distribution.py` additionally checks that the flags Vela's
updater passes to the installer are the same ones that check installs with, so
an update can never run an invocation nothing has verified.

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
