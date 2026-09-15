# Changelog

Notable changes to Vela Server and its browser dashboard. SDKs and individual
apps are versioned in their own repositories. Changes stay under Unreleased
until the release workflow prepares a tested server version.

## Unreleased

### Added

- ServerKit install badge, deployment manifest and container build with
  persistent app data and password-protected access through a trusted HTTPS
  proxy. Set the public origin and proxy address before the first deployment.
- Connect existing HTTPS web services from Library with a name, address and
  icon color. Open them inside Vela with reload, edit and browser fallback
  controls. Connections use a separate hostname and keep the service's own
  login and data; removing a connection does not stop or uninstall the service.
- The welcome popup connects iPhone and Android users with a QR code. Enable
  password-protected Wi-Fi access directly on the server computer; the phone
  page guides certificate trust, browser choice and Home Screen installation.
  Hosted servers use their configured HTTPS domain. Reopen setup or turn off
  Wi-Fi access from Settings. Contributors should update Python requirements
  and run `npm --prefix web ci` for certificate and QR-code dependencies.
- Windows installer with the Vela sail icon, Start menu integration and optional
  startup at sign-in. Windows portable downloads remain available.
- Windows tray controls for server status, opening the dashboard, starting and
  stopping the server, sign-in startup and logs, without keeping a terminal open.

### Changed

- Home states the date, server version and how many apps are installed and
  running, shows placeholders while that list loads, and animates a progress
  line only while an install, launch or stop is actually in flight. Library now
  shows catalog cards with each app's description, category, version and the one
  action that applies to it, category filters with counts, a filter for pending
  updates, and a single Add an app dialog covering the supported sources: a
  release archive, a folder on the computer running Vela, or connecting an HTTPS
  service you already run.
- Ask keeps your conversations. A panel beside the conversation lists them by
  date with search, rename, archive and permanent delete, the open conversation
  is part of the address so reloading or sharing the link returns to it, and each
  conversation keeps its own unsent draft. Follow-up questions use that stored
  conversation, so restarting the server no longer answers as if the history were
  not there, and a stopped answer is kept with the question it belongs to.
  Starting another conversation never erases the previous one. The transcript
  your browser used to hold is imported once, and turning chat history off in
  Settings still deletes everything that was stored.
- The dashboard now uses a narrow app rail instead of a labelled sidebar. The
  rail keeps Home, Ask, your installed apps, Library, Automations, Apps, System
  and Settings one click away, marks the open destination and names each icon on
  hover and keyboard focus. Search, the server address, and notifications moved
  into a contextual header above each page. On phones the same destinations,
  including every installed app, open from a navigation drawer beside the
  existing bottom bar.
- Apps that ask for the hub's own layout, and connected web services, now open
  beside the rail with a single contextual header showing the app's icon, name
  and state instead of a second app bar. Connected services keep their edit,
  reload and open-in-browser controls in that header, and their own origin,
  login and data are untouched. Apps that declare the compact or seamless
  presentation are unchanged until their manifest opts in. Opening an app that
  has been removed now lands in the normal workspace with a way back.
- Settings opens in a compact popup over the current screen, with searchable
  categories, light and dark previews, and separate chat privacy controls.
  Existing settings links open the matching category; phone layouts keep the
  categories and controls inside the popup.
- Ask now keeps its multiline composer at the bottom, with a separately scrolling
  conversation, formatted replies, copy controls, expandable checks, and a jump
  to the latest response. Type `@` to find an installed app by name or ID and
  mention it in a question. Stopped or interrupted answers remain visible and
  can be retried. Contributors should run `npm --prefix web ci` for the new
  Markdown dependencies.
- Contributors can run `npm --prefix web run check` for lint, formatting, tests
  and the dashboard build; CI uses the same command. The dashboard now shares
  engine status across pages and reuses dialogs/drawers with keyboard focus
  handling and dismissal guards while work is pending. Development requires
  Node.js 22.13+ in the 22.x line or Node.js 24+; run `npm --prefix web ci` after
  updating to install the new check tools.
- Dashboard development now uses organized SCSS modules, shared UI controls and
  request hooks, and one route/navigation definition. The developer guide
  includes an example for adding a page; contributors should run `npm --prefix
  web ci` to install the Sass build dependency.
- Automatic releases now require the tested Windows installer alongside all
  three portable downloads and checksums. Windows upgrades and uninstall keep
  existing apps and data; installers and executables carry Vela branding.

### Fixed

- Installing or updating an app no longer fails on Windows when a virus scanner
  still holds a handle on the files that were just written. The move into place
  retries briefly before reporting a permission error.
- Long dashboard pages scroll independently of the desktop navigation, which
  stays in place. Server connection status is shown by the address badge in
  each page's header.
- Browser tabs now show Vela's purple-and-cyan sail logo instead of the old amber icon.
- The dashboard Ask page can talk to the local model again. Sending a message
  failed immediately with "Request failed (422)" because the dashboard posted a
  request shape the server rejected. Failed requests now also show the server's
  explanation instead of only a status code.

## 0.1.0 - 2026-09-14

### Added

- Personal app server with a browser dashboard, local app lifecycle management,
  settings, notifications, backups and home-screen installation support.
- Versioned app manifests, a scoped browser SDK, persistent app data with
  revision checks, legacy data import and explicit app-to-app action grants.
- Reviewed app folder/ZIP installation, pinned release catalogs, independent
  app updates and rollback to matching package/data checkpoints.
- Password-protected HTTPS access for other devices and a scoped connection
  to an existing Ollama server.
- Portable server packaging that includes the Python runtime and built
  dashboard, opens the browser after startup, and supports headless launch.
- A workflow for building and smoke-testing native server downloads.
- Contributor and security guides, maintainer credits, donation links and QR
  codes, and shared agent instructions requiring meaningful changelog updates.
- Pull request templates and Windows/Linux checks for the hub and dashboard.

### Changed

- Merging `dev` into `main` now versions, builds and smoke-tests Windows,
  macOS and Linux downloads, then publishes a GitHub Release with the archives
  and SHA-256 files attached. Manual runs on `main` use the same release flow.
- Separated apps, SDK, contracts, templates, generator and catalog into their
  own repositories. The hub runs and tests without sibling checkouts.
- Simplified the Quick Start around downloading and opening Vela Server;
  source setup and packaging commands now live in the developer guide.
- Replaced public implementation-increment notes with current app/testing
  guides. Working plans and historical notes are kept locally and excluded
  from GitHub.
