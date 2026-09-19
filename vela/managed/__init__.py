"""Managed web apps: existing self-hosted web servers Vela installs and runs.

A managed web app is not an SDK app and not a saved connection to something
someone else operates. It is an ordinary web server -- Memos, say -- whose
release Vela installs, whose process Vela owns, and whose interface Vela
publishes on an address of its own. The application keeps its own UI, accounts
and database; Vela keeps the installation record, the service lifetime, the
gateway and the declared recovery operations.

The split across this package follows those responsibilities:

- `contract`  the v3 manifest: what a package may declare, and what it may not.
- `packages`  staging, downloading, verifying and extracting release artifacts.
- `store`     the persistent record: identity, intent, releases and the journal.
- `supervisor` the process: argv, readiness, ownership, stop and restart budget.
- `gateway`   the app's own web origin, its launch exchange and its proxy.
- `service`   the operations a person performs, and the order they happen in.

Nothing here grants the SDK bridge, app storage, actions, widgets or agent
access. Hosting an application is not an introduction to it.
"""

from .contract import (
    HOST_MANAGED_SERVICE_REVISION,
    ManagedManifest,
    ManagedManifestError,
    host_target,
    load_managed_manifest,
    validate_managed_manifest,
)

__all__ = [
    "HOST_MANAGED_SERVICE_REVISION",
    "ManagedManifest",
    "ManagedManifestError",
    "host_target",
    "load_managed_manifest",
    "validate_managed_manifest",
]
