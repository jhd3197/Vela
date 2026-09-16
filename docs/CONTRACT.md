# Vela Core Contract

This document is the single source of truth for how the Vela backend, frontend,
and apps fit together. Backend and frontend are built against this contract.

## Overview

Vela (Virtual Environment for Local Apps) is an open-access, browser-based hub
for discovering, installing, and running apps **locally** on the user's machine.

- **Backend**: Python + FastAPI, serves the REST API and (in production) the built frontend.
- **Frontend**: React (Vite) SPA in `web/`, built to `web/dist/`, served by the backend at `/`.
- **Apps**: self-contained folders with an `app.json` manifest. Available apps ship in `apps/`;
  installed apps live in the user's data dir.

## Ports

- Backend API + static hosting: **7700**
- Vite dev server: 5173, with a proxy forwarding `/api` AND `/apps` to `http://localhost:7700`

## The Hub Is the Environment

The hub is not a control panel that kicks users to `localhost:PORT`. Apps live
inside the hub. Legacy v1 views share its origin; v2 embedded documents have
opaque sandbox origins:

- **Process apps**: when running, the hub **reverse-proxies** `/apps/{id}/` â†’
  `http://127.0.0.1:{port}/`. Paths and query strings are forwarded. Hub cookies,
  authorization, hop-by-hop headers and upstream Set-Cookie are stripped;
  responses stream back. When the app is not running, `/apps/{id}/` returns
  409 `{"detail": "app {id} is not running"}` (process apps) or
  404 `{"detail": "app {id} is not installed"}` (web apps not yet installed).
- **Web apps**: served statically at `/apps/{id}/` (unchanged from v1).
- **Embedded `url` is `/apps/{id}/`** â€” launch responses, app
  summaries, status. The underlying `port` still appears in status/launch for
  debugging, but the UI never sends users to it directly. External views report
  their HTTPS destination; headless views report `null`.
- **Embedded view**: the hub shell renders apps at frontend route `/app/{id}`
  (singular). Presentation follows `view.chrome`, with a host-owned return control
  outside the iframe. Stop is separate from closing the view.

## Versioned app contract

### Host-owned connected web apps

The hub can register an existing HTTPS service without importing a package.
These registrations live in the `connected_web_apps` table in `app-data.sqlite`.
They are **not manifest v2 entries**: the portable schema and SDK remain unchanged.
IDs use `web--<uuid hex>`, which cannot collide with a valid manifest's slug.

| Endpoint                                                           | Authorization and behavior                                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| `POST /api/web-apps`                                               | Hub bearer; `{name, url, color?}` creates a registration (201)            |
| `PUT /api/web-apps/{id}`                                           | Hub bearer; `{name, url, color?, revision}` updates the reviewed revision |
| `DELETE /api/web-apps/{id}`                                        | Hub bearer; `{revision}` removes only the registration                    |
| `GET /api/apps`, `GET /api/apps/{id}`, `GET /api/apps/{id}/status` | Hub bearer; includes connected app summaries                              |

Stale update/removal revisions return 409; missing records return 404. Names are
limited to 80 characters, addresses to 2048, icon colors to six-digit hex and
registrations to 200. Validation allows unambiguous HTTPS URLs without credentials;
the hostname must differ from the hub. The browser rechecks against its actual
hostname, including when the hub address changes. No server-side URL fetch, DNS
lookup or proxy is performed.

Summaries have `kind: "connected-web"`, `schemaVersion: null`, `runtime: "connected"`,
`view: {surface: "connected", chrome: "hub", url}`, `installed: true`,
`running: false` and no capabilities. `installed` means the connection is saved;
`running` does not claim upstream availability. Engine installed counts include
registrations; running counts do not. Package lifecycle and app-session endpoints
do not accept these records, and the assistant has no upstream data access.

The host renders a script-free `srcdoc` wrapper with a restrictive CSP whose
`frame-src` is exactly the saved origin. Its child iframe preserves the service's
origin, with scripts, forms, downloads and sandboxed popups permitted. Top-level
navigation and popup sandbox escape are not allowed. Cross-origin redirects are
blocked. No bridge, app bearer or hub bearer is supplied to either frame. Host
referrers are suppressed; subsequent navigation within the service uses its own
referrer policy. Upstream frame policies and browser cookie policies still apply.
The host always offers browser fallback; iframe load events cannot reliably prove
that an upstream service is available or successfully rendered. These summaries
are host-owned, so the hub renders them inside its own rail and contextual
header (`chrome: "hub"`), with edit, reload and open-in-browser controls in that
header. This presentation choice never reaches into the service's own document.

### Package manifests

Unversioned manifests and `schemaVersion: 1` normalize to the legacy runtime
rules and `{surface: "embedded", chrome: "compact"}`. The original manifest
format below describes that v1 input. V2 input is validated against
[runtime manifest schema](../vela/assets/manifest-v2.schema.json) (canonical source: `vela-contracts`).
Unknown v2 fields and unsupported versions fail closed. Installed manifests
are authoritative; an invalid installed manifest cannot fall back to catalog
metadata. All six existing app IDs remain unchanged.

The [Chat Studio fixture](../tests/fixtures/chat-fixture/app.json) is a complete v2 example.
V2 declares `compatibility.bridge: 1`, `runtime.static.entry` and/or
`runtime.process` (`trust: "trusted-native"` and OS-specific commands), plus
`view.surface`. A required process controls support/readiness even with static
assets present. Container runtimes and arbitrary wrapper protocols are not
supported. External and headless entries may declare an empty runtime object.

| Field                   | Implemented values                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| `view.surface`          | `embedded`, `external` (HTTPS `url` required), `none`                                          |
| `view.chrome`           | Embedded only: `hub`, `compact` (default), `seamless`                                          |
| `view.appearance`       | Optional: `light`, `dark`, `auto` (default); the theme of the app's window title bar           |
| `capabilities.required` | `storage`, `connections`, `actions`, `widgets`; any unknown required grant blocks validation   |
| `capabilities.optional` | Known capabilities granted; others reported in `unavailableCapabilities`                       |
| `data.schemaVersion`    | Positive integer, required when requesting storage                                             |
| `data.quotaBytes`       | 1 KiBâ€“10 MiB; default 1 MiB                                                                  |
| `widgets`               | Up to 4 `{id, name, layout, size}` desk widget declarations; requires the `widgets` capability |

`hub` and `compact` both give the app a **title bar** of its own beside the
rail (see the app-window bullet under Frontend Expectations); `seamless` renders
no bars and retains a 44px-or-larger Vela menu outside the app. The seamless
menu can return, close, show the compact bar (which is that title bar), pin the
app, add its widget to the desk, reach App settings, or stop a managed process.
`view.appearance: "dark"` draws the title bar in the dark tokens whatever the
hub theme is, so a dark app is not topped by a light strip; it does not change
the `theme` reported to the app. A user compact preference persists per app.
Failed loading retains host controls.
The host reports viewport dimensions, insets and its control rectangle to the
SDK; apps should avoid that rectangle. `viewport.width`/`height` are the frame's
own box, `visualHeight` is how much of that box is on screen, and `insets` are
the parts of the frame the browser is not showing — browser chrome, a device
safe area or an open keyboard — expressed in the frame's own coordinates, not
the host's. A frame the host has already sized to the visible area therefore
reports zero insets, so an app never subtracts the same keyboard twice. Inside a
frame `env(safe-area-inset-*)` is zero and the frame's visual viewport describes
the frame, so these values are the only source. Closing never implies stopping.
Unsaved work supports save/discard/cancel and a save timeout. Native page
unload uses the browser's confirmation.

Every v2 embedded document uses `sandbox="allow-scripts"` and response CSP
`sandbox allow-scripts`, without `allow-same-origin`. Each document has an
opaque origin. It cannot read the hub DOM, localStorage, cookies, or APIs.
Direct fetches, native form submission, service workers and nested frames are
disabled. Public static assets permit CORS for loading inside opaque views;
authenticated APIs do not. The host bridge checks message source, `null`
origin, protocol version and a per-view random nonce. It allows only declared
storage, connection, action and navigation operations; a caller's app ID is never used as identity.

The SDK package (`vela-sdk`) exposes `Vela.ready`,
`storage.read/write`, context updates, navigation and unsaved-work reporting.
`_vela/sdk.js` is served by the engine beneath the installed app URL. Runtime
and SDK package versions are separate from manifest schema version 2 and
bridge protocol 1. No package has been published.

### Authentication and app storage endpoints

The hub obtains a process-lifetime bearer from `GET /api/session` with
`X-Vela-Bootstrap: 1`. Bootstrap accepts only a loopback peer and loopback Host,
same-origin requests (plus explicit localhost Vite origins on 5173), and rejects
opaque/cross-site origins. This default mode is a **local-user trust boundary**.
Opt-in HTTPS uses the password login described below. The frontend retains the token in memory and refreshes on 401.
The token never enters app frames, URLs, localStorage or service-worker caches.

All hub `/api/*` endpoints require the hub bearer except health, session
bootstrap, login/logout and public app icons. The hub can issue an app session with
`POST /api/apps/{id}/session` for an installed v2 embedded app. The host keeps
that one-hour bearer and uses it for these endpoints:

| Endpoint                  | Result                                                                               |
| ------------------------- | ------------------------------------------------------------------------------------ |
| `GET /api/app/storage`    | `{value, revision, schemaVersion}`; missing document has `null` value and revision 0 |
| `PUT /api/app/storage`    | `{value, revision}` body; returns the saved document at revision + 1                 |
| `DELETE /api/app/session` | Revokes that bearer                                                                  |

An app bearer cannot access hub settings, app catalogs, lifecycle operations,
or another app's session. Extra write fields are rejected. Identity and grants
come from the server-side session. Storage requires the `storage` grant;
conflicts and schema mismatches return 409, quota overflow 413, missing grants
403, and invalid/revoked/expired tokens 401. Clients must explicitly reconcile
conflicts; unconditional writes are not supported.

SQLite transactions persist identities and documents separately from installed
code in `app-data.sqlite`. Uninstall revokes sessions and deactivates identity,
while retaining app data; reinstall issues a fresh identity. Backups snapshot
the database with SQLite's backup API. The engine provides the legacy import
and app restore operations below. Executable schema migrations, offline
synchronization and whole-engine live restore remain later work.

**Legacy v1 apps remain trusted same-origin code** pending deliberate browser
data migration. Native runtimes execute with the user's privileges even when
their UI uses the v2 browser sandbox. See [the security boundaries](../SECURITY.md#current-boundaries).

### App data, connections and HTTPS

`data.schema` optionally names an app-local JSON Schema used on writes, imports
and restores. External schema references are rejected. `data.legacy` declares
`key` (within `vela.{id}.`), `field` and an app-local `schema`. The hub reads only
that browser key after a user action, or accepts a recovery JSON file. Import
accepts an array of uniquely identified records, deduplicates identical IDs and
values, and gives differing records with the same ID new IDs. The engine retains
the original import and its digest atomically with the write. Repeat imports of
the same copy do not duplicate records. Browser data is never deleted.

| Endpoint                                       | Authorization and behavior                                                                      |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `POST /api/apps/{id}/migration/preview`        | Hub; `{value, revision}`; reports current revision, additions and conflicts                     |
| `POST /api/apps/{id}/migration`                | Hub; same body, compare-and-swap commit with recovery copy                                      |
| `POST /api/apps/{id}/upgrade`                  | Hub; one-time bundled static v1 to v2 replacement; archives the previous package                |
| `GET /api/app/storage/snapshots`               | App storage grant; own backup metadata                                                          |
| `POST /api/app/storage/snapshots`              | App storage grant; snapshot current saved document                                              |
| `POST /api/app/storage/snapshots/{id}/restore` | App storage grant; `{revision}`; validates schema, snapshots current data, writes next revision |
| `GET/PUT/DELETE /api/apps/{id}/connection`     | Hub; inspect, test/bind `{endpoint}`, disconnect                                                |
| `GET /api/app/connection`                      | App connections grant; own binding status                                                       |
| `POST /api/app/connection/invoke`              | App connections grant; `{operation, payload}` within manifest allowlist                         |

App snapshots retain the latest 20 entries; migration originals are retained
separately. Uninstall preserves app documents and snapshots but removes the
connection binding and revokes sessions. App exports download the saved document
with its revision/schema metadata. They are portable recovery copies; uploading
arbitrary exports as live document replacements is not implemented.

The only connection provider is `ollama`; `connection.operations` can declare
`models.list`, `models.show`, and `server.version`. URLs are bound by the hub,
restricted to HTTP(S) loopback/private LAN IPs, and never supplied by the frame.
Named calls use fixed API paths, a five-second deadline and a 1 MiB response cap.
Redirects and credential-bearing URLs are rejected. No hub credentials are sent
upstream. The wrapper never starts, stops, pulls or deletes Ollama or its models.

`python -m vela --set-password` stores a salted scrypt password hash in
`access.json`. LAN hosting requires `--host`, TLS `--cert`/`--key`, and an exact
HTTPS `--origin`. `POST /api/login` accepts `{password}` and requires the same
origin and `X-Vela-Bootstrap: 1`. Five failed attempts per IP within ten minutes
are throttled. The login creates a 12-hour in-memory session and a Secure,
HttpOnly, SameSite=Strict `__Host-vela-session` cookie. The cookie only bootstraps
the in-memory bearer; authenticated API calls still require bearer headers.
`POST /api/logout` revokes that client and its app sessions. Restart revokes all
sessions. There are no separate user accounts: clients share the same engine.
TLS terminates at Vela; forwarded proxy headers are disabled. See
[the server network setup guide](SERVER.md#another-device).

### Explicitly granted app actions

Apps with the `actions` capability can declare `actionRequests` entries of
`{app, action}`. Providers also require `storage` and declare unique `actions`
with `id`, `title`, `effect: "write"`, app-local `inputSchema`/`outputSchema`, and
a declarative `handler`. The implemented handler is `storage.append`: `field`
selects the provider's array, `initial` defines its empty document, and `fields`
lists input fields copied to the new record. The engine supplies `id` and
`updated`; complete data and output validation precede the database commit.
Input cannot select a raw database, field, record ID or handler.

Installing a caller does not authorize another app's action. The host lists its
exact declared requests. A grant binds source/target installation identities,
action ID and both manifest fingerprints. The grant request must include the
fingerprints reviewed by the user, rejecting a changed review. Updates and
reinstalls require a fresh grant; revocation takes effect before subsequent calls.

| Endpoint                             | Authorization / behavior                                                |
| ------------------------------------ | ----------------------------------------------------------------------- |
| `GET /api/apps/{id}/actions`         | Hub; requests, availability, permission state and reviewed fingerprints |
| `PUT /api/apps/{id}/actions/grant`   | Hub; `{app, action, allow, sourceContract, targetContract}`             |
| `GET /api/apps/{id}/actions/history` | Hub; latest 50 incoming/outgoing execution metadata entries             |
| `GET /api/app/actions`               | App session; own declared action requests                               |
| `POST /api/app/actions/invoke`       | App session; `{app, action, input, key}` with current explicit grant    |

The SDK exposes `Vela.actions.list()` and
`Vela.actions.invoke(app, action, input, key)`. Inputs are capped at 32 KiB;
admitted calls check a two-second elapsed deadline before writing. Success writes
the target document, receipt and activity entry in one SQLite transaction.
Receipts are keyed by stable source/target app IDs, action ID and retry key so
they follow retained app data across reinstalls. Authorization still checks the
current installation grant before replaying a receipt. Repeating exact input
returns the original result; different input with the same key returns 409.
Replays do not recreate records deleted or removed by rollback. Limit: 10,000
retained receipts per source app, with an explicit 429 at capacity. Activity
retains the latest 200 entries per source app and excludes input/output content.

Meals and Notes now use SDK storage. Notes declares `create-note`; Meals declares
that request and saves an outbox key/input before sending a recipe or week plan.
An interrupted send can be retried after reload. Notes uses revision checks so
its stale editor cannot erase a record appended by an action. Legacy Notes uses
the record import contract. Meals declares `data.legacyBundle` (`keys`, `field`,
`schema`, `initial`); the hub validates its namespaced browser keys and retains
each distinct bundle as an imported copy before the user applies it inside Meals.

This is a synchronous app-action broker. It runs no scripts, containers,
arbitrary network calls or native code. See
[the Meals and Notes guide](APPS.md#meals-and-notes).

### App-provided desk widgets

An app can offer the desk a small summary of itself. It never renders there:
the app publishes JSON and the host draws it with its own components, always
labelled with the app it came from. No app code runs on the desk.

Declaration lives in the manifest, so the user sees it at install:

```json
"capabilities": { "optional": ["widgets"] },
"widgets": [
  { "id": "sync", "name": "Sync", "layout": "stat", "size": "m" },
  { "id": "queued", "name": "Queued changes", "layout": "list", "size": "l" }
]
```

`id` matches `^[a-z][a-z0-9-]{0,31}$` and is unique within the app; at most four
widgets per app; `layout` is `stat`, `progress`, `list` or `actions`; `size` is
`s` (1x1), `m` (2x1) or `l` (2x2). Declaring `widgets` without the capability
fails validation. The install review shows the capability as "Show summaries on
your desk" and names the declared widgets.

The published summary is a flat JSON object of at most 4 KB. Every field is
optional — `{}` is valid and means "nothing to report yet":

```json
{
  "value": "73",
  "unit": "changes",
  "delta": "+12",
  "caption": "queued since 02:14",
  "progress": 32,
  "rows": [{ "label": "…", "detail": "…" }],
  "actions": [{ "action": "sync", "label": "Sync now" }],
  "attention": true,
  "expiresAt": "2026-09-16T02:14:00Z"
}
```

`value`, `unit`, `delta`, `caption` and every row and action label are text of at
most 200 characters; `progress` is 0–100; `rows` holds at most eight
`{label, detail}` pairs; `actions` at most three, each naming one of the app's
own actions; `attention` is the boolean the rail's dot reads; `expiresAt` is an
ISO 8601 timestamp after which the desk marks the summary stale. Nothing nests
further and nothing is rendered as markup.

| Endpoint                          | Authorization / behavior                                                                                          |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `PUT /api/app/widgets/{widgetId}` | App session with the `widgets` grant; 403 without it, 422 for an undeclared id or an invalid field, 413 over 4 KB |
| `GET /api/apps/{id}/widgets`      | Hub; every widget this app declares, each with its latest summary or `null`                                       |
| `GET /api/widgets`                | Hub; the same for every installed app, plus `grantedActions` where a summary offers actions                       |

The SDK exposes `Vela.widgets.publish(id, summary)`. Summaries are stored in
`app-data.sqlite` keyed by app id, replaced rather than accumulated, and deleted
when the app is uninstalled — unlike app data, which survives for a reinstall.
The desk merges one widget type per declared widget, named `<appId>:<widgetId>`,
renders it inside the app card chrome, shows "as of …" once a summary is past
`expiresAt` or older than an hour, and "Open <app> to update" when there is no
summary yet. An action is offered only when it appears in that app's granted
actions; choosing it opens the app, because the engine has no host-initiated
action path and the desk does not act for the user. Notes and Health are the
first apps to use this.

An automation is the second kind of caller. It holds no app session and cannot
declare requests in a manifest, so it authorizes differently while sharing the
same transactional write: a grant binds one workflow to one action, to the inputs
the user reviewed, to the target's manifest fingerprint and to its installation
identity. Editing the granted step, updating the app or reinstalling it ends the
grant, and authorization is rechecked inside the write transaction so a
revocation stops a call already in flight. Automation receipts and activity
entries use a caller id of `automation:{workflowId}`, which cannot collide with an
app id. See [the automations guide](AUTOMATIONS.md).

### Automation contract

Automations are hub-authenticated; an app session cannot reach any of their
endpoints. Vela owns saved workflows, their revisions, permissions, schedules,
runs and events in `automations.sqlite`.

- The document format is Tramo's `WorkflowDoc` (`version: 1`). Vela validates it
  on save, import, activation and dispatch against one catalog of vetted step
  types, and refuses anything else. A newer document version is refused rather
  than downgraded.
- A draft may be incomplete. Activating or running additionally requires exactly
  one trigger at the root, every step reachable from it, every required setting
  filled in, and every app action allowed.
- Activation freezes the current revision. A run pins the revision, catalog
  version and runtime it executed against; editing the draft afterwards never
  changes a queued, running or waiting run.
- Execution happens in a private Node worker on a versioned, bounded stdio
  protocol. It receives the approved revision and nothing else — no hub token, no
  app session, no data directory, no listening port — and asks Vela to perform
  every side effect. It exits on its own if Vela stops talking to it.
- Step types that evaluate configuration as code, or reach the network directly,
  are excluded from the executor registry. Both the server and the worker refuse
  an unknown step type instead of skipping it.
- Vela is the only scheduler. One due moment is claimed once; occurrences that
  pass while Vela is not running are skipped and reported, never replayed as a
  burst. Webhook ingress authenticates with a per-workflow secret, is limited to
  a 64 KiB JSON body, refuses an identical replay, and changes nothing about
  Vela's bind, TLS or origin policy.

| Endpoint                                                                                | Authorization / behavior                                                                     |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `GET/POST /api/automations`                                                             | Hub; list with live state and statistics, or create                                          |
| `GET /api/automations/catalog`                                                          | Hub; vetted step definitions plus installed app actions                                      |
| `GET /api/automations/status`                                                           | Hub; runtime availability and the notification destination                                   |
| `GET/PUT/PATCH/DELETE /api/automations/{id}`                                            | Hub; `PUT` takes the expected revision and returns 409 on conflict                           |
| `POST /api/automations/{id}/activate                                                    | pause                                                                                        | archive | restore | duplicate` | Hub; activation requires an executable revision and current grants |
| `GET /api/automations/{id}/export`, `POST /api/automations/import`                      | Hub; steps only — never permissions, schedules or secrets                                    |
| `PUT /api/automations/{id}/grants`                                                      | Hub; `{app, action, allow, requestContract, targetContract}`, rejecting a changed review     |
| `POST /api/automations/{id}/runs`                                                       | Hub; returns a run id immediately                                                            |
| `POST /api/automations/{id}/webhook`                                                    | Hub; issues a new address and secret, shown once                                             |
| `GET /api/automations/runs`, `/runs/{runId}`, `/runs/{runId}/events`                    | Hub; durable history and ordered events                                                      |
| `POST /api/automations/runs/{runId}/cancel`                                             | Hub; stops future steps, never claims to undo finished ones                                  |
| `GET /api/automations/approvals`, `POST /api/automations/runs/{runId}/approvals/{gate}` | Hub; durable pending state and authenticated decisions                                       |
| `POST /api/automations/hooks/{tokenId}`                                                 | Per-workflow secret in `X-Vela-Automation-Secret`; starts that one workflow and nothing else |

### Release contract

See [the app guide](APPS.md#install-and-update) for installation and rollback.
New static app releases can be installed from a folder, uploaded ZIP or pinned
catalog without rebuilding Vela. Updates and rollback are explicit reviewed
actions; rollback restores the matching data checkpoint with a new revision.
Only retained legacy packages can be restored as v1. New release imports require
v2 and do not execute native processes or migration scripts. Existing trusted
bundled process apps keep their lifecycle workflow.

`data.migrations` is an optional array of `{from, to, file}` declarations. A
schema-changing update needs exactly one direct migration from the stored schema
to its target. Files contain bounded object-key `add`, `replace`, `remove` patches;
the engine validates resulting data with the candidate's schema before activation.
General executable migrations and multi-step graph resolution are unsupported.

There is no default catalog in a standalone hub. Health source is in the sibling
`vela-health/health`, with stable ID `health`. Set `VELA_CATALOG` to select
the catalog path/HTTPS URL. HTTPS indexes require `VELA_CATALOG_SHA256` as the
operator's trust pin. App author/publisher labels alone do not prove identity.

| Endpoint                             | Hub-only operation                                                 |
| ------------------------------------ | ------------------------------------------------------------------ |
| `GET /api/catalog`                   | Source, cached/error status and pinned releases                    |
| `POST /api/catalog/refresh`          | Keep last good index on failure                                    |
| `POST /api/releases/prepare`         | `{folder}` or `{app_id}`; optional `{app_id, rollback: releaseId}` |
| `POST /api/releases/upload`          | Raw ZIP body, at most 32 MiB; returns staged review                |
| `POST /api/releases/{review}/commit` | Exact reviewed `{capabilities, operations}` approval               |
| `DELETE /api/releases/{review}`      | Cancel and remove staged files                                     |
| `GET /api/apps/{id}/releases`        | Release history and retained previous versions                     |

Reviews last 20 minutes and pin staged bytes, installed bytes and data revision.
Only increasing app versions are accepted as updates; earlier versions require
rollback. New installation identities, app data, migration markers and release
records share one SQLite transaction. A journal coordinates installed-directory
swaps with that transaction. Startup recovers an uncommitted swap before serving
requests. App tokens are revoked on release changes. Pending reviews expire
and do not survive a restart. Releases/checkpoints are retained until explicitly
managed by the operator; no automatic release-history pruning is implemented.

### Engine API details

`GET /api/engine` â€” the "Local Engine" status powering the System UI:

```json
{
  "status": "running",
  "engine": "local",
  "endpoint": "http://127.0.0.1:7700",
  "apps_installed": 2,
  "apps_running": 1,
  "storage_bytes": 1234567,
  "data_dir": "/Users/juan/.vela"
}
```

`storage_bytes` = total size of the data dir (installed apps + logs + state).
`endpoint` is the local engine address as configured â€” currently hardcoded to
`http://127.0.0.1:7700`.

### Updates API details

Hub session only.

- `GET /api/updates` — what Vela currently believes, with no request made:
  `{ current, latest, available, notes, asset, checksum, capability, checkedAt,
  error, check, mode, hour }`.
- `POST /api/updates/check` — asks now, bypassing the six-hour cache. With the
  preference off it makes **no request** and answers `{ skipped: "off" }`.

The request is a single anonymous `GET` to
`https://api.github.com/repos/jhd3197/vela/releases/latest` with
`Accept: application/vnd.github+json`, no authentication and no body. Nothing
identifying the server is sent. `VELA_UPDATE_API` overrides the address, which
is how the tests and the distribution checks drive it without touching GitHub.
The answer is cached in `<data_dir>/updates/latest.json` for six hours; a check
that fails keeps the last good answer rather than blanking it, because being
offline is not evidence that there is no update.

`capability` says what this copy could do about an update: `installer` (a
Windows Inno Setup install, detected by the uninstall registry key or
`unins000.exe` beside the exe), `portable` (an unzipped Windows folder),
`tarball` (macOS/Linux), `source` (a checkout) or `container`. `asset` is the
matching release file for this capability and architecture, and `checksum` its
`.sha256` sidecar; both are `null` for `source` and `container`.

Preferences live under the `updates` settings key as
`{ check, mode, hour }`, validated on `PATCH /api/settings`. `check` defaults
to true and `mode` to `notify` — installing automatically is opt-in. The
scheduler checks two minutes after startup and then daily, publishing one
`kind: 'update'` notification per new release. `GET /api/doctor` carries the
same answer under `update` so the desk reads it with the health checks rather
than making a second request.

### Backups API details

Hub session only.

- `GET /api/backups` — `{ backups: [{ name, size, created_at, safety }] }`,
  newest first. `safety` marks a copy taken automatically before a restore.
- `POST /api/backups` (`201`) — creates one. Two in the same second get a
  `-2`, `-3` … suffix rather than failing.
- `POST /api/backups/{name}/verify` — the restore drill, in an isolated temp
  directory. Live files are never touched.
- `GET /api/backups/stats` —
  `{ count, totalSize, lastSuccessAt, lastName, keep, schedule }`, where
  `schedule` is `{ enabled, time, keep, timezone, nextRunAt, error? }`.
- `POST /api/backups/{name}/restore` — requires `X-Vela-Confirm: restore`;
  without it the answer is `428` and nothing changes. Returns
  `{ name, restored, safety, stopped, restarted, failedToRestart, restored_at }`.
  A backup that fails its drill is refused with `409` before anything is
  touched.

The schedule is stored under the `backups` settings key as
`{ schedule: { enabled, time, keep } }` and validated on `PATCH /api/settings`
(`422` for a bad time or an out-of-range `keep`). It runs daily at `time` in the
server's own timezone, using the same arithmetic as automation schedules, so a
clock change behaves identically for both. There is no catch-up: a run missed
while Vela was not running does not happen later.

A restore, in order: verify; stop every running *process* app; create a
`pre-restore-<timestamp>` backup; replace `state.json`, `settings.json`,
`app-data.sqlite` and the installed `app.json` manifests; start again the apps
that were stopped. A failure before the replace step leaves everything as it
was. Logs, wallpapers and chat history are never restored or deleted. Retention
counts ordinary backups against `keep` and safety copies separately, so backups
taken after a restore cannot push out the copy that restore made.

### Errors and support bundle API details

Hub session only. Nothing here leaves the computer.

- `GET /api/errors?source=&resolved=&search=&page=` —
  `{ errors: [...], total, page, pageSize }`, newest first. `source` is
  `server`, `dashboard` or `app`; `resolved` is `true`/`false`.
- `GET /api/errors/stats` — `{ unresolved, lastDay, total, bySource }`.
- `POST /api/errors/client` — the dashboard reporting its own failure:
  `{ message, type?, stack?, url? }`. Answers `202` with
  `{ recorded }`; over 20 reports a minute the report is accepted and dropped,
  so a render loop that throws cannot fill the database.
- `POST /api/errors/{id}/resolve` with `{ resolved }`, and
  `DELETE /api/errors/{id}` (`204`).
- `GET /api/support-bundle`, `POST /api/support-bundle` (`201`, returns
  `{ name, size, created_at }`), `GET /api/support-bundle/{name}` (the zip).

An error is `{ id, fingerprint, source, type, message, traceback, endpoint,
count, firstSeen, lastSeen, resolved }`. The fingerprint is
`sha256(source|type|message[:200]|endpoint)`: a repeat of an *unresolved*
failure raises its `count` instead of adding a row, while a repeat of a
resolved one starts a new row, because that is news. Retention is 500 rows or
30 days, whichever comes first.

Unhandled server exceptions are recorded by an exception handler that re-raises,
so the caller still gets the `500` it would have got. Ordinary `HTTPException`
answers (a `404`, a `422`) are not errors and are not recorded. App frames are
not hooked: an app's errors are the app's.

A support bundle contains `README.txt`, `meta.json`, `settings.json`,
`doctor.json`, `apps.json`, `desk.json`, `errors.json`, `automations.json` and
`logs/<name>` (the last 500 lines of each). Settings values whose key matches
`token|secret|pass|key|credential|authorization` are replaced, and all free text
is scrubbed of `Bearer`/`Basic` tokens, bare JWTs and credential assignments.
App storage, chat history, wallpapers and the access password are never
collected. Bundles older than 7 days are pruned. See `SECURITY.md`.

### Health API details

Hub session only. Reading never starts a sweep, so opening a page costs
nothing; `POST /api/doctor/run` is the deliberate action.

- `GET /api/doctor` — the last sweep:
  `{ checks: [...], ranAt, summary }`. Before the first sweep in this process,
  `checks` is empty and `ranAt` is `null`.
- `POST /api/doctor/run` — runs every check and returns the same shape. Checks
  run together with a per-check timeout (5 s) inside one wall-clock budget
  (15 s); a check that raises or overruns becomes one `warn` rather than
  ending the sweep.
- `POST /api/doctor/{key}/repair` — `{ ok, detail, check }`, where `check` is
  that check re-run afterwards, so a caller updates one row without a second
  sweep. Recorded in `audit.log`. An unknown or unrepairable key is `422`.

A check is `{ key, title, status, detail, repairable, ranAt }` with `status`
one of `ok`, `warn`, `fail` or `skipped`. `skipped` means the check does not
apply to this server (no HTTPS, no catalog, no chat model) and is excluded
from the summary's counts. `repairable` is true only where a repair exists
*and* there is something to repair. Failing checks sort first.

The thirteen checks are `data-dir`, `port`, `certificate`, `stale-apps`,
`orphans`, `worker`, `node`, `psutil`, `catalog`, `ollama`, `backups`,
`settings` and `update`. `stale-apps`, `orphans` and `settings` have repairs;
`orphans` creates a backup before it removes anything, and removes nothing if
that backup fails. The scheduler sweeps about a minute after startup and then
daily, publishing one notification (`kind: 'health'`) per newly failing check
and re-arming only after that check passes again. Nothing leaves the computer:
every check reads local state.

### Logs API details

All four routes need the hub session and refuse anything that is not a plain
file name inside the data directory's `logs/`. "Show developer tools" decides
what the dashboard shows, never what a request may do, so it is not a
server-side check.

- `GET /api/logs` — `{ "logs": [{ name, kind, size, modified, base, rotated }] }`.
  `kind` is `server`, `audit`, `worker` or `app`; `base` is the log a rotated
  file belongs to (`server.log` for `server.log.1`).
- `GET /api/logs/{name}?lines=&from_end=&pattern=` —
  `{ name, lines: [...], total, truncated }`. Without `pattern` this tails
  (`from_end=true`, the default) or heads the file. With one it searches: a
  case-insensitive substring, or a regular expression when the pattern is
  wrapped in `/…/`. `total` counts every line or every match, so `truncated`
  says whether more exist than were returned. At most 5000 lines per request.
- `GET /api/logs/{name}/download` — the whole file as `text/plain`, as an
  attachment. It carries the hub token like any other call, so the dashboard
  fetches it rather than linking to it.
- `DELETE /api/logs/{name}` — truncates the file in place, so the running
  handler keeps writing to it, and records the action in `audit.log`. Requires
  `X-Vela-Confirm: clear`; without the header the answer is `428` and nothing
  is deleted. Rotated copies are not touched.

Vela writes `server.log` (2 MB × 5, shared by the console server and the tray),
`audit.log` (install, uninstall, launch, stop, update, backup, restore, repair
and log clearing, each with `actor=local` or `actor=remote`), and one
`<app-id>.log` per process app. `httpx` and `httpcore` log at `WARNING`, so a
request line no longer appears for every poll.

## Data Directory

Default: `~/.vela/` (overridable with env var `VELA_DATA_DIR`).

```
~/.vela/
  installed/          # one folder per installed app (copied from apps/)
  state.json          # { "<app-id>": { "pid": int, "port": int, "pid_ctime": int|null, "started_at": iso8601 } }
  settings.json       # hub settings (theme, chat model, ntfy config â€” see Settings)
  app-data.sqlite     # installation identities and app-scoped revisioned JSON
  logs/<app-id>.log   # stdout/stderr of running apps
  backups/<YYYYMMDD-HHMMSS>/   # hub backups (state, settings, installed manifests)
```

`pid_ctime` is a process creation-time token recorded at launch (Windows
`GetProcessTimes` FILETIME int; Linux `/proc/<pid>/stat` starttime; `null` on
macOS, where the platform provides no token). Liveness = PID alive AND ctime
match, so a reused PID can never impersonate the original process; where the
token is `null`, liveness degrades to a bare PID check.

## App Manifest (`app.json`)

Every app is a folder containing `app.json`:

```json
{
  "id": "hello-vela",
  "name": "Hello Vela",
  "version": "1.0.0",
  "description": "A tiny static page served locally.",
  "icon": "icon.svg",
  "color": "#8b5cf6",
  "category": "getting-started",
  "author": "Vela",
  "platforms": {
    "posix": { "run": "python3 -m http.server {port}", "port": 8801 },
    "windows": { "run": "python -m http.server {port}", "port": 8801 },
    "web": { "entry": "index.html" }
  }
}
```

Rules:

- `id`: lowercase slug, unique, matches the folder name.
- `color`: optional hex accent for the app's icon tile in the hub UI; surfaced as
  `"color"` in app summaries. Defaults to the hub blurple (`#9184d9`) when absent.
- `platforms` keys: `posix` (macOS/Linux), `windows`, `android` (may be `null` = unsupported), `web` (PWA â€” see below).
- `run`: shell command; `{port}` is substituted with the manifest `port` (or a free port if taken).
- `port`: preferred local port the app listens on. Optional â€” omit for apps that don't serve HTTP.
- The app process runs with its working directory set to its installed folder.
- **Runtime shape**: `runtimes` lists what the manifest can do on the current
  platform â€” `"web"` first when a `web` entry exists, plus `"process"` when
  there's a non-null entry for the current platform (a subset of
  `["web", "process"]`, web-first). `runtime` is the one the hub actually uses:
  `"web"` wins when both exist, else `"process"`, else `null` (unsupported).
  `hello-vela` ships both `web` and `posix`/`windows` entries, so it resolves to
  `runtimes: ["web", "process"]`, `runtime: "web"`. Both fields appear in app
  summaries and in launch/status responses.

## Web Apps / PWAs (the iPhone & Safari path)

iOS cannot install native apps from a browser. Vela's answer: **an app can also be a web app**.
A manifest with a `"web"` entry â€” `{ "entry": "index.html" }` â€” means the app folder is a
static web app the hub serves directly. This works on every platform, including iPhone via
Safari's "Add to Home Screen".

- **Support**: a `web` entry makes the app `supported: true` on EVERY current platform.
- **Serving**: installed web apps are served under `/apps/{id}/` â€” the entry file at
  `/apps/{id}/`, other files relative to it. Served from the installed copy in the data dir.
  404 (or redirect to install) if not installed.
- **Generated PWA pieces** (backend generates these on the fly from `app.json`, they are NOT
  files in the app folder):
  - `/apps/{id}/manifest.webmanifest` â€” `name`, `short_name`, `start_url: "."`, `scope: "."`,
    `display: "standalone"`, theme/background colors, icons (from the app icon).
  - `/apps/{id}/sw.js` â€” service worker so the app works offline after first
    load: navigations are network-first with the cached shell as offline
    fallback; cache-first applies only to same-origin static assets on an
    extension allowlist; API calls and non-GET requests are never cached. Its
    cache is versioned (`vela-{id}-{version}`) and on activate it deletes only
    caches with its own `vela-{id}-` prefix that aren't the current version â€”
    hub caches and other apps' caches are never touched.
  - `/apps/{id}/icon-192.png` and `/apps/{id}/icon-512.png` â€” PNG icons for the web manifest.
- **Lifecycle**: web apps spawn no process. `POST /launch` on a web app installs if needed and
  returns `{"id": ..., "running": true, "pid": null, "port": null, "url": "/apps/{id}/",
"runtime": "web", "runtimes": [...]}`. `POST /stop` is an explicit no-op returning
  `{"running": false}` â€” the web app stays installed and served. For web apps,
  `status.running` mirrors `installed`, `url` is `/apps/{id}/`, and the active runtime
  surfaces as `runtime: "web"`.
- **App icons for iOS**: a web app SHOULD ship a 180x180 PNG and reference it from its own HTML
  via `<link rel="apple-touch-icon">` so Add-to-Home-Screen gets a proper icon.
- **The hub itself is also a PWA**: the frontend ships its own `manifest.webmanifest` + service
  worker (`web/public/sw.js`) so users can Add the Vela hub to their home screen too. The hub
  SW deletes only stale caches prefixed `vela-shell-`/`vela-runtime-`/`vela-api-` outside its
  keep set â€” per-app caches (`vela-{id}-...`) belong to installed apps and are never touched.

## REST API

Base URL: `/api`. All responses JSON. Errors: `{"detail": "..."}` with proper HTTP status.
CORS: authenticated APIs do not allow cross-origin browser reads. See the
authentication section above. Supply `Authorization: Bearer <hub-token>` for
hub operations; app tokens work only on `/api/app/*`.

### Platforms

`GET /api/platforms`

```json
{
  "current": "posix",
  "supported": ["posix", "windows", "android"]
}
```

### Available apps (registry)

`GET /api/apps` â€” lists apps bundled in `apps/` merged with install/run state.

```json
{
  "apps": [
    {
      "id": "hello-vela",
      "name": "Hello Vela",
      "version": "1.0.0",
      "description": "...",
      "category": "getting-started",
      "author": "Vela",
      "installed": false,
      "running": false,
      "url": null,
      "supported": true,
      "runtimes": ["web", "process"],
      "runtime": "web"
    }
  ]
}
```

`supported` = the manifest has a non-null entry for the current platform OR has a `web` entry
(web apps are supported everywhere). App summaries for installed web apps carry
`"url": "/apps/{id}/"` and `running: true`. Summaries resolve installed-first: when an app is
installed, the installed copy's `app.json` (in `<data_dir>/installed/<id>/`) is the source of
truth, so the summary reports the INSTALLED version, and apps stay listed, launchable, and
servable even if their source folder under `apps/` is gone.

`GET /api/apps/{id}` â€” single app, same shape plus `"manifest": { ...full manifest... }`.
404 if unknown.

`GET /api/apps/{id}/icon` â€” serves the app's icon file (image/*). 404 if none.

### Lifecycle

Install, launch, stop, and uninstall are serialized by a backend-wide lifecycle lock â€” a
concurrent launch of the same app returns 409 rather than spawning orphan processes â€” and
`state.json` writes are locked per store.

`POST /api/apps/{id}/install` â€” copies the app from `apps/` into the data dir as a clean
replace: any previous installed copy is removed first, then copied fresh (never merged).
Idempotent.

```json
{ "id": "hello-vela", "installed": true }
```

`DELETE /api/apps/{id}` â€” uninstall: stops it if running, removes installed copy.

```json
{ "id": "hello-vela", "installed": false }
```

`POST /api/apps/{id}/launch` â€” installs if needed, starts the process locally. The run
command resolves from the INSTALLED copy's manifest, so launch works without the source
folder under `apps/`.

```json
{
  "id": "system-info",
  "running": true,
  "pid": 12345,
  "port": 8802,
  "url": "/apps/system-info/",
  "runtime": "process",
  "runtimes": ["process"]
}
```

`url` is null if the app has no port. 409 if already running. 400 if unsupported on the
current platform (including the android stub). After launch, apps with a port get a TCP
readiness probe (127.0.0.1:port, 0.1s interval, 5s max); on timeout the process is stopped,
its state cleared, and launch fails with 502 "did not become ready".

`POST /api/apps/{id}/stop`

```json
{ "id": "hello-vela", "running": false }
```

`GET /api/apps/{id}/status`

```json
{
  "id": "system-info",
  "installed": true,
  "running": true,
  "pid": 12345,
  "port": 8802,
  "url": "/apps/system-info/",
  "started_at": "2025-01-01T12:00:00",
  "logs": "last ~40 lines of the app log",
  "runtime": "process",
  "runtimes": ["process"]
}
```

`GET /api/health` â†’ `{"status": "ok", "version": "0.1.0"}`

### Settings

`GET /api/settings` â€” hub settings. The ntfy password is write-only: the API
reports `passConfigured` and never echoes the secret.

```json
{
  "theme": "dark",
  "chat_model": null,
  "chat_history": true,
  "ntfy_config": {
    "server": "",
    "topic": "",
    "user": "",
    "passConfigured": false,
    "events": { "digest": true, "status_alerts": true }
  }
}
```

`chat_model: null` = use the backend default model. `chat_history` toggles
server-side conversation history for the assistant.

`PATCH /api/settings` â€” partial update of the same keys (unknown keys ignored).
Semantics: absent key â†’ keep, `""` â†’ clear, value â†’ set. Nested objects
(`ntfy_config`, `ntfy_config.events`) merge. Returns `{"ok": true}`.

### Notifications (ntfy)

Push notifications via an [ntfy](https://ntfy.sh) server configured in Settings.
The backend POSTs JSON `{topic, title, message, tags, priority}` to
`{server}/{topic}` (basic auth when `user` is set) and validates the receipt
(`event === "message"`, matching topic, numeric time) before reporting success.
Distinct 502 errors for: not configured / invalid address, auth denied (401/403),
rate limited (429), server unreachable, invalid receipt.

`POST /api/notify/test` â€” optionally saves `{"ntfy_config": {...}}` from the
body first, then sends a test notification.

```json
{ "ok": true, "id": "abc123", "accepted_at": "2025-01-01T12:00:00" }
```

`POST /api/notify/publish` â€” `{title, message, tags?, priority?}` (priority 1â€“5,
default 3). Same response shape as test.

`GET /api/notifications` â€” in-memory ring buffer of the last 50 notifications
sent by this backend (newest first):

```json
{
  "notifications": [
    { "timestamp": "...", "title": "Vela hub digest", "kind": "digest" }
  ]
}
```

`kind` is one of `publish`, `test`, `digest`, `status_alert`.

A background scheduler ticks every 15 minutes while the app runs:

- **digest** (if enabled): once per day after 09:00 local â€” running apps,
  installed count, data-dir disk usage.
- **status_alerts** (if enabled): notifies when a previously-running app
  process is found dead (web apps excluded â€” they spawn no process).

### Assistant (Ollama)

Local AI assistant backed by Ollama (`OLLAMA_URL` env var, default
`http://localhost:11434`), scoped to hub data via tools: `list_apps`,
`app_status(app_id)` (pid/port/uptime for process apps), `app_logs(app_id, tail?)`,
`engine_status`. Bounded: max 5 model rounds and 8 tool calls per turn, 256KB
response cap, 24KB per tool result. Rate limit: 12 requests/minute per client.

Conversations are stored in `chat.sqlite` beside the other hub data when
`chat_history` is true, and the model context is rebuilt from that transcript
(last 16 messages) on every turn, so a server restart does not silently start an
unrelated model conversation behind a visible history. The stored transcript and
the model context are deliberately separate: the transcript keeps up to 400
messages per conversation and 200 conversations, pruned oldest-first, with 64KB
per message. A stopped or failed turn stores the partial answer it produced,
marked interrupted.

Access scope: Vela is a single-user personal server, so every request carrying
the hub bearer owns every conversation; there is no per-user partition. An
unknown id is 404, never another conversation. When `chat_history` is false no
durable record is written, a `conversationId` identifies only that request, and
turning the setting off deletes every stored conversation immediately.

`GET /api/ai/status` â€” never errors, even with Ollama down:

```json
{
  "reachable": false,
  "url": "http://localhost:11434",
  "chat_model": "qwen3:8b",
  "models": [],
  "hint": "..."
}
```

When reachable: `{"reachable": true, ..., "model_available": true, "models": [...]}`.

`POST /api/chat` â€” body `{"messages": [{"role": "user", "content": "..."}],
"conversationId": null}`; messages must end with a user message (400 otherwise).
Responds with `text/event-stream`, one JSON object per `data:` frame:

| event           | shape                                                                                       |
| --------------- | ------------------------------------------------------------------------------------------- |
| conversation id | `{"conversationId": "<uuid>"}` (first frame)                                                |
| streaming text  | `{"text": "<answer so far>"}`                                                               |
| tool activity   | `{"activity": {"id": 1, "tool": "list_apps", "state": "running" \| "complete" \| "error"}}` |
| final answer    | `{"done": true, "text": "<full answer>"}`                                                   |
| failure         | `{"error": "<message>"}`                                                                    |

Keepalive comment lines (`: keepalive`) are sent every 15s; the model request is
aborted when the client disconnects. With Ollama down, the stream still opens
and delivers a graceful `error` frame. The first frame also carries `title` when
the stored conversation was titled from this question. A `conversationId` that
does not exist is refused with an `error` frame rather than being replaced by a
new one.

### Conversation history

All of these require the hub bearer and return 409 when `chat_history` is false.

| Endpoint                                              | Behavior                                                                                                                                                                                      |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/chat/conversations?query=&archived=&limit=` | `{"conversations": [...], "enabled": true}`; newest first, bounded, searching titles and message bodies. Returns `{"conversations": [], "enabled": false}` instead of 409 when history is off |
| `POST /api/chat/conversations`                        | 201 with a new empty conversation                                                                                                                                                             |
| `GET /api/chat/conversations/{id}`                    | Conversation with its `draft` and full `messages`                                                                                                                                             |
| `PATCH /api/chat/conversations/{id}`                  | `{title?, archived?, draft?}`; a draft change does not reorder history                                                                                                                        |
| `DELETE /api/chat/conversations/{id}`                 | 204, permanent. Archiving is a separate reversible operation                                                                                                                                  |
| `POST /api/chat/conversations/import`                 | One-time import of a browser-held transcript; a server-side marker makes repeats no-ops, so a second tab or a retry cannot duplicate it                                                       |

A conversation summary is `{id, title, createdAt, updatedAt, archived,
messageCount, preview}`. Titles are derived from the first question and can be
renamed; later questions never rewrite a chosen title.

### Backups

Backups live under `data_dir/backups/<YYYYMMDD-HHMMSS>/` and contain copies of
`state.json`, `settings.json`, every installed app's `app.json`, and a consistent
`app-data.sqlite` snapshot when present (logs are skipped). Verification checks
SQLite integrity and stored JSON as well as manifests. Retention: newest 10
kept, older pruned on each create. This is a restore drill, not a live restore.

`GET /api/backups` â†’ `{"backups": [{"name": "20250101-120000", "size": 12345, "created_at": "..."}]}` (newest first).

`POST /api/backups` â†’ 201 `{"name": "...", "created_at": "...", "size": 12345}`.

`POST /api/backups/{name}/verify` â€” restore drill: the backup is copied into an
isolated temp dir and validated there (every JSON file parses; every installed
manifest loads); live files are never touched.

```json
{
  "name": "20250101-120000",
  "ok": true,
  "verified_at": "...",
  "files": [{ "file": "settings.json", "ok": true }],
  "manifests": [{ "id": "hello-vela", "ok": true }]
}
```

404 for unknown or malformed names.

## Static Hosting

If `web/dist/` exists, the backend serves it at `/` (SPA fallback to `index.html`
for non-`/api` routes). If not, `/` returns a small JSON notice pointing at the API.

## Backend Layout (target)

```
vela/
  __init__.py
  config.py          # data dir, paths, env overrides
  manifest.py        # manifest load/validate
  registry.py        # available vs installed apps (installed copy wins)
  state.py           # state.json + PID/creation-time liveness checks
  runners/
    __init__.py      # get_runner() platform dispatch
    base.py          # Runner ABC: launch/stop/status
    posix.py         # macOS/Linux subprocess runner
    windows.py       # Windows runner (for dev machines)
    android.py       # stub raising NotImplementedError with clear message
  api.py             # FastAPI app wiring everything
  settings.py        # settings.json store with secret redaction
  notify.py          # ntfy publish client + 15-min digest/status-alert scheduler
  assistant.py       # Ollama tool-calling assistant (hub-scoped tools, SSE emits)
  conversations.py   # Durable Ask conversations (chat.sqlite), retention and bounds
  backups.py         # timestamped backups, retention, isolated restore drill
  pwa.py             # on-the-fly manifest.webmanifest + sw.js generation for web apps
  webapps.py         # serving installed web apps under /apps/{id}/
  __main__.py        # `python -m vela` starts uvicorn on 7700
apps/
  hello-vela/        # static page via http.server AND a web app ("web" platform entry)
  system-info/       # tiny python HTTP server returning system info JSON
web/                 # React frontend (Vite)
```

Route precedence: `/api/*` and `/apps/*` are matched BEFORE the SPA fallback â€” the
frontend's SPA fallback must never swallow `/apps/{id}/...` requests. Note that the
hub shell's OWN routes (`/apps`, `/library`, `/environments`, `/settings`, `/app/:id`)
must not collide with backend app serving at `/apps/{id}/` (plural, with an id) â€”
the backend only claims `/apps/{id}/...` for KNOWN app ids; `/apps` (bare) belongs to
the SPA. The embedded app route is `/app/{id}` (singular) to avoid any ambiguity.

## Frontend Expectations — the Hub Shell

The dashboard is a full shell, not a single grid page: a narrow rail beside a
workspace, in the Nocturne visual language (Inter, pale lavender surfaces,
purple-leaning accents, colorful per-app icon tiles from each manifest `color`,
restrained 14px card corners), in light and dark.

- **Rail** (desktop, 62px): the Vela mark, then **Desk** and the **Launchpad**
  fixed at the top, then the apps the user **pinned** (core tools and installed
  apps alike, in the saved order), a separator, the apps that are **open but not
  pinned** under an **OPEN** label, and Settings — plus, for a remote session,
  Sign out — at the foot. The default pins are **Ask** and the **Marketplace**.
  There is no "More" menu and no "All apps" control; every other app lives in
  the Launchpad. The OPEN label is absent, not empty, when nothing unpinned is
  running, and a pinned app that is also running stays in the pinned group
  rather than appearing twice. An app carries an attention dot only when one of
  its own published widget summaries says `attention`. Pins are stored on the
  server as `rail.pinned` (core ids or app ids; the dashboard drops any that no
  longer resolve). Right-click, Shift+F10 or long-press a pinned item to unpin
  it or move it up or down, or an open item to pin it; the same pin actions are
  in the Launchpad and the app-window menu. The open destination gets a raised
  surface, an edge marker and `aria-current`; every icon is named on hover and
  keyboard focus, and the app region scrolls so the foot controls stay reachable
  in a short window.
- **Launchpad** (`/apps`): a full-screen app grid over the blurred wallpaper,
  the one place that answers "which apps do I have". It replaces the earlier
  All apps drawer and the Manage apps page. A centred hero search sits at the
  top and filters the grid live; Enter opens the first match; `Ctrl+Space`
  (`Cmd+Space` on a Mac) toggles it from anywhere and Escape returns to the
  previous route. The grid is sectioned **Open** (running apps), **Apps**
  (installed, alphabetical), **Vela** (Vela's own tools) and a final "get more
  apps" tile leading to the Marketplace. Each tile is a large icon with a label, a
  green dot when the app is running and an amber dot when a summary says
  `attention`. Right-click, Shift+F10 or a long press opens a context menu —
  Open, Pin to rail, Add widget to desk (when the app declares widgets), App
  settings, Stop (running process apps) and Remove — and arrow keys move
  between tiles in the `role="grid"`. On a phone it is a four-column grid the
  rail stays beside.
- **Phone**: there is no bottom bar and no hamburger. Every page keeps the
  rail on screen at every width — beside its content, never over it — so a
  destination or a ready app opens with one tap. A page's own panel slides in
  from the left edge beside the rail: Ask puts its conversation list there, so
  apps and conversations are one gesture away from the chat. A swipe up from the
  desk's bottom edge opens the Launchpad; a swipe down from the top closes it;
  press-and-hold raises the same context menus a right-click gives.
- **Shortcuts**: one window keydown listener owns the OS-level keys —
  `Ctrl+K`/`⌘K` search, `Ctrl+Space`/`⌘Space` toggle the Launchpad, `Ctrl+1`..
  `Ctrl+9` open the pinned apps in rail order, `Esc` backs out, and `?` or
  `Ctrl+/` opens the shortcut sheet. Modifier chords fire even in a field; plain
  keys yield to editable targets and never reach an app's iframe.
- **Workspace**: an optional context panel, a contextual header and the content
  surface. The header carries the ⌘K search palette (apps, settings entries and
  same-origin mini-app data) and the notification bell. A healthy server is not
  reported: the header shows a “Can’t connect to Vela” notice with Retry only
  when the engine resource has actually failed, and the server address badge
  only while developer tools are on. Missing or still-loading data is neither.
  Pages that show their own `<h1>` do not repeat it in the header; Ask uses it.
- **App window** (`/app/{id}`, hub and compact chrome): the workspace is topped
  by the app's own **title bar** instead of the contextual header — a back
  control to where the app was opened from (else Desk), the app icon and name, a
  state pill (Running / Starting / Stopped / Not supported), a thin progress
  line while the frame loads, up to two of the app's granted quick actions, the
  notifications bell, and a `⋯` menu (Pin/Unpin, Add widget to desk, App
  settings, Reload app, Open in new tab, Stop app, Close). It has no search
  field; `Ctrl+K` still opens the palette. While the frame connects, the app is
  shown centred over a dimmed ground; after a 10-second timeout that becomes
  "This app did not respond" with Reload and Stop. A missing app shows "App
  unavailable" with a Marketplace link. `view.appearance: "dark"` draws the bar
  in the dark tokens under any hub theme. Seamless apps keep their floating Vela
  menu instead, extended with Pin and Add widget.
- **Desk** (`/`): the home of Vela is a widget board over the user's wallpaper,
  with the rail beside it. It replaces the earlier launcher-only Home, and with
  it the rule that `/` shows no system information: a desk may show what the
  server actually knows. Widgets are rendered by the host and never invent
  data — a widget type ships only once a real source for it exists, so weather,
  Health, Money, Meals, Photos and Paperless are absent rather than mocked.
  The seeded board is **Your apps** (the tile grid, each tile an Open control
  showing the app's own short purpose, plus one "Add an app" tile and
  placeholders while the first list loads), **Clock** (time, weekday and date),
  **Running now** (the apps the engine reports running, "Nothing running."
  otherwise) and **Ask** (the newest conversation's title and last line, and a
  box that opens Ask with what was typed). There are two boards: six columns
  above 860px and two below, edited and stored separately, never reflowed into
  each other. Each widget is a labelled region. The seeded phone board is
  **Clock**, **Needs you** (the apps whose own summaries say `attention`, with
  “Everything’s running.” when none do), **Your apps** as a four-column icon
  grid, and **Ask**.
- **Arranging the desk**: **Add widget** opens a grouped, searchable list of
  every placeable type — Vela's own first, then one group per app that provides
  any. **Arrange desk** turns on dragging and resizing, undo and redo
  (Ctrl+Z / Ctrl+Shift+Z), a per-widget menu with Duplicate, Remove and any
  options that type has, and keyboard arrangement on a focused frame: arrows
  move, Shift+arrows resize, Delete removes, each announced through an
  `aria-live` region. **Done** saves, **Cancel** restores, and navigating away
  with unsaved changes prompts. A long press enters Arrange mode on a phone.
  Boards persist in `<data_dir>/desk.json` behind `GET/PUT /api/desk`; a `PUT`
  built on an older `revision` answers 409 with the current one and the
  dashboard reloads and says so, and an invalid board answers 422 without
  storing anything. A board may only name a widget type that exists now, so
  uninstalling an app removes its widgets rather than leaving dead frames.
- **Personalise** (a desk control, and a long press on bare wallpaper on a
  phone): the wallpaper — bundled `lake` (a photograph), `sage` and `night`
  (gradients), or `custom` — plus **Dim the wallpaper**, **Show app names** and
  **Ask on this board**, which adds or removes that board's Ask widget and saves
  immediately. `GET/PUT/DELETE /api/wallpaper` stores one image in the data
  directory: JPEG, PNG or WebP only, checked against its own header rather than
  its declared type, at most 8 MB, replaced rather than accumulated, and
  removing it returns the desk to `lake`.
- **Desk widgets and their sources**: `clock` (the browser's clock),
  `apps` and `running` (`/api/apps`), `ask` (`/api/chat/conversations`),
  `system` and `volume` (`GET /api/system/metrics`), `flows`
  (`/api/automations/status` — `runsToday`, `failuresToday`,
  `averageDurationMs`, counted since this computer's midnight) and `backups`
  (`/api/backups`, with **Back up now**). `/api/system/metrics` reports CPU,
  memory, uptime and a 60-sample CPU history taken every 10 s in memory only,
  plus one entry per volume the user named in `settings.desk.volumes` and the
  one Vela's own data sits on. It answers `{"available": false}` when psutil is
  missing rather than failing, and a volume that is not connected is reported
  as unreachable rather than dropped. `settings.desk` holds
  `volumes`, `wallpaper`, `dim` and `labels`; a volume path must be a folder
  that exists or `PATCH /api/settings` answers 422. Settings › Desk is where
  volumes are added and removed.
- **Marketplace** (`/library`; labelled Marketplace in the UI, the URL keeps
  the `library` name): one place to find, install, update and remove apps,
  replacing the separate Manage apps page. Three tabs ride in `?tab=`:
  **Discover** (default) shows the apps you do not have yet as catalog cards —
  icon tile, name, category and version, the app's own description, and an
  Install action — led by a small featured row of described apps, with search
  and category chips; installed apps are not repeated here. **Installed** lists
  everything on this computer (packages and connected web apps) as rows leading
  to the detail drawer, with an All / Running filter, Open, updates and removal.
  **Updates** gathers the apps with a newer pinned release and offers **Update
  all**, which installs each in turn and reports any that fail. The single "Add
  an app" dialog stays in the header and offers only supported sources: "Install
  from file", "Connect a website", and — only while developer tools are on — a
  folder on the server computer. Manifest URLs and pasted JSON are not supported
  sources. `/apps/manage` is not a route (the backend owns the `/apps/*`
  namespace); links to the management surface point at `/library?tab=installed`.
- **Ask** (`/ask`, `/ask/{conversationId}`): a conversation panel with date
  groups, search, archived view and per-conversation rename/archive/delete; a
  contextual header with the conversation title, model and connection state; the
  transcript; and the composer. Below 1100px the panel overlays the workspace.
- **Embedded app view** (`/app/{id}`): versioned hub/compact/seamless
  presentation over an iframe of `/apps/{id}/`. `hub` and `compact` render
  inside the shell, beside the rail, with the contextual header naming the app
  and no second app bar; `seamless` keeps its own standalone chrome with the
  host's Vela menu. Each offers **App settings**.
  If the app is installed but a process app that is stopped, show an
  interstitial with one Open button instead of the iframe; nothing else — not
  polling, a prefetch, a render effect or a passive visit — may start a process.
  A removed or unknown id recovers inside the shell.
- **System** (`/environments`): a developer destination. With developer tools
  off the route stays valid and explains itself, offering an explicit "Enable
  developer tools" action and a way back; visiting never enables it. With them
  on it has tabs, the active one in `?tab=`: **Overview** (the default) shows
  the Local Engine card from `GET /api/engine` — status dot and Running pill,
  endpoint, applications installed, storage used, data directory — plus the
  running apps; **Logs** (`?tab=logs`, the open file in `?log=`) lists the
  files Vela wrote, grouped by what wrote them with rotated copies under their
  base, and shows one of them with a line count, search, auto-refresh,
  Download and a confirmed Clear. Automation runs are linked to their own
  viewer rather than duplicated here.
- **Settings**: a popup over the current screen with searchable categories for
  General, Appearance, Chat & privacy, Local AI, Notifications, Backups &
  storage, and Developer tools while that preference is on. Theme previews and
  chat preferences save through the settings API; notification connection
  details have an explicit Save action. Switching categories keeps unsaved form
  entries. Existing `/settings#category` links open the matching category over
  the desk, with `#storage` → Backups & storage and `#network`/`#environments` →
  Developer tools. General includes phone setup, Home Screen installation and
  the developer-tools switch. Backups can be created and verified through the API.
- **Developer tools**: one browser-local preference (`vela-developer-tools`),
  off when absent and off for any unrecognized value, never inferred from the
  host, user agent or installed apps. It is reactive and shared: switching it
  updates navigation, search, settings, app menus and app details immediately,
  without reloading, remounting an open app, discarding a draft, changing a
  grant or touching app lifecycle. Unavailable storage falls back to memory for
  the session and says so; another tab on the same origin is followed.
- **App detail** (drawer, from any card): description, author, access and trust,
  release history, the Add-to-Home-Screen section for web apps, the app's
  permissions, and — under developer tools — a collapsed Diagnostics block.
- **App settings** (drawer, from an app's own workspace): permissions,
  connection setup, earlier-data import, update and removal, plus Diagnostics
  under developer tools.
- **App diagnostics**: one collapsed block per app holding identifiers, runtime,
  process and start time, platform commands and ports, the log tail, manual
  start/stop and the action execution history. It fetches nothing until opened.
- **Permissions**: action requests are decisions, not diagnostics. They stay
  available with developer tools off, a waiting decision is announced above the
  app with a Review control, and the drawer names the requesting app, the
  action, the app it happens in, and what Allow does and does not grant.
- Poll `GET /api/apps` every 5s globally; `status` for the focused app; `engine`
  every 10s. All consumers share those requests; pages do not add their own.
- All fetches relative `/api/...`; Vite proxy forwards `/api` and `/apps`.
- The hub remains an installable PWA (v1 work: manifest, sw.js, icons) â€” keep it.
