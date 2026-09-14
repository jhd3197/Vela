# Changelog

Notable changes to Vela Server and its browser dashboard. SDKs and individual
apps are versioned in their own repositories. Changes stay under Unreleased
until the release workflow prepares a tested server version.

## Unreleased

## 0.1.2 - 2026-09-14

No additional release notes were provided.

## 0.1.1 - 2026-09-14

### Added

- Windows installer with the Vela sail icon, Start menu integration and optional
  startup at sign-in. Windows portable downloads remain available.
- Windows tray controls for server status, opening the dashboard, starting and
  stopping the server, sign-in startup and logs, without keeping a terminal open.

### Changed

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
