"""The operations a person performs on a managed app, and the order they happen in.

The shape of an installation is what makes the rest of this file short. Code is
never swapped in place: every reviewed release is extracted once into
`releases/<id>/`, and the installation record names which one is active. So an
update is an extraction followed by a database commit, and a rollback is a
database commit. There is no half-replaced directory to recover from, because
nothing is ever half-replaced.

What *is* replaced in place is the app's own data, and only ever by a restore.
That one operation has a journal, staged directories and a safety copy taken
first, because it is the only step where an interruption could leave a database
that is neither the old one nor the new one.

Order matters and is the same every time an operation touches a running app:

1. Claim the app's one exclusive operation, or refuse with what is already running.
2. Revoke gateway access, so nothing is writing through the front door.
3. Stop the service and confirm it has let go of its data.
4. Take a verified snapshot.
5. Do the thing.
6. Commit. Only now has anything changed.
7. Start again if it was running, and report what happened either way.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import dir_size
from ..errors_http import Conflict, InvalidRequest, NotFound, Precondition, Unprocessable
from ..logging_setup import audit
from ..package_files import replace_dir
from . import packages
from .contract import (
    ManagedManifest,
    ManagedManifestError,
    describe_target,
    host_target,
    load_managed_manifest,
)
from .gateway import GatewaySessions, launch_path
from .packages import ArtifactError
from .store import ManagedStore, safe_app_id
from .supervisor import ManagedSupervisor

LOG = logging.getLogger(__name__)

#: How long an install review stays open before its staged bytes are discarded.
REVIEW_SECONDS = 1800
MAX_OPEN_REVIEWS = 10
#: Retained releases per app, beyond the active one. Enough to roll back and to
#: roll back the rollback; not so many that a 60 MB binary accumulates for ever.
KEEP_RELEASES = 3
#: Automatic snapshots retained per app. A snapshot a person asked for is never
#: pruned automatically.
KEEP_AUTO_SNAPSHOTS = 5


class ManagedApps:
    """Installing, running, updating, backing up and removing managed web apps."""

    def __init__(
        self,
        config,
        *,
        store: ManagedStore,
        supervisor: ManagedSupervisor,
        sessions: GatewaySessions,
        registry=None,
        connected_apps=None,
        notify=None,
    ) -> None:
        self.config = config
        self.store = store
        self.supervisor = supervisor
        self.sessions = sessions
        self.registry = registry
        self.connected_apps = connected_apps
        self._notify = notify
        self._reviews: dict[str, dict[str, Any]] = {}
        self.target = host_target()
        self.cache = config.data_dir / "managed-cache"

    # ----------------------------------------------------------- identities --

    def owns(self, app_id: str) -> bool:
        try:
            return self.store.get(safe_app_id(app_id)) is not None
        except NotFound:
            return False

    def manifest(self, app_id: str) -> ManagedManifest:
        record = self.store.require(app_id)
        return self._manifest_from(record)

    def _manifest_from(self, record: dict[str, Any]) -> ManagedManifest:
        from .contract import validate_managed_manifest

        raw = record["manifest"]
        return validate_managed_manifest(
            raw, folder=record["app_id"], path=self.store.paths(record["app_id"]).package
        )

    # ------------------------------------------------------------ addresses --

    @property
    def domains(self) -> list[str]:
        """Every parent name a managed app may be published under."""
        return list(self.config.app_domains)

    def host_for(self, app_id: str, domain: str | None = None) -> str:
        return f"{app_id}.{domain or self.domains[0]}"

    def origin_for(self, app_id: str) -> str:
        """The address this app is published on for the computer running Vela."""
        port = self.config.app_gateway_port
        host = self.host_for(app_id)
        suffix = "" if port in (80, None) else f":{port}"
        return f"http://{host}{suffix}"

    def addresses(self, app_id: str) -> list[dict[str, str]]:
        """Every address this app answers on, with what each one needs.

        The second entry is only useful once a name resolves to this computer,
        which is an operator's DNS to arrange. Saying so beside the address is
        better than publishing a link that quietly fails on a phone.
        """
        found = [{
            "url": self.origin_for(app_id),
            "scope": "this computer",
            "requires": "",
        }]
        for domain in self.domains[1:]:
            port = self.config.app_gateway_lan_port or self.config.app_gateway_port
            found.append({
                "url": f"https://{self.host_for(app_id, domain)}:{port}",
                "scope": "other devices on this network",
                "requires": (
                    f"a DNS entry pointing *.{domain} at this computer, and Vela's "
                    "Wi-Fi certificate installed on the device"
                ),
            })
        return found

    # ---------------------------------------------------------------- review --

    def _prune_reviews(self) -> None:
        moment = time.monotonic()
        for token, plan in list(self._reviews.items()):
            if plan["expires"] < moment:
                shutil.rmtree(plan["stage"], ignore_errors=True)
                self._reviews.pop(token, None)

    def review(self, *, folder: str | None = None, archive: str | None = None) -> dict[str, Any]:
        """Stage, download, verify and expand a package, then describe it.

        Everything expensive happens before a person is asked to approve
        anything, and nothing is approved that is not already on this disk. That
        is what lets the approval name a digest: by the time the review is shown,
        the bytes it describes are the bytes that will run.
        """
        self._prune_reviews()
        if len(self._reviews) >= MAX_OPEN_REVIEWS:
            raise Conflict(
                "There are too many install reviews open. Finish or cancel one first.",
                code="managed.too_many_reviews",
            )
        token = str(uuid.uuid4())
        stage = self.config.data_dir / "staging" / f"managed-{token}"
        try:
            package = stage / "package"
            if folder:
                source_path = Path(folder).expanduser().resolve()
                if not source_path.is_dir():
                    raise NotFound("That folder is not on this computer",
                                   code="managed.folder_missing")
                data_root = self.config.data_dir.resolve()
                if source_path == data_root or source_path in data_root.parents:
                    raise Unprocessable(
                        "That folder contains Vela's own data and cannot be imported as an app",
                        code="managed.folder_refused",
                    )
                packages.copy_package(source_path, package)
                source = {"kind": "folder", "path": str(source_path),
                          "publisher": "Unverified local package"}
            elif archive:
                packages.unpack_package(Path(archive), package)
                source = {"kind": "archive", "publisher": "Unverified local package",
                          "archiveSha256": packages.digest_file(Path(archive))}
            else:
                raise InvalidRequest("Choose a package folder or archive",
                                     code="managed.no_source")
            manifest = load_managed_manifest(package)
            self._refuse_identity_collision(manifest.id)
            artifact = manifest.artifact_for(self.target)
            if artifact is None:
                raise Conflict(
                    f"{manifest.name} has no build for this computer "
                    f"({describe_target(self.target)}). It offers "
                    f"{', '.join(manifest.supported_targets)}.",
                    code="managed.unsupported_target",
                )
            if manifest.endpoint["websocket"] == "required":
                raise Conflict(
                    f"{manifest.name} needs WebSocket connections, which Vela's app "
                    "gateway does not carry yet. It cannot be installed on this version.",
                    code="managed.websocket_required",
                )
            code = stage / "code"
            self._materialise(artifact, package, code)
            executable = packages.executable_in(code, artifact.executable)
            plan = {
                "token": token,
                "stage": stage,
                "package": package,
                "code": code,
                "manifest": manifest,
                "artifact": artifact,
                "source": source,
                "package_digest": packages.digest_tree(package),
                "artifact_digest": packages.digest_tree(code),
                "executable": str(executable),
                "expires": time.monotonic() + REVIEW_SECONDS,
            }
            self._reviews[token] = plan
            return self._review_body(plan)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _materialise(self, artifact, package: Path, code: Path) -> None:
        """Get the verified archive onto this disk and expand it."""
        if artifact.bundled:
            source = package / artifact.file
            packages.verify_artifact(source, sha256=artifact.sha256, size=artifact.size)
        else:
            self.cache.mkdir(parents=True, exist_ok=True)
            suffix = ".zip" if artifact.format == "zip" else ".tar.gz"
            source = self.cache / f"{artifact.sha256}{suffix}"
            if source.is_file():
                try:
                    packages.verify_artifact(source, sha256=artifact.sha256, size=artifact.size)
                except ArtifactError:
                    source.unlink(missing_ok=True)
            if not source.is_file():
                packages.download_artifact(
                    artifact.url, sha256=artifact.sha256, size=artifact.size, destination=source
                )
        packages.extract_artifact(
            source, format=artifact.format, target=code, limit=artifact.expanded_size_limit
        )

    def _refuse_identity_collision(self, app_id: str) -> None:
        """A managed app may not take the name of an app that already exists."""
        if self.registry is not None and self.registry.get(app_id) is not None:
            raise Conflict(
                f"An app called {app_id} is already installed from a Vela package. "
                "A managed web app needs an id of its own.",
                code="managed.identity_taken",
            )
        if self.connected_apps is not None and self.connected_apps.owns(app_id):
            raise Conflict(
                f"A saved website already uses {app_id}.", code="managed.identity_taken"
            )

    def _review_body(self, plan: dict[str, Any]) -> dict[str, Any]:
        manifest: ManagedManifest = plan["manifest"]
        existing = self.store.get(manifest.id)
        licence = None
        if manifest.source.get("licenseFile"):
            licence = packages.read_text_if_present(
                plan["package"] / manifest.source["licenseFile"], 8192
            )
        body = manifest.review(self.target)
        body.update({
            "review": plan["token"],
            "packageDigest": plan["package_digest"],
            "artifactDigest": plan["artifact_digest"],
            "source": {**body["source"], **plan["source"]},
            "licenseText": licence,
            "expiresIn": REVIEW_SECONDS,
            "operation": "update" if existing else "install",
            "installedVersion": existing["version"] if existing else None,
            "addresses": self.addresses(manifest.id),
            "dataPreserved": bool(existing),
        })
        return body

    def cancel_review(self, token: str) -> dict[str, Any]:
        plan = self._reviews.pop(token, None)
        if plan:
            shutil.rmtree(plan["stage"], ignore_errors=True)
        return {"cancelled": True}

    # --------------------------------------------------------------- install --

    def commit(self, token: str, *, artifact_digest: str, package_digest: str,
               trust: str, start_with_vela: bool = False) -> dict[str, Any]:
        """Install or update, having been approved against these exact bytes."""
        plan = self._reviews.get(token)
        if plan is None or plan["expires"] < time.monotonic():
            self.cancel_review(token)
            raise Conflict("That install review has expired. Review the package again.",
                           code="managed.review_expired")
        if trust != "trusted-native":
            raise Precondition(
                "Installing this app means agreeing to run its program with your own "
                "permissions. Approve that to continue.",
                code="managed.trust_not_accepted",
            )
        if artifact_digest != plan["artifact_digest"] or package_digest != plan["package_digest"]:
            raise Conflict(
                "The package changed since it was reviewed. Review it again before installing.",
                code="managed.review_mismatch",
            )
        # Measured again from the staged files, not read back from the plan: the
        # point of binding approval to a digest is lost if the digest compared
        # is the one recorded rather than the one on disk.
        if (packages.digest_tree(plan["package"]) != plan["package_digest"]
                or packages.digest_tree(plan["code"]) != plan["artifact_digest"]):
            raise Conflict(
                "The staged package changed since it was reviewed. Nothing was installed.",
                code="managed.review_mismatch",
            )
        manifest: ManagedManifest = plan["manifest"]
        app_id = manifest.id
        existing = self.store.get(app_id)
        if existing is None:
            self._refuse_identity_collision(app_id)
        kind = "update" if existing else "install"
        self.store.begin_operation(app_id, kind, f"{kind} {manifest.version}")
        release_id = str(uuid.uuid4())
        paths = self.store.paths(app_id)
        snapshot = None
        was_running = bool(existing) and self.supervisor.alive(app_id) is not None
        try:
            self.sessions.revoke_app(app_id)
            if existing:
                self.supervisor.stop(app_id, self._manifest_from(existing))
                snapshot = self._snapshot(
                    app_id, existing, kind="before-update",
                    note=f"Before updating to {manifest.version}",
                )
            self.store.write_journal(app_id, {
                "kind": kind, "release_id": release_id, "stage": "staging",
                "previous_release": existing["release_id"] if existing else None,
                "snapshot_id": snapshot["id"] if snapshot else None,
            })
            release = paths.release(release_id)
            release.mkdir(parents=True, exist_ok=False)
            Path(plan["package"]).rename(release / "package")
            Path(plan["code"]).rename(release / "code")
            paths.data.mkdir(parents=True, exist_ok=True)
            paths.data_directory(manifest.data["directory"]).mkdir(parents=True, exist_ok=True)
            self.store.add_release(
                release_id=release_id,
                app_id=app_id,
                manifest=manifest.raw,
                package_digest=plan["package_digest"],
                artifact_digest=plan["artifact_digest"],
                artifact=plan["artifact"].summary(),
                source=plan["source"],
            )
            record = self.store.create_or_replace(
                app_id=app_id,
                manifest=manifest.raw,
                package_digest=plan["package_digest"],
                artifact_digest=plan["artifact_digest"],
                release_id=release_id,
                start_with_vela=start_with_vela,
            )
            self.store.clear_journal(app_id)
            self._prune_releases(app_id)
            audit("managed", f"{kind} app={app_id} version={manifest.version} "
                             f"digest={plan['artifact_digest'][:12]}")
        except BaseException:
            self.store.clear_journal(app_id)
            shutil.rmtree(paths.release(release_id), ignore_errors=True)
            self.store.drop_release(app_id, release_id)
            self.store.end_operation(app_id)
            self.cancel_review(token)
            raise
        self.store.end_operation(app_id)
        self.cancel_review(token)
        result = {"id": app_id, "installed": True, "version": manifest.version,
                  "release": release_id, "operation": kind,
                  "snapshot": snapshot["id"] if snapshot else None}
        if was_running:
            try:
                self.start(app_id, reason="after update")
                result["restarted"] = True
            except Exception as exc:  # noqa: BLE001 - reported, and recovery is offered
                LOG.warning("managed: %s did not start after %s: %s", app_id, kind, exc)
                result["restarted"] = False
                result["startError"] = str(exc)
        return result

    # ---------------------------------------------------------------- rollback --

    def rollback(self, app_id: str, release_id: str) -> dict[str, Any]:
        """Go back to a retained release, with its matching data.

        Old code against a database a newer release migrated is the failure this
        guards against, so the data goes back with the binary. Whatever was
        written since the checkpoint is replaced, which the dashboard says
        before asking, and a safety snapshot of the current data is taken first
        so the rollback is itself undoable.
        """
        record = self.store.require(app_id)
        target = self.store.release(app_id, release_id)
        if target is None:
            raise NotFound("That release is no longer retained", code="managed.release_missing")
        if record["release_id"] == release_id:
            raise Conflict("That release is already the one running",
                           code="managed.release_active")
        paths = self.store.paths(app_id)
        release_dir = paths.release(release_id)
        if not (release_dir / "code").is_dir():
            raise Conflict(
                "The files for that release are gone, so it cannot be restored.",
                code="managed.release_missing",
            )
        if packages.digest_tree(release_dir / "code") != target["artifact_digest"]:
            raise Conflict(
                "The retained release failed its integrity check and was not used. "
                "Nothing changed.",
                code="managed.release_corrupt",
            )
        snapshots = [item for item in self.store.snapshots_for(app_id)
                     if item["release_id"] == release_id]
        if not snapshots:
            raise Conflict(
                "There is no data snapshot matching that release, so going back to it "
                "could run older code against newer data. Restore a snapshot instead.",
                code="managed.no_matching_snapshot",
            )
        return self._restore(app_id, snapshots[0]["id"], release_id=release_id, kind="rollback")

    # --------------------------------------------------------------- snapshots --

    def snapshot(self, app_id: str, note: str = "") -> dict[str, Any]:
        """A consistent copy of this app's data, taken with the service stopped."""
        record = self.store.require(app_id)
        self.store.begin_operation(app_id, "backup", "Backing up app data")
        running = self.supervisor.alive(app_id) is not None
        try:
            manifest = self._manifest_from(record)
            self.sessions.revoke_app(app_id)
            self.supervisor.stop(app_id, manifest)
            result = self._snapshot(app_id, record, kind="manual",
                                    note=note or "Backup taken from Vela")
        finally:
            self.store.end_operation(app_id)
        if running:
            self.start(app_id, reason="after backup")
        return result

    def _snapshot(self, app_id: str, record: dict[str, Any], *, kind: str, note: str) -> dict[str, Any]:
        """Copy the declared data directory, then verify what was copied.

        Stop-then-copy rather than a live hook, because a SQLite database with a
        write-ahead log is not one file and copying it while something is writing
        produces a copy that opens and is wrong. The service is already stopped
        by the caller; this checks that nothing of ours is still holding it.
        """
        if self.supervisor.alive(app_id) is not None:
            raise Conflict(
                "The app is still running, so its data cannot be copied safely.",
                code="managed.still_running",
            )
        manifest = self._manifest_from(record)
        paths = self.store.paths(app_id)
        source = paths.data_directory(manifest.data["directory"])
        snapshot_id = str(uuid.uuid4())
        target = paths.snapshot(snapshot_id)
        target.mkdir(parents=True, exist_ok=False)
        payload = target / "data"
        try:
            if source.is_dir():
                shutil.copytree(source, payload, symlinks=False, dirs_exist_ok=False)
            else:
                payload.mkdir(parents=True)
            digest = packages.digest_tree(payload)
            files = sum(1 for item in payload.rglob("*") if item.is_file())
            metadata = {
                "id": snapshot_id,
                "app_id": app_id,
                "release_id": record["release_id"],
                "version": record["version"],
                "kind": kind,
                "digest": digest,
                "files": files,
                "size": dir_size(payload),
                "note": note,
                "manifest": record["manifest"],
                "artifactDigest": record["artifact_digest"],
                "dataDirectory": manifest.data["directory"],
            }
            (target / "snapshot.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        stored = self.store.add_snapshot(metadata)
        self._prune_snapshots(app_id)
        audit("managed", f"snapshot app={app_id} kind={kind} files={files}")
        return stored

    def restore(self, app_id: str, snapshot_id: str) -> dict[str, Any]:
        """Put a snapshot back, with the release it was taken against."""
        record = self.store.snapshot(app_id, snapshot_id)
        if record is None:
            raise NotFound("That backup is no longer here", code="managed.snapshot_missing")
        return self._restore(app_id, snapshot_id, release_id=record["release_id"], kind="restore")

    def _restore(self, app_id: str, snapshot_id: str, *, release_id: str, kind: str) -> dict[str, Any]:
        installation = self.store.require(app_id)
        snapshot = self.store.snapshot(app_id, snapshot_id)
        if snapshot is None:
            raise NotFound("That backup is no longer here", code="managed.snapshot_missing")
        paths = self.store.paths(app_id)
        payload = paths.snapshot(snapshot_id) / "data"
        if not payload.is_dir():
            raise Conflict("That backup's files are missing", code="managed.snapshot_missing")
        if packages.digest_tree(payload) != snapshot["digest"]:
            raise Conflict(
                "That backup failed its integrity check and was not used. Nothing changed.",
                code="managed.snapshot_corrupt",
            )
        release = self.store.release(app_id, release_id)
        if release is None or not (paths.release(release_id) / "code").is_dir():
            raise Conflict(
                "The app version that backup belongs to is no longer here, so restoring it "
                "would run this backup against different code.",
                code="managed.release_missing",
            )
        self.store.begin_operation(app_id, kind, "Restoring app data")
        running = self.supervisor.alive(app_id) is not None
        safety = None
        try:
            manifest = self._manifest_from(installation)
            self.sessions.revoke_app(app_id)
            self.supervisor.stop(app_id, manifest)
            safety = self._snapshot(
                app_id, installation, kind="before-restore",
                note=f"Before {kind} of {snapshot['created_at']}",
            )
            self.store.write_journal(app_id, {
                "kind": kind, "snapshot_id": snapshot_id, "release_id": release_id,
                "safety_id": safety["id"], "stage": "replacing",
                "previous_release": installation["release_id"],
            })
            self._replace_data(paths, manifest.data["directory"], payload)
            if release_id != installation["release_id"]:
                self.store.create_or_replace(
                    app_id=app_id,
                    manifest=release["manifest"],
                    package_digest=release["package_digest"],
                    artifact_digest=release["artifact_digest"],
                    release_id=release_id,
                    start_with_vela=installation["start_with_vela"],
                )
            self.store.clear_journal(app_id)
            audit("managed", f"{kind} app={app_id} snapshot={snapshot_id[:8]} release={release_id[:8]}")
        except BaseException:
            self.store.clear_journal(app_id)
            self.store.end_operation(app_id)
            raise
        self.store.end_operation(app_id)
        result = {"id": app_id, "restored": snapshot_id, "release": release_id,
                  "safety": safety["id"] if safety else None, "operation": kind}
        if running:
            try:
                self.start(app_id, reason=f"after {kind}")
                result["restarted"] = True
            except Exception as exc:  # noqa: BLE001 - said plainly rather than hidden
                result["restarted"] = False
                result["startError"] = str(exc)
        return result

    @staticmethod
    def _replace_data(paths, relative: str, payload: Path) -> None:
        """Swap a data directory through two renames, never a partial write.

        A copy straight over the live directory is the version of this that
        loses data when the disk fills half way. The new content is built
        alongside, and only then does the old one move out of the way.
        """
        current = paths.data_directory(relative)
        staged = current.with_name(current.name + ".restoring")
        displaced = current.with_name(current.name + ".replaced")
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(displaced, ignore_errors=True)
        shutil.copytree(payload, staged, symlinks=False)
        # `replace_dir` retries briefly on a Windows PermissionError. A tree
        # written a moment ago is often still held open by a virus scanner or
        # the search indexer, and failing the restore over that would mean
        # failing an operation that succeeds a fraction of a second later.
        if current.exists():
            replace_dir(current, displaced)
        replace_dir(staged, current)
        shutil.rmtree(displaced, ignore_errors=True)

    # ------------------------------------------------------------------ run --

    def start(self, app_id: str, *, reason: str = "requested") -> dict[str, Any]:
        """Start the service, record that running is what the user wants."""
        record = self.store.require(app_id)
        self._refuse_during_operation(app_id, ("install", "update", "restore", "rollback", "remove"))
        manifest = self._manifest_from(record)
        paths = self.store.paths(app_id)
        release = paths.release(record["release_id"])
        artifact = manifest.artifact_for(self.target)
        if artifact is None:
            raise Conflict(
                f"{manifest.name} has no build for this computer "
                f"({describe_target(self.target)}).",
                code="managed.unsupported_target",
            )
        executable = packages.executable_in(release / "code", artifact.executable)
        data = paths.data_directory(manifest.data["directory"])
        data.mkdir(parents=True, exist_ok=True)
        self.store.set_desired(app_id, "running")
        try:
            state = self.supervisor.start(
                app_id, manifest,
                code=release / "code",
                data=data,
                executable=executable,
                generation=record["generation"],
                release_id=record["release_id"],
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            self.store.set_error(app_id, str(exc))
            raise
        self.store.set_error(app_id, None)
        return {"id": app_id, **state.as_dict()}

    def stop(self, app_id: str) -> dict[str, Any]:
        """Stop the service. This is a decision, and it survives a restart."""
        record = self.store.require(app_id)
        self.store.set_desired(app_id, "stopped")
        self.sessions.revoke_app(app_id)
        state = self.supervisor.stop(app_id, self._manifest_from(record))
        self.store.set_error(app_id, None)
        return {"id": app_id, **state.as_dict()}

    def set_start_with_vela(self, app_id: str, enabled: bool) -> dict[str, Any]:
        self.store.require(app_id)
        record = self.store.set_start_with_vela(app_id, enabled)
        return {"id": app_id, "startWithVela": record["start_with_vela"]}

    def _refuse_during_operation(self, app_id: str, kinds: tuple[str, ...]) -> None:
        operation = self.store.operation(app_id)
        if operation and operation["kind"] in kinds:
            raise Conflict(
                f"{operation['kind']} is running for this app; wait for it to finish",
                code="managed.operation_in_progress",
                status=423,
            )

    # --------------------------------------------------------------- removal --

    def remove(self, app_id: str, *, erase_data: bool = False) -> dict[str, Any]:
        """Remove the app. Its data stays unless erasing it was asked for."""
        record = self.store.require(app_id)
        self.store.begin_operation(app_id, "remove", "Removing this app")
        paths = self.store.paths(app_id)
        try:
            manifest = self._manifest_from(record)
            self.sessions.revoke_app(app_id)
            self.supervisor.stop(app_id, manifest)
            self.supervisor.forget_entirely(app_id)
            for release in self.store.releases_for(app_id):
                shutil.rmtree(paths.release(release["id"]), ignore_errors=True)
                self.store.drop_release(app_id, release["id"])
            shutil.rmtree(paths.package, ignore_errors=True)
            data = paths.data_directory(manifest.data["directory"])
            retained = None
            if erase_data:
                for snapshot in self.store.snapshots_for(app_id):
                    shutil.rmtree(paths.snapshot(snapshot["id"]), ignore_errors=True)
                    self.store.drop_snapshot(app_id, snapshot["id"])
                shutil.rmtree(paths.data, ignore_errors=True)
            else:
                retained = {"path": str(data), "size": dir_size(data) if data.is_dir() else 0}
            audit("managed", f"remove app={app_id} erased={bool(erase_data)}")
        finally:
            self.store.end_operation(app_id)
        self.store.forget(app_id)
        if erase_data:
            shutil.rmtree(paths.root, ignore_errors=True)
        return {"id": app_id, "installed": False, "dataErased": bool(erase_data),
                "retainedData": retained}

    def erase_data(self, app_id: str) -> dict[str, Any]:
        """Delete this app's data, as its own deliberate action."""
        record = self.store.require(app_id)
        manifest = self._manifest_from(record)
        self.store.begin_operation(app_id, "erase", "Erasing app data")
        try:
            self.sessions.revoke_app(app_id)
            self.supervisor.stop(app_id, manifest)
            paths = self.store.paths(app_id)
            data = paths.data_directory(manifest.data["directory"])
            size = dir_size(data) if data.is_dir() else 0
            shutil.rmtree(data, ignore_errors=True)
            data.mkdir(parents=True, exist_ok=True)
            audit("managed", f"erase app={app_id} bytes={size}")
        finally:
            self.store.end_operation(app_id)
        return {"id": app_id, "erased": True, "bytes": size}

    def data_usage(self, app_id: str) -> dict[str, Any]:
        record = self.store.require(app_id)
        manifest = self._manifest_from(record)
        data = self.store.paths(app_id).data_directory(manifest.data["directory"])
        return {
            "path": str(data),
            "size": dir_size(data) if data.is_dir() else 0,
            "describe": manifest.data.get("describe", ""),
        }

    # ------------------------------------------------------------- reporting --

    def summary(self, record: dict[str, Any]) -> dict[str, Any]:
        app_id = record["app_id"]
        try:
            manifest = self._manifest_from(record)
        except ManagedManifestError:
            manifest = None
        state = self.supervisor.state(app_id, record)
        operation = self.store.operation(app_id)
        running = state.state in ("ready", "starting")
        view = manifest.view if manifest else {"surface": "managed-web", "chrome": "compact"}
        return {
            "id": app_id,
            "schemaVersion": 3,
            "profile": "managed-web",
            "name": record["name"],
            "version": record["version"],
            "description": manifest.description if manifest else "",
            "category": manifest.category if manifest else "Apps",
            "author": manifest.author if manifest else "",
            "color": (manifest.color if manifest else None) or "#7b4dff",
            "installed": True,
            "running": running,
            "supported": bool(manifest and manifest.supports(self.target)),
            "runtime": "managed-service",
            "runtimes": ["managed-service"],
            "isolation": "trusted-native",
            "capabilities": [],
            "widgets": [],
            "unavailableCapabilities": [],
            "view": view,
            "url": f"/app/{app_id}",
            "managed": {
                "state": state.state,
                "detail": state.detail or (record.get("last_error") or ""),
                "desired": record["desired"],
                "startWithVela": record["start_with_vela"],
                "generation": record["generation"],
                "installationId": record["installation_id"],
                "releaseId": record["release_id"],
                "operation": operation["kind"] if operation else None,
                "origin": self.origin_for(app_id),
                "addresses": self.addresses(app_id),
                "embedding": view.get("embedding", "auto"),
                "port": state.port,
                "pid": state.pid,
                "startedAt": state.started_at,
                "restarts": state.restarts,
                "sessions": self.sessions.count(app_id),
            },
        }

    def list_apps(self) -> list[dict[str, Any]]:
        return [self.summary(record) for record in self.store.all()]

    def describe(self, app_id: str) -> dict[str, Any]:
        record = self.store.require(app_id)
        summary = self.summary(record)
        manifest = self._manifest_from(record)
        summary["manifest"] = record["manifest"]
        summary["managed"].update({
            "source": manifest.source,
            "trust": manifest.review(self.target)["trust"],
            "data": self.data_usage(app_id),
            "artifact": (manifest.artifact_for(self.target).summary()
                         if manifest.artifact_for(self.target) else None),
            "supportedTargets": manifest.supported_targets,
            "target": describe_target(self.target),
            "releases": [
                {"id": item["id"], "version": item["version"], "createdAt": item["created_at"],
                 "active": item["id"] == record["release_id"], "source": item["source"]}
                for item in self.store.releases_for(app_id)
            ],
            "snapshots": [
                {"id": item["id"], "version": item["version"], "kind": item["kind"],
                 "createdAt": item["created_at"], "size": item["size"], "files": item["files"],
                 "note": item["note"], "releaseId": item["release_id"]}
                for item in self.store.snapshots_for(app_id)
            ],
            "logFile": self.supervisor.log_path(app_id).name,
        })
        return summary

    def status(self, app_id: str) -> dict[str, Any]:
        return self.summary(self.store.require(app_id))

    # ----------------------------------------------------------- the gateway --

    def resolve_for_gateway(self, app_id: str) -> dict[str, Any] | None:
        """What the gateway needs to answer a request on this app's host."""
        record = self.store.get(app_id)
        if record is None:
            return None
        try:
            manifest = self._manifest_from(record)
        except ManagedManifestError:
            return None
        state = self.supervisor.state(app_id, record)
        return {
            "app_id": app_id,
            "manifest": manifest,
            "generation": record["generation"],
            "endpoint": self.supervisor.endpoint(app_id),
            "state": state.state,
        }

    def launch(self, app_id: str, *, owner: str | None, path: str | None = None,
               start: bool = True) -> dict[str, Any]:
        """Start the app if needed and hand back a one-use link into it."""
        record = self.store.require(app_id)
        self._refuse_during_operation(app_id, ("install", "update", "restore", "rollback", "remove"))
        if start and self.supervisor.endpoint(app_id) is None:
            self.start(app_id, reason="opened")
            record = self.store.require(app_id)
        host = self.host_for(app_id)
        port = self.config.app_gateway_port
        suffix = "" if port in (80, None) else f":{port}"
        ticket = self.sessions.issue_ticket(
            app_id=app_id,
            generation=record["generation"],
            owner=owner,
            host=f"{host}{suffix}",
            path=launch_path(path),
        )
        origin = self.origin_for(app_id)
        return {
            "id": app_id,
            "origin": origin,
            "url": f"{origin}/_vela/enter?t={ticket}",
            "expiresIn": 30,
            "embedding": self._manifest_from(record).view.get("embedding", "auto"),
            "addresses": self.addresses(app_id),
        }

    def revoke_owner(self, owner: str | None) -> int:
        """Sign-out, lock or session expiry: every gateway session it opened ends."""
        return self.sessions.revoke_owner(owner)

    # ------------------------------------------------------------- lifecycle --

    def recover(self) -> list[dict[str, Any]]:
        """Undo anything a crash left half done, before anything auto-starts.

        Two kinds of leftovers. An install or update that did not reach its
        database commit has files and no record, so the files go. A restore that
        was interrupted has a data directory that may be neither version, so the
        staged and displaced directories are resolved in the one order that can
        be reasoned about, and the safety copy is put back when there is any
        doubt.
        """
        notes: list[dict[str, Any]] = []
        for app_id, journal in self.store.journals():
            try:
                notes.append(self._recover_one(app_id, journal))
            except Exception as exc:  # noqa: BLE001 - one app must not stop the rest
                LOG.exception("managed: recovery failed for %s", app_id)
                notes.append({"id": app_id, "outcome": "failed", "detail": str(exc)})
                self.store.set_error(app_id, f"Recovery after a restart failed: {exc}")
        for operation in self.store.operations():
            # An operation row with no journal is one whose process died between
            # claiming the lock and doing anything. Releasing it is safe and is
            # the difference between an app that works and one that reports
            # "already running" for ever.
            self.store.end_operation(operation["app_id"])
        self._clean_orphan_releases()
        return notes

    def _recover_one(self, app_id: str, journal: dict[str, Any]) -> dict[str, Any]:
        record = self.store.get(app_id)
        paths = self.store.paths(app_id)
        kind = journal.get("kind")
        if kind in ("install", "update"):
            release_id = journal.get("release_id")
            committed = record and record["release_id"] == release_id
            if not committed and release_id:
                shutil.rmtree(paths.release(release_id), ignore_errors=True)
                self.store.drop_release(app_id, release_id)
            self.store.clear_journal(app_id)
            outcome = "completed" if committed else "rolled back"
            if not committed:
                self.store.set_error(
                    app_id,
                    f"The last {kind} did not finish and was undone. Nothing was changed.",
                )
            return {"id": app_id, "operation": kind, "outcome": outcome}
        if kind in ("restore", "rollback"):
            outcome = self._recover_restore(app_id, journal, record, paths)
            self.store.clear_journal(app_id)
            return {"id": app_id, "operation": kind, "outcome": outcome}
        self.store.clear_journal(app_id)
        return {"id": app_id, "operation": kind or "unknown", "outcome": "cleared"}

    def _recover_restore(self, app_id, journal, record, paths) -> str:
        if record is None:
            return "cleared"
        manifest = self._manifest_from(record)
        current = paths.data_directory(manifest.data["directory"])
        staged = current.with_name(current.name + ".restoring")
        displaced = current.with_name(current.name + ".replaced")
        if current.exists():
            # The second rename either never started or already finished.
            shutil.rmtree(staged, ignore_errors=True)
            shutil.rmtree(displaced, ignore_errors=True)
            outcome = "completed"
        elif staged.exists():
            staged.rename(current)
            shutil.rmtree(displaced, ignore_errors=True)
            outcome = "completed"
        elif displaced.exists():
            displaced.rename(current)
            outcome = "rolled back"
        else:
            safety = journal.get("safety_id")
            payload = paths.snapshot(safety) / "data" if safety else None
            if payload and payload.is_dir():
                shutil.copytree(payload, current, symlinks=False)
                outcome = "recovered from the safety copy"
            else:
                current.mkdir(parents=True, exist_ok=True)
                self.store.set_error(
                    app_id,
                    "A restore was interrupted and this app's data could not be put back. "
                    "Restore a backup before starting it.",
                )
                outcome = "needs a backup"
        self.store.set_desired(app_id, "stopped")
        return outcome

    def _clean_orphan_releases(self) -> None:
        """Release directories with no database row: an interrupted extraction."""
        if not self.store.root.is_dir():
            return
        for folder in self.store.root.iterdir():
            if not folder.is_dir():
                continue
            try:
                app_id = safe_app_id(folder.name)
            except NotFound:
                continue
            known = {item["id"] for item in self.store.releases_for(app_id)}
            releases = folder / "releases"
            if not releases.is_dir():
                continue
            for release in releases.iterdir():
                if release.is_dir() and release.name not in known:
                    LOG.info("managed: removing an unfinished release for %s", app_id)
                    shutil.rmtree(release, ignore_errors=True)

    def start_enabled(self) -> list[dict[str, Any]]:
        """Start what the user asked to come back with Vela, and adopt the rest.

        `startWithVela` is the user's setting; an explicit Stop is the user's
        decision. A stopped app with the setting on stays stopped, because the
        setting says what to do when Vela starts *this app*, not what to do
        against a choice already made.
        """
        started = []
        for record in self.store.all():
            app_id = record["app_id"]
            try:
                manifest = self._manifest_from(record)
            except ManagedManifestError:
                continue
            if self.supervisor.adopt(app_id, manifest, record["generation"]):
                started.append({"id": app_id, "outcome": "adopted"})
                continue
            if not record["start_with_vela"] or record["desired"] != "running":
                self.supervisor.forget(app_id)
                continue
            try:
                self.start(app_id, reason="start with Vela")
                started.append({"id": app_id, "outcome": "started"})
            except Exception as exc:  # noqa: BLE001 - recorded, and Vela still starts
                LOG.warning("managed: %s did not start with Vela: %s", app_id, exc)
                self.store.set_error(app_id, str(exc))
                started.append({"id": app_id, "outcome": "failed", "detail": str(exc)})
        return started

    def reconcile(self) -> None:
        """One tick: restart what crashed, within its budget."""
        for record in self.store.all():
            app_id = record["app_id"]
            if record["desired"] != "running" or self.supervisor.busy(app_id):
                continue
            if self.store.operation(app_id) is not None:
                continue
            if not self.supervisor.crashed(app_id):
                continue
            try:
                manifest = self._manifest_from(record)
            except ManagedManifestError:
                continue
            allowed, delay, reason = self.supervisor.may_restart(app_id, manifest)
            self.supervisor.forget(app_id)
            if not allowed:
                self.store.set_desired(app_id, "stopped")
                self.store.set_error(app_id, reason)
                self.supervisor.note_failure(app_id, reason)
                self.sessions.revoke_app(app_id)
                LOG.warning("managed: %s", reason)
                if self._notify is not None:
                    self._notify(app_id, reason)
                continue
            self.supervisor.note_restart(app_id)
            time.sleep(min(delay, 5.0))
            try:
                self.start(app_id, reason="restart after it stopped")
            except Exception as exc:  # noqa: BLE001 - the next tick tries again
                LOG.warning("managed: could not restart %s: %s", app_id, exc)

    def stop_all(self) -> list[str]:
        """Stop every managed service, keeping each app's startup preference."""
        manifests = {}
        for record in self.store.all():
            try:
                manifests[record["app_id"]] = self._manifest_from(record)
            except ManagedManifestError:
                continue
        stopped = self.supervisor.stop_all(manifests)
        self.sessions.revoke_owner(None)
        return stopped

    # ---------------------------------------------------------------- pruning --

    def _prune_releases(self, app_id: str) -> None:
        record = self.store.get(app_id)
        if record is None:
            return
        paths = self.store.paths(app_id)
        keep = {record["release_id"]}
        ordered = [item for item in self.store.releases_for(app_id)
                   if item["id"] not in keep][:KEEP_RELEASES]
        keep.update(item["id"] for item in ordered)
        for item in self.store.releases_for(app_id):
            if item["id"] in keep:
                continue
            shutil.rmtree(paths.release(item["id"]), ignore_errors=True)
            self.store.drop_release(app_id, item["id"])
            for snapshot in self.store.snapshots_for(app_id):
                if snapshot["release_id"] == item["id"] and snapshot["kind"] != "manual":
                    shutil.rmtree(paths.snapshot(snapshot["id"]), ignore_errors=True)
                    self.store.drop_snapshot(app_id, snapshot["id"])

    def _prune_snapshots(self, app_id: str) -> None:
        paths = self.store.paths(app_id)
        automatic = [item for item in self.store.snapshots_for(app_id) if item["kind"] != "manual"]
        for item in automatic[KEEP_AUTO_SNAPSHOTS:]:
            shutil.rmtree(paths.snapshot(item["id"]), ignore_errors=True)
            self.store.drop_snapshot(app_id, item["id"])
