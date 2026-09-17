# Develop Vela

This setup is for changing Vela's source. Normal users run the packaged server.

Requires Python 3.10+ and Node.js 22.13 or later in the 22.x line (or Node.js 24+).
Clone the hub and prepare a Python virtual
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
python scripts/setup-automation-worker.py
python -m vela --open-browser
```

The automation step installs the workflow engine the automations feature runs in.
Skipping it leaves the rest of Vela working; the automations page then explains
that the runtime is missing. See [Automations](#automations).

For live frontend development, leave the server running and run
`npm --prefix web run dev` in another terminal. Open http://localhost:5173.

Run the hub checks:

```bash
npm --prefix web run check
```

This runs ESLint, Prettier's formatting check, Node regression tests, the Python
suite, and the dashboard build, stopping at the first failure. CI uses the same
command. Python is selected from `VELA_TEST_PYTHON`, the repository `.venv`, or
`python` on PATH, in that order. Run `npm --prefix web run format` to apply the
formatting conventions and `npm --prefix web run lint` for a quick code check.
Formatting covers the dashboard source, SCSS, browser scripts and configuration;
generated output, dependency trees and the lockfile are excluded.

See [the repository guide](REPOSITORIES.md) for sibling app development.

## Dashboard structure

The dashboard lives in `web/src/`. Reuse these foundations when adding a feature:

| Location | Responsibility |
| --- | --- |
| `styles/main.scss` | Stylesheet entry point; ordered Sass `@use` modules |
| `styles/_tokens.scss` | Shared colors, fonts, radii, shadows and light/dark theme variables |
| `styles/layout/`, `styles/components/`, `styles/pages/` | Shell styles, reusable UI styles, and feature-specific styles |
| `components/ui/` | Shared controls, page states, `Dialog` and `Drawer` |
| `components/` | Vela-specific pieces such as app rows, the shell, and release reviews |
| `hooks/` | Shared resource loading, polling, and user-triggered action state |
| `navigation.js` | Dashboard routes, page components, labels, icons and mobile visibility |
| `engine.jsx` | One engine status provider shared by the shell and dashboard pages |
| `pages/` | Page composition and feature-specific behavior |
| `api.js`, `store.jsx`, `bridge/` | Authenticated host requests, shared app state, and the host/app boundary |
| `automationsApi.js`, `components/automations/` | Automation requests and the embedded workflow editor |
| `desk/` | The home board: grid geometry, the widget registry and types, the widget renderers, one data provider and the boards client |

Run `npm --prefix web ci` after pulling dependency changes. Vite compiles SCSS
during development and builds; server users need no Sass installation.

Keep theme values as CSS custom properties (`var(--text)`, `var(--bg-card)`,
etc.) so light/dark switching works at runtime. Put new styles in their owning
module and load new modules from `styles/main.scss`. Its current order preserves
the existing cascade, including responsive overrides; do not alphabetize it.
Prefer existing classes and tokens. Add Sass mixins when a repeated styling
pattern needs one, and keep selector nesting shallow.

### Add a desk widget

Widget types live in `web/src/desk/types.jsx`, each with an id, a name, a
category, a default size, a minimum size and a `render` component. Ship a type
only once a real source for it exists; `vela/desk.py` keeps `CORE_WIDGET_TYPES`
in step so a saved board can never name a type the server does not know. Widgets
read from `desk/DeskDataProvider.jsx` rather than fetching for themselves, so a
board with four system widgets still makes one request per source, and that
provider pauses while the tab is hidden. Widgets an app provides are not added
here: they arrive from the app's manifest and render through
`desk/widgets/AppWidget.jsx`.

### Add a dashboard page

For example, a read-only system-status page can use the existing engine API:

```jsx
// web/src/pages/SystemStatus.jsx
import { useEngine } from '../engine.jsx';
import Button from '../components/ui/Button.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';

export default function SystemStatus() {
  const {
    engine: data, engineError: error, engineLoading: loading,
    engineRefreshing: refreshing, refreshEngine: refresh,
  } = useEngine();
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
rail: 'tools', railOrder: 5, component: SystemStatus }`.
`HardDrives` is already imported there. The shell and router both use this
definition. `rail` places the destination in the narrow rail (`primary` above
the installed-app shortcuts, `tools` below the separator, `foot` at the bottom)
and `railOrder` sorts within a group. The rail stays on screen at every width,
phones included; a page that needs a panel of its own on a phone renders
`NavDrawer` with a `panel` and a `label`, as Ask does with its conversations.
`childPaths` adds extra routes that render the same page, as
`/ask/:conversationId` does. Embedded `/app/:id` routes remain outside this
list.

Wrap the page body in `WorkspacePage` so it gets the contextual header and the
content surface:

```jsx
<WorkspacePage actions={<Button onClick={refresh}>Refresh</Button>}>
  <div className="page-inner">…</div>
</WorkspacePage>
```

Pass `panel` for a route that needs a context column beside its content (Ask
does), `scroll={false}` when the page owns its own scrolling regions, and
`compactSearch` when the header is too busy for the inline search box. Host
pages keep their own `<h1>` in the content; the header carries a `title` only
where the content does not, such as Ask and app workspaces.

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

### Make an app look like part of the server

An app owns everything inside its frame. To have the server draw the rail, the
app's name and its state around that frame instead of the app carrying its own
bar, declare `"view": {"surface": "embedded", "chrome": "hub"}` in `app.json`.
`compact` (the default) and `seamless` keep their existing standalone chrome, so
this is an explicit, per-app choice and nothing changes until a manifest opts in.

For the appearance itself, copy `vela-app.css`, `vela-theme.js` and
`vela-viewport.js` from a generated app (`create-vela-app`) or from
[vela-templates](https://github.com/jhd3197/vela-templates). They provide the
server's surfaces, spacing and controls, mirror the server's light/dark
preference from the bridge context onto `data-vela-theme`, and mirror the
reported viewport insets onto `--vela-inset-*`. Override `--vela-accent` to keep
the app's own identity.

Size the app to the frame it was given: `vela-app.css` uses `height: 100%` and
shrinkable regions, not a viewport unit. Inside a frame `100dvh` is the frame's
height, `env(safe-area-inset-*)` is zero, and the frame's own visual viewport
describes the frame rather than the phone. The host sizes the frame to the
workspace it can actually show and reports what it could not show; `.vela-layout`
subtracts that once. Do not subtract it again lower down, and do not add a
second estimate of the keyboard.
[vela-notes](https://github.com/jhd3197/vela-notes) is the worked example of a
list-plus-editor app using this layout. No host code, DOM or credentials are
shared: the bridge already sends the theme, and the stylesheet is the app's own.

### Share behavior where the semantics match

`EngineProvider` is mounted once above the dashboard routes in `main.jsx`.
Use `useEngine()` for engine status instead of creating a separate resource in
each page. The shell and pages share the same data, error and refreshing state;
navigation retains that state. Automatic polling waits ten seconds after each
completed request. `refreshEngine()` shares a pending request rather than
duplicating it. Failed refreshes retain the last successful data, and unmounting
the provider aborts its request and stops polling. Existing imports from
`store.jsx` remain supported through its re-export.

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

### Shared dialogs and drawers

Use `Dialog` or `Drawer` for modal overlays. Both use native modal dialogs for
focus containment and background isolation, restore focus to the opener on
close, and keep page scrolling locked until the last modal closes. Supply an
accessible name using `aria-labelledby` or `aria-label`. Keep `open` controlled
by the caller and pass `onClose` to update it; do not call native `.close()` or
use `form method="dialog"` in consumers.

```jsx
import { useId, useRef } from 'react';
import Dialog from '../components/ui/Dialog.jsx';
import Button from '../components/ui/Button.jsx';

function ConfirmChanges({ open, pending, onClose, onConfirm }) {
  const titleId = useId();
  const cancelButton = useRef(null);
  return <Dialog open={open} onClose={onClose} pending={pending}
    initialFocusRef={cancelButton} aria-labelledby={titleId}>
    <h2 id={titleId}>Apply changes?</h2>
    <p>Review your changes before applying them.</p>
    <Button pending={pending} onClick={onConfirm}>Apply</Button>
    <Button ref={cancelButton} disabled={pending} onClick={onClose}>Cancel</Button>
  </Dialog>;
}
```

`pending` prevents Escape/backdrop dismissal. Disable the consumer's Cancel
and competing action buttons during that work too. `initialFocusRef` selects
a safe starting control, usually Cancel or Close. When the opener may disappear
(for example, a menu item), `returnFocusRef` provides a fallback focus target.
Dialog backdrop dismissal is off by default; opt in with `closeOnBackdrop`.
Drawer enables it by default,
while preserving Vela's existing side-panel layout and full-width phone view.
Use its `drawer-header`, `drawer-body` and `drawer-footer` sections for content.
Keep permission reviews, saving and error messages in the feature itself.
See app details, release review and the unsaved-work prompt for real consumers.

### Layouts, touch and the viewport

One service owns viewport measurement: `src/viewport.js`, started once in
`main.jsx` and read through `hooks/useViewport.js`. It observes the visual
viewport, coalesces events to one measurement per frame, notifies consumers only
when a value they can see has changed, and publishes three custom properties on
the document so most layout needs no React render at all:

| Property | Meaning |
| --- | --- |
| `--vela-visible-height` | The height the browser is actually showing, in CSS pixels |
| `--vela-visible-top` | Where that rectangle starts inside the layout viewport |
| `--vela-keyboard-inset` | The bottom gap attributable to an on-screen keyboard |

Always give these a fallback (`var(--vela-visible-height, 100dvh)`) so a page
loaded before the service starts, or a browser without `VisualViewport`, still
lays out. Do not add a listener of your own; subscribe to the service instead,
so there is one measurement and one policy.

A smaller visible rectangle is not proof of a keyboard. Browser chrome moves by
about a toolbar's height, and pinch zoom can halve the rectangle while occluding
nothing. `--vela-keyboard-inset` is non-zero only when a text entry holds focus,
the page is not zoomed, and the remaining gap is large enough to be a keyboard —
so a browser that already resized the layout is never charged twice. While the
reader is zoomed, `--vela-visible-height` falls back to the layout viewport: a
zoomed page keeps its layout and is panned, not reflowed.

Subtract each inset exactly once, and say where:

- `.shell` keeps the dynamic viewport. Keyboard-sensitive regions inside it, such
  as Ask's workspace, subtract `--vela-keyboard-inset` themselves.
- `.appview`, which renders outside the shell, is sized to the visible rectangle,
  so an app frame inside it has nothing left to subtract.
- Dialogs and drawers are in the top layer, outside ordinary shell layout, so
  they apply the same geometry directly.
- An app frame is told what remains occluded, in the frame's own coordinates, as
  `context.viewport.insets`.

Structural thresholds are named once in `styles/_breakpoints.scss`, with the
reason for each, and mirrored in `src/breakpoints.js` for the few components
that decide what to render rather than how to style it. Ordinary reflow — a grid
that wants more columns, a header row that wraps — belongs to `auto-fit`,
`flex-wrap` or a container query, not to a new threshold. Give an `auto-fit`
track `minmax(min(240px, 100%), 1fr)` so it cannot outgrow a narrow container.

`styles/layout/_mobile.scss` is imported last and owns the shared touch policy:
a 16px floor on field text so iOS does not zoom on focus, `touch-action:
manipulation` on ordinary controls while pinch zoom, panning and selection stay
available, and `overscroll-behavior-y: contain` on each region that scrolls.
Do not suppress selection, context menus or scrolling globally, and keep
`touch-action: none` scoped to a surface that owns a custom gesture — the
workflow canvas does this inside `.tr-canvas-v2`, with visible fit and zoom
controls beside it.

Do not remount an iframe, editor, conversation or canvas because its container
crossed a threshold: keep the same instance, its unsaved text, its selection and
its scroll position across the change.

Keep app lifecycle operations in the existing app provider. Keep permission
reviews, grants and migration decisions explicit in their feature components.
New HTTP endpoints should call Vela's existing Python domain services; add
domain routers when route growth warrants them. Independent apps and their SDK
remain in sibling repositories, as described in [REPOSITORIES.md](REPOSITORIES.md).

Run the relevant [checks and browser suites](TESTING.md), including both themes
and desktop/phone layouts for shared styles or controls. Add an Unreleased
changelog entry for user behavior or contributor workflow changes.

## Automations

Automations are a Python service in `vela/automations/` plus a private Node worker
in `scripts/automation-worker/`. Vela owns everything durable — saved workflows,
revisions, permissions, schedules, runs and their events, all in
`automations.sqlite` beside the app data. The worker only executes one approved
revision at a time and asks Vela to perform every side effect.

| Location | Responsibility |
| --- | --- |
| `automations/catalog.py` | The one vetted node catalog, shared by the editor, validation and the worker |
| `automations/validate.py` | What may be stored as a draft, and what may be activated or run |
| `automations/store.py` | SQLite: workflows, revisions, runs, events, grants, schedules, webhooks, approvals |
| `automations/effects.py` | Permission checks and the app-action and notification effects |
| `automations/worker.py` | Starting, supervising and stopping the Node worker |
| `automations/service.py` | The run queue, the scheduler and the HTTP-facing behavior |
| `scripts/automation-worker/` | The worker: its stdio protocol and its executor allow-list |

Adding a step means changing three places that must agree: its definition in
`catalog.py`, any extra rules in `validate.py`, and its executor in
`scripts/automation-worker/src/executors.mjs`. A node type with no executor is
refused rather than skipped, on both sides of the boundary.

The dashboard embeds Tramo's own editor (`@tramo/editor`) with a registry built
from the server's catalog, so the step picker can only offer what Vela will also
validate and execute. Tramo's `--tr-*` variables are mapped to Vela's theme
tokens in `styles/components/_automation-canvas.scss`, scoped to the editor.

### Set up and update the runtime

```bash
python scripts/setup-automation-worker.py
```

This installs the pinned `@tramo/runtime` from npm and writes
`scripts/automation-worker/provenance.json` recording where it came from. When
that version is not published yet, pass a local Tramo checkout — a
development-only path that is recorded as such in the provenance file:

```bash
python scripts/setup-automation-worker.py --tramo-source ../tramo
```

Tramo is developed in its own repository; see [REPOSITORIES.md](REPOSITORIES.md).
Fix anything generic there rather than in Vela, and adopt it here by changing the
pinned version.

### What is deliberately excluded

Tramo ships many more node types than Vela registers. The ones that compile
configuration strings with `new Function` — `js-transform`, `if`, `switch`,
`json-parse`, the loop family, the state variables, `call-flow` — and the ones
that reach the network directly — `http-request`, `mcp-tool-call`, the AI nodes —
are left out of the executor registry, not merely out of the picker. A separate
process is isolation for crashes and lifetimes, not a sandbox; leaving an
executor unregistered is what makes its node unreachable.

### Ship the runtime in a download

```bash
python scripts/fetch-node-runtime.py
```

This downloads the pinned Node binary, verifies it against the official digest
committed in that script, and puts it in `.local/node-runtime/`. `build-server.py`
adds it and the worker to the bundle, and says which of the two is missing if a
build would ship without automations. It adds roughly 80 MiB to the installed
size and about 30 MiB to a download.

## Desktops

A desktop is a persistent workspace: a name, an appearance, and the desk's two
responsive boards. `desktop` and `phone` are two layouts of *one* workspace, not
two workspaces — that distinction is why `vela/desktops/` sits above
`vela/desk.py` rather than replacing it. (`vela/desktop.py`, singular, is the
Windows tray and is unrelated.)

| Location | Responsibility |
| --- | --- |
| `desktops/models.py` | What may be stored: ids, names, appearance references, bounds |
| `desktops/store.py` | `desktops.sqlite`: desktops, boards, appearance, wallpaper assets |
| `desktops/migration.py` | Turning the existing `desk.json` into Desktop 1, exactly once |
| `desktops/service.py` | The rules: board validation and repair, asset reference counting, deletion |
| `desktops/api.py` | `/api/desktops`, hub-authenticated like every other `/api` route |

Board geometry is not re-implemented. `vela/desk.py` still decides what a board
may contain: `validate_widgets` on the way in, `repair_widgets` on the way out.

### One desk, two names for it

`/api/desk`, the `desk` appearance keys in `/api/settings`, and `/api/wallpaper`
are the first desktop under their original names. They are aliases, not a second
store: same rows, same revision, same 409 with `X-Vela-Desk-Revision`. Keep it
that way — two writable copies of one board is how an arrangement gets lost.

Revisions are per concern. Renaming a desktop, arranging it and changing its
wallpaper each have their own, so two people doing unrelated things in the same
workspace both succeed.

### Migration

It runs once, on the way up, and is lossless: both boards, every widget id and
position, the chosen wallpaper and the uploaded image. `desk.json` and the old
`wallpaper.*` file are left where they are.

A widget whose type no longer exists is *not* dropped during migration —
unknown types are dropped when a board is read, which is where that decision has
always been. The order is deliberate: the image is copied and verified first,
outside any transaction, into a file named after its own SHA-256; then one
transaction writes the desktop, its boards, its appearance and the marker
together. A crash before the commit leaves an orphan file that the next run
either reuses or sweeps. It never leaves two Desktop 1s.

### Wallpaper assets

Uploaded images live in `desktop-assets/<sha256><ext>`. Addressing them by
content is what lets two desktops draw the same photo without two copies, and
stops one of them deleting it from under the other. `cleanup_assets()` removes
files nothing references and rows whose file is gone; it runs at start-up and
after anything that can drop a reference.

`desktops.sqlite` is in `BACKED_UP_FILES` because appearance used to live in
`settings.json` and a restore that stopped bringing the wallpaper back would be
a regression. The image files are not backed up — they never were — so a
restored desktop whose picture is missing falls back to a painted one.

### Views and windows

A **view** is what is open on a desktop. It is the identity that survives being
minimized, maximized, moved between panes and restored; the app process it talks
to has its own, shorter, life. Keeping those apart is the point: minimizing a
window must not end a session, and closing one must not stop an app another
desktop is also showing.

| Table | Holds |
| --- | --- |
| `desktop_views` | What is open: kind, app and installation, owner surface or URL, title, who opened it, and the small navigation state it may remember |
| `desktop_view_presentation` | Where the window is: bounds, restore bounds, minimized, stacking |
| `desktop_layout` | One arrangement per desktop: floating, maximized or split, its panes, the divider and the selected view |

Four kinds of view. `app` and `web` are things an agent can be pointed at;
`host` and `agent` are the owner's own controls and are marked
`agentViewable: false` at the service boundary rather than in every caller. A
`host` view names one of a closed list of surfaces — one that could name any
path would be a way to put the owner's dashboard, and its credentials, inside
something that is not the owner's dashboard.

**Installation binding.** An `app` view records the installation identity it
opened against, and `available` is computed by comparing that to the current one
on every read. That is deliberately not a hook at uninstall time: a hook is
something a future code path can forget to call, and a window pointing at a
replaced installation is exactly what must never be treated as still bound.

**Revisions.** Only `PUT /layout` carries one. Opening a window, moving it and
selecting it happen constantly, and charging them against the layout revision
would make every click conflict with a drag somebody else was finishing.

`web/src/desktops/window-state.js` is the geometry, with no React, no DOM and no
fetch in it — because it is the part that has to keep being right after someone
rotates a tablet, zooms to 200% or opens a layout saved on a monitor they no
longer own, and `tests/desktop-window-state.test.mjs` can ask it all of those
questions in milliseconds instead of only in a browser at one size.

`DesktopViewHost` draws the windows over the desk, `WindowFrame` is the chrome,
`AppWindow` is what goes inside an app window, and `useDesktopViews` keeps a
fast local copy of the server's records: a window follows the pointer at the
refresh rate and the write happens once, on a trailing edge, because a gesture
that emitted a request per frame would be a request storm.

`web/src/desktops/view-lifecycle.js` owns one app view's session and bridge and
the end of both. The full-screen app page and the desktop's windows share it;
two copies of "open a session, attach a bridge, revoke on the way out" would be
two places for the revoke to be forgotten.

### What an agent is allowed to change

Vela already had three kinds of caller — the owner at the dashboard, an app in
its iframe, an automation running a reviewed workflow. An agent is a fourth, and
deliberately none of the others. It is not the owner, because owner
authentication is what approves things and an agent approving its own effects
would make approval meaningless. It is not an ordinary app session either: that
one carries everything a manifest declares, for an hour, and an agent gets only
what one run on one desktop needs, for minutes.

| Location | Responsibility |
| --- | --- |
| `desktops/principals.py` | What an agent session is, and how any code holding a session asks whether a person or a run is behind it |
| `desktops/effects.py` | Every `/api/app` operation, classified; and the vocabulary for how an effect ended |
| `desktops/policy.py` | What a desktop allows: apps, sites, approval mode, granted actions, budgets |
| `desktops/grants.py` | What has actually been allowed, stored beside the app data it authorizes changes to |
| `desktops/gateway.py` | The coarse check, run on every app-scoped request before a handler sees it |

**Classification is a list, and the default is no.** `effects.OPERATIONS` maps
each `(method, path)` under `/api/app` to an operation and an effect class. A
route nobody classified has no class, so there is nothing to grant, so the
gateway refuses it. That is the point of the list being a list: a route added
without a thought about agents is refused rather than waved through.

**Grants live in `app-data.sqlite`, not in `desktops.sqlite`.** Not where they
conceptually belong — where they can be checked. A grant is read inside the
transaction that writes the effect it authorizes, so revoking it and committing
the effect contend for one SQLite write lock and one of them wins outright.
Storing them with the desktop would have meant two databases, two transactions
and a window between them, and "we check, then we write" is not a sentence worth
having here. `tests/test_agent_permissions.py` races the two a dozen times and
asserts the invariant: a refusal changed nothing, a success happened.

**Every service that can change something takes a `guard`.** `AppServices`,
`Actions`, `Widgets` and `Connections` are constructed with
`Desktops.effect_guard`. It returns `None` for a person and, for an agent, a
callable that service runs inside its own write transaction. That is why a
clicked Save in an agent's window follows the same rule as a tool call: both are
the same route, the same session and the same check. `AppStorage.write` and
`snapshot` grew an `authorize` hook for this.

The one boundary that cannot work that way is a connection: a request to another
service cannot be rolled back, so the check happens before dispatch and a lost
response stays `unknown` rather than becoming `failed`.

**Changing a policy revokes everything issued under it** — every grant row and
every agent session for that desktop. Narrowing what an agent may do has to take
effect now, not when something is next re-checked, and the only way to mean that
is to remove the authority rather than mark it stale. Deleting a desktop and
stopping a run do the same.

Grants name the installation identity and the manifest fingerprint they were
reviewed against, so updating or reinstalling an app does not hand the new one a
decision somebody made about the old one. A grant carrying a request digest
covers that exact request and nothing else.

### The dashboard side

`web/src/desktops/` owns which workspace the browser is looking at.

| Location | Responsibility |
| --- | --- |
| `desktopsApi.js` | The scoped routes. Every call names a desktop, so a caller cannot forget which one |
| `DesktopsProvider.jsx` | The list, the selection and the appearance, above the routes |
| `DesktopSwitcher.jsx` | The rail entry and its menu, plus create/rename/delete |
| `DesktopRoute.jsx` | `/desktops/:id` — selects that desktop and shows the ordinary desk |
| `AppsOverlay.jsx` | All apps, drawn over the current page rather than replacing it |

The selection lives in `localStorage`, not on the server. A phone and a laptop
are two viewers of one server, and one of them choosing Desktop 2 must not move
the other. It is re-resolved against the list on every load, so an id deleted
elsewhere falls back to the first desktop instead of showing an empty board.

The desk waits for its own board before drawing anything. Showing the seeded
default first and then rearranging it would be wrong on every desk but a brand
new one, and the board now takes one request longer to arrive because the page
has to know which desktop it is for.

All apps follows `SettingsProvider`: `/apps` still works as a link and a
bookmark, and the rail entry opens the overlay in place. While it is up, the
workspace behind is marked `inert` and `aria-hidden` — its own search field must
not be the second searchbox a screen reader finds — and the rail stays live
because it is how you leave.

## Agent desktop runtime

Agent desktops render their app views in a managed Chromium that Vela starts and
stops, in `scripts/browser-worker/`. The parts that exist today are the ones the
rest of the feature has to be able to rely on: the stdio protocol, the network
boundary and the browser session that applies it.

| Location | Responsibility |
| --- | --- |
| `scripts/browser-worker/src/protocol.mjs` | The versioned stdio envelope and the identities a command must carry |
| `scripts/browser-worker/src/network-policy.mjs` | What a desktop may reach: the Vela gateway and the owner's approved sites, nothing else |
| `scripts/browser-worker/src/session.mjs` | One desktop's browser: its context, its views and the policy applied to every transport |
| `tests/agent-boundary.test.mjs` | The boundary, checked against a real browser rather than a mocked policy |

### The supervised process

`vela/desktops/runtime.py` owns the worker's whole lifetime, following
`vela/automations/worker.py` — two workers that fail the same way are two
workers a maintainer only has to learn once. It starts hidden on Windows,
speaks the frozen NDJSON protocol over its own pipes, receives no Vela
credential and no network address, and is stopped when Vela stops.

Both ends watch. Vela sends a heartbeat; the worker exits on its own if Vela
goes quiet, because a force-killed server cannot send `shutdown` and a browser
running with nobody to stop it is the failure worth preventing. A worker that
dies takes its browsers with it and the desktops it held are gone — a browser
session cannot be recreated, and pretending otherwise would mean an agent
believing it is still looking at a page that is not there.

Images never travel on the control channel. `view.capture` writes a PNG named
after its own digest into a directory Vela passes on the command line and
returns the name; one oversized capture taking the whole protocol down with it
is the thing that avoids.

`GET /api/desktops/runtime` reports whether agent desktops can run here at all,
before any work is accepted, with a reason somebody can act on rather than the
word "unavailable".

### Set up the runtime

```bash
python scripts/setup-browser-worker.py
```

This installs the pinned `playwright-core` and the Chromium build that version
expects, then writes `scripts/browser-worker/provenance.json` recording both.
`--check` reports what is installed without changing anything; `--skip-browser`
installs the package alone and leaves the runtime unavailable. Downloading a
browser is a deliberate setup step, never something a running task does.

Sessions launch the full Chromium (`channel: 'chromium'`), not the headless
shell the runtime would otherwise pick: the agent's screen is the same screen a
human takes over, so it has to render through the same engine. Chromium's own
sandbox stays on — a platform that cannot run it is reported unavailable rather
than launched with the sandbox disabled.

### Where the boundary is enforced

A separate browser context per desktop separates cookies, storage and input. It
is not a sandbox for hostile code, so the network boundary is enforced
separately and in more than one place, because no single hook covers every
transport:

- `context.route` screens documents, subresources, redirects and `fetch`.
- `context.routeWebSocket` screens handshakes, which `route` does not see.
- A `framenavigated` guard catches a navigation that arrived some other way.
- Service workers are blocked, because a worker sits between the page and the
  screened network.
- `response.serverAddr()` is re-checked against the policy, because a URL cannot
  tell you that an approved hostname resolves to a private address.

Loopback is denied except for the one gateway origin, under the path prefixes
Vela publishes for it; the owner API is not one of them. The check understands
the spellings that hide a private address — `2130706433`, `0x7f000001`, `127.1`,
`[::ffff:127.0.0.1]`, `169.254.169.254` — because a denial that only matches
dotted quads is not a denial.

## Container build

The root `Dockerfile` builds the dashboard with Node.js 22 and packages it with
the Python server. No sibling repositories are needed. Build with:

```bash
docker build -t vela:local .
```

Run `python -m unittest discover -s tests` for container configuration and proxy
authentication regressions, then smoke-test the image with a disposable volume
and an HTTPS reverse proxy. Follow the [ServerKit settings](SERVER.md#serverkit),
publish port 7700 to loopback only, verify sign-in and the Library, and recreate
the container with the same test volume to verify persistence. Never use an
installed user's data for this check. The health endpoint alone does not verify
the proxy configuration. Native apps needing Node.js or other system packages
require extending the runtime image.

## Build a server download

From the activated environment, after building the dashboard:

```bash
pip install -r scripts/requirements-build.txt
python scripts/build-server.py
python scripts/test-server-bundle.py
```

The archive and SHA-256 file appear under `.local/releases/`. The bundle
contains the Python runtime, backend, SDK/schema snapshots and built dashboard.
`psutil` (BSD 3-Clause) is bundled for the desk's System and Volume widgets; it
is imported lazily and collected through an explicit `--hidden-import`, so
`python scripts/test-server-bundle.py` asserts `/api/system/metrics` reports
`available: true` rather than letting a bundle without it pass quietly.
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
macOS/Linux currently use portable archives. Code signing, macOS notarization
and background system services are not implemented.

Vela updates itself from these releases. A running server asks GitHub once a
day which release is newest, downloads the asset matching how it was installed,
verifies it against the published `.sha256` sidecar, backs itself up, and then
hands over to a small script that replaces the files and starts Vela again —
because a process cannot overwrite what it is running from. The Windows
installer path runs the new setup exe with the same silent flags
`scripts/test-windows-distribution.py` verifies; the portable and tarball paths
move the install directory aside as `.previous` and swap the new one in, which
is also what a rollback moves back. Source checkouts and containers are told to
update the way they actually update. `VELA_UPDATE_API` points the checker at a
local asset server, which is how both distribution checks exercise the path
without touching a real release.
