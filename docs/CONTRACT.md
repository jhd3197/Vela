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

| Endpoint | Authorization and behavior |
| --- | --- |
| `POST /api/web-apps` | Hub bearer; `{name, url, color?}` creates a registration (201) |
| `PUT /api/web-apps/{id}` | Hub bearer; `{name, url, color?, revision}` updates the reviewed revision |
| `DELETE /api/web-apps/{id}` | Hub bearer; `{revision}` removes only the registration |
| `GET /api/apps`, `GET /api/apps/{id}`, `GET /api/apps/{id}/status` | Hub bearer; includes connected app summaries |

Stale update/removal revisions return 409; missing records return 404. Names are
limited to 80 characters, addresses to 2048, icon colors to six-digit hex and
registrations to 200. Validation allows unambiguous HTTPS URLs without credentials;
the hostname must differ from the hub. The browser rechecks against its actual
hostname, including when the hub address changes. No server-side URL fetch, DNS
lookup or proxy is performed.

Summaries have `kind: "connected-web"`, `schemaVersion: null`, `runtime: "connected"`,
`view: {surface: "connected", chrome: "compact", url}`, `installed: true`,
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
that an upstream service is available or successfully rendered.

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

| Field | Implemented values |
| --- | --- |
| `view.surface` | `embedded`, `external` (HTTPS `url` required), `none` |
| `view.chrome` | Embedded only: `hub`, `compact` (default), `seamless` |
| `capabilities.required` | `storage`, `connections`, `actions`; any unknown required grant blocks validation |
| `capabilities.optional` | Known capabilities granted; others reported in `unavailableCapabilities` |
| `data.schemaVersion` | Positive integer, required when requesting storage |
| `data.quotaBytes` | 1 KiBâ€“10 MiB; default 1 MiB |

`hub` renders the normal navigation; `compact` renders an app bar; `seamless`
renders no bars and retains a 44px-or-larger Vela menu outside the app. That
menu can return, close, show the compact bar, or stop a managed process. A user
compact preference persists per app. Failed loading retains host controls.
The host reports viewport dimensions, visual insets and its control rectangle
to the SDK; apps should avoid that rectangle. Closing never implies stopping.
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

| Endpoint | Result |
| --- | --- |
| `GET /api/app/storage` | `{value, revision, schemaVersion}`; missing document has `null` value and revision 0 |
| `PUT /api/app/storage` | `{value, revision}` body; returns the saved document at revision + 1 |
| `DELETE /api/app/session` | Revokes that bearer |

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

| Endpoint | Authorization and behavior |
| --- | --- |
| `POST /api/apps/{id}/migration/preview` | Hub; `{value, revision}`; reports current revision, additions and conflicts |
| `POST /api/apps/{id}/migration` | Hub; same body, compare-and-swap commit with recovery copy |
| `POST /api/apps/{id}/upgrade` | Hub; one-time bundled static v1 to v2 replacement; archives the previous package |
| `GET /api/app/storage/snapshots` | App storage grant; own backup metadata |
| `POST /api/app/storage/snapshots` | App storage grant; snapshot current saved document |
| `POST /api/app/storage/snapshots/{id}/restore` | App storage grant; `{revision}`; validates schema, snapshots current data, writes next revision |
| `GET/PUT/DELETE /api/apps/{id}/connection` | Hub; inspect, test/bind `{endpoint}`, disconnect |
| `GET /api/app/connection` | App connections grant; own binding status |
| `POST /api/app/connection/invoke` | App connections grant; `{operation, payload}` within manifest allowlist |

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

| Endpoint | Authorization / behavior |
| --- | --- |
| `GET /api/apps/{id}/actions` | Hub; requests, availability, permission state and reviewed fingerprints |
| `PUT /api/apps/{id}/actions/grant` | Hub; `{app, action, allow, sourceContract, targetContract}` |
| `GET /api/apps/{id}/actions/history` | Hub; latest 50 incoming/outgoing execution metadata entries |
| `GET /api/app/actions` | App session; own declared action requests |
| `POST /api/app/actions/invoke` | App session; `{app, action, input, key}` with current explicit grant |

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

This is a synchronous app-action broker, not an automation scheduler or assistant
integration. It runs no scripts, containers, arbitrary network calls or native
code. See [the Meals and Notes guide](APPS.md#meals-and-notes).

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

| Endpoint | Hub-only operation |
| --- | --- |
| `GET /api/catalog` | Source, cached/error status and pinned releases |
| `POST /api/catalog/refresh` | Keep last good index on failure |
| `POST /api/releases/prepare` | `{folder}` or `{app_id}`; optional `{app_id, rollback: releaseId}` |
| `POST /api/releases/upload` | Raw ZIP body, at most 32 MiB; returns staged review |
| `POST /api/releases/{review}/commit` | Exact reviewed `{capabilities, operations}` approval |
| `DELETE /api/releases/{review}` | Cancel and remove staged files |
| `GET /api/apps/{id}/releases` | Release history and retained previous versions |

Reviews last 20 minutes and pin staged bytes, installed bytes and data revision.
Only increasing app versions are accepted as updates; earlier versions require
rollback. New installation identities, app data, migration markers and release
records share one SQLite transaction. A journal coordinates installed-directory
swaps with that transaction. Startup recovers an uncommitted swap before serving
requests. App tokens are revoked on release changes. Pending reviews expire
and do not survive a restart. Releases/checkpoints are retained until explicitly
managed by the operator; no automatic release-history pruning is implemented.

### Engine API details

`GET /api/engine` â€” the "Local Engine" status powering the Environments UI:
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
    "posix":   { "run": "python3 -m http.server {port}", "port": 8801 },
    "windows": { "run": "python -m http.server {port}", "port": 8801 },
    "web":     { "entry": "index.html" }
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
{ "id": "system-info", "running": true, "pid": 12345, "port": 8802, "url": "/apps/system-info/", "runtime": "process", "runtimes": ["process"] }
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
{ "notifications": [ { "timestamp": "...", "title": "Vela hub digest", "kind": "digest" } ] }
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
response cap, 24KB per tool result. Conversations live in memory keyed by UUID
(30-minute TTL, cap 100, last 16 messages of history; history disabled when
`chat_history` is false). Rate limit: 12 requests/minute per client.

`GET /api/ai/status` â€” never errors, even with Ollama down:
```json
{ "reachable": false, "url": "http://localhost:11434", "chat_model": "qwen3:8b", "models": [], "hint": "..." }
```
When reachable: `{"reachable": true, ..., "model_available": true, "models": [...]}`.

`POST /api/chat` â€” body `{"messages": [{"role": "user", "content": "..."}],
"conversationId": null}`; messages must end with a user message (400 otherwise).
Responds with `text/event-stream`, one JSON object per `data:` frame:

| event | shape |
|---|---|
| conversation id | `{"conversationId": "<uuid>"}` (first frame) |
| streaming text | `{"text": "<answer so far>"}` |
| tool activity | `{"activity": {"id": 1, "tool": "list_apps", "state": "running" \| "complete" \| "error"}}` |
| final answer | `{"done": true, "text": "<full answer>"}` |
| failure | `{"error": "<message>"}` |

Keepalive comment lines (`: keepalive`) are sent every 15s; the model request is
aborted when the client disconnects. With Ollama down, the stream still opens
and delivers a graceful `error` frame.

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
  "files": [ { "file": "settings.json", "ok": true } ],
  "manifests": [ { "id": "hello-vela", "ok": true } ]
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

## Frontend Expectations â€” the Hub Shell (v2)

The hub is a full shell, not a single grid page. Dark, refined, Linear/Raycast-class
restraint. Reference mockup: sidebar layout, colorful icon tiles, purple-leaning accents
on a near-black base (keep the Vela amber for primary actions; per-app `color` for tiles).

- **Sidebar** (desktop): Vela wordmark + sail logo, nav â€” Home, Apps, Library,
  Environments, Settings. Collapses to a bottom tab bar on mobile
  (Home / Apps / Library / Settings) with safe-area insets.
- **Top bar**: search field ("Search apps, settings, or anythingâ€¦", âŒ˜K-style hint;
  client-side filter over apps + settings entries), notification bell (may be static),
  avatar circle. "+ Install App" primary button â†’ goes to Library.
- **Home** (`/`): heading "Your local app hub" + sub "Install, organize, and run your
  apps â€” all on your machine." Grid of INSTALLED app cards (icon tile with app color,
  name, one-line description, chevron, Running badge with green pulse or Install button
  for available ones), footer status line "All systems ready Â· Running locally" from
  `GET /api/engine`.
- **Apps** (`/apps`): installed apps, richer rows/cards â€” Open (â†’ `/app/{id}`), Stop,
  Uninstall, status. Filter tabs: All / Running / Not installed.
- **Library** (`/library`): ALL available apps (installed or not) as rows: icon tile,
  name, description, category tag, Install/Installed button. Search + category filter
  chips. This is where "+ Install App" lands.
- **Embedded app view** (`/app/{id}`): versioned hub/compact/seamless presentation
  over an iframe of `/apps/{id}/`. If the app is installed but a process app that is stopped,
  show a launch interstitial with a Launch button instead of the iframe.
- **Environments** (`/environments`): "App Environments â€” apps run in isolated
  environments on your machine." Local Engine card from `GET /api/engine`: status dot +
  Running pill, endpoint, applications installed, storage used (human-readable),
  plus a list of running apps ("Manage" â†’ links to Apps).
- **Settings** (`/settings`): section list (General, App Environments â†’ links to
  Environments, Storage â†’ engine storage info, Network â†’ platform/endpoint info,
  Extensions/Backups as disabled "soon" rows). Keep it honest â€” only wire what exists.
- **App detail** (drawer or page, from any card): description, author, version,
  category, status, actions, log tail for process apps, Add-to-Home-Screen section
  for web apps (iOS instructions / beforeinstallprompt as in v1).
- Poll `GET /api/apps` every 5s globally; `status` for focused app; `engine` on
  Home/Environments/Settings mount + 10s.
- All fetches relative `/api/...`; Vite proxy forwards `/api` and `/apps`.
- The hub remains an installable PWA (v1 work: manifest, sw.js, icons) â€” keep it.
