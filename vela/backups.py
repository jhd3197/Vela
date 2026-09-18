"""Timestamped hub backups with retention, an isolated restore drill, a
schedule, and a restore that puts a copy back.

A restore replaces the hub's own files — settings, app state, app storage and
the installed manifests. It never touches the logs, and it always makes a
safety backup of what it is about to replace, so "I restored the wrong one" is
recoverable. Running process apps are stopped first, because replacing the
storage under a running app is how data gets lost, and the ones that were
running are started again afterwards.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .automations.schedules import next_occurrence, resolve_timezone
from .config import Config, dir_size
from .logging_setup import audit
from .manifest import ManifestError, load_manifest

KEEP_BACKUPS = 10
#: The files a backup covers, and therefore the files a restore replaces.
#: `desktops.sqlite` holds how each desk is arranged and dressed. That used to
#: live in settings.json, so leaving it out would quietly make a restore stop
#: bringing the wallpaper back. The uploaded images themselves are not here:
#: they never were, and a desktop whose picture is missing falls back to a
#: painted one rather than failing.
#:
#: An allowlist rather than an exclusion list, which is what keeps two things
#: out by construction: an agent desktop's staged files, which are a
#: half-finished transfer rather than somebody's documents, and its kept website
#: sign-ins, which are the nearest thing in the data directory to a password. A
#: backup is not a copy of a browser session.
BACKED_UP_FILES = ("state.json", "settings.json", "app-data.sqlite", "desktops.sqlite")

#: Directories a backup copies whole, file by file. `themes/` holds the
#: themes a user imported -- somebody else's work that they chose to keep, and
#: nowhere else on the disk. Bundled themes are not here: they ship with the
#: server and would only go stale inside a backup.
BACKED_UP_DIRECTORIES = ("themes",)

#: The SQLite databases a backup copies through the engine rather than the file
#: system, so a write in progress cannot produce a torn copy.
BACKED_UP_DATABASES = ("app-data.sqlite", "desktops.sqlite")
#: A safety copy taken immediately before a restore.
SAFETY_PREFIX = "pre-restore-"
DEFAULT_SCHEDULE = {"enabled": False, "time": "03:00", "keep": KEEP_BACKUPS}
#: How many pre-restore safety copies to keep, counted separately from the rest.
KEEP_SAFETY = 3
# A second copy taken inside the same second gets a "-2", "-3" … suffix, so
# two restores in quick succession cannot fail over a name collision.
_NAME_RE = re.compile(r"^(?:pre-restore-)?\d{8}-\d{6}(?:-\d+)?$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def _theme_reads(path) -> bool:
    """Whether a backed-up theme still reads as one this version accepts.

    Imported inside the function: `vela.themes` reads the token whitelist the
    dashboard's build exports, and backups must keep working on a server whose
    assets are being rebuilt.
    """
    try:
        from .themes import validate_document

        validate_document(json.loads(path.read_text(encoding="utf-8")))
        return True
    except Exception:  # noqa: BLE001 - any failure means "not a theme any more"
        return False


class BackupError(Exception):
    """Backup creation or verification failure."""


class BackupStore:
    """Backups under data_dir/backups/<YYYYMMDD-HHMMSS>/.

    Each backup copies state.json, settings.json, desktops.sqlite, the app data
    and every installed app's app.json manifest (logs are skipped). Verification
    is a restore *drill*:
    the backup is copied into an isolated temp dir and validated there — live
    files are never touched.
    """

    def __init__(self, config: Config, keep: int = KEEP_BACKUPS):
        self._config = config
        self._keep = keep
        self._dir = config.data_dir / "backups"

    def create(self, *, prefix: str = "") -> dict[str, Any]:
        stamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
        name, target = "", None
        for attempt in range(1, 50):
            name = prefix + stamp + ("" if attempt == 1 else f"-{attempt}")
            target = self._dir / name
            if not target.exists():
                break
        else:
            raise BackupError("too many backups in the same second; try again")
        target.mkdir(parents=True)
        try:
            for filename in ("state.json", "settings.json"):
                source = self._config.data_dir / filename
                if source.is_file():
                    shutil.copy2(source, target / filename)
            installed = self._config.installed_dir
            for filename in BACKED_UP_DATABASES:
                database = self._config.data_dir / filename
                if not database.is_file():
                    continue
                source_db = sqlite3.connect(database)
                target_db = sqlite3.connect(target / filename)
                try:
                    source_db.backup(target_db)
                finally:
                    target_db.close()
                    source_db.close()
            for folder in BACKED_UP_DIRECTORIES:
                source_dir = self._config.data_dir / folder
                if not source_dir.is_dir():
                    continue
                for item in sorted(source_dir.glob("*.json")):
                    destination = target / folder
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, destination / item.name)
            if installed.is_dir():
                for app_dir in sorted(installed.iterdir()):
                    manifest_file = app_dir / "app.json"
                    if app_dir.is_dir() and manifest_file.is_file():
                        destination = target / "installed" / app_dir.name
                        destination.mkdir(parents=True)
                        shutil.copy2(manifest_file, destination / "app.json")
        except (OSError, sqlite3.Error) as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise BackupError(f"backup failed: {exc}") from exc
        self._prune()
        return {
            "name": name,
            "created_at": _created_at(name),
            "size": dir_size(target),
            "safety": name.startswith(SAFETY_PREFIX),
        }

    def list(self) -> list[dict[str, Any]]:
        entries = []
        if not self._dir.is_dir():
            return entries
        for child in self._dir.iterdir():
            if not child.is_dir() or not _NAME_RE.match(child.name):
                continue
            entries.append(
                {
                    "name": child.name,
                    "size": dir_size(child),
                    "created_at": _created_at(child.name),
                    # A safety copy is taken by a restore, not asked for. It is
                    # listed so it can be restored from, and marked so it is
                    # not mistaken for one the user made.
                    "safety": child.name.startswith(SAFETY_PREFIX),
                }
            )
        return sorted(entries, key=lambda e: (e["created_at"], e["name"]), reverse=True)

    def verify(self, name: str) -> dict[str, Any]:
        """Restore drill: copy the backup into an isolated temp dir and validate it."""
        if not _NAME_RE.match(name):
            raise BackupError("invalid backup name")
        source = self._dir / name
        if not source.is_dir():
            raise BackupError(f"backup not found: {name}")
        sandbox = Path(tempfile.mkdtemp(prefix="vela-restore-"))
        try:
            restored = sandbox / name
            shutil.copytree(source, restored)
            ok = True
            files = []
            for filename in BACKED_UP_DATABASES:
                database = restored / filename
                if not database.is_file():
                    continue
                db = None
                try:
                    db = sqlite3.connect(database)
                    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise sqlite3.DatabaseError(integrity)
                    if filename == "app-data.sqlite":
                        for row in db.execute("SELECT value FROM documents"):
                            json.loads(row[0])
                    else:
                        # A desktop file that cannot list its desktops would
                        # restore a dashboard with nowhere to go.
                        db.execute("SELECT id, name FROM desktops").fetchall()
                        for row in db.execute("SELECT widgets FROM desktop_boards"):
                            json.loads(row[0])
                    files.append({"file": filename, "ok": True})
                except (sqlite3.Error, json.JSONDecodeError) as exc:
                    files.append({"file": filename, "ok": False, "error": str(exc)})
                    ok = False
                finally:
                    if db is not None:
                        db.close()
            for json_file in sorted(restored.rglob("*.json")):
                relative = str(json_file.relative_to(restored))
                try:
                    json.loads(json_file.read_text(encoding="utf-8"))
                    files.append({"file": relative, "ok": True})
                except (OSError, json.JSONDecodeError) as exc:
                    files.append({"file": relative, "ok": False, "error": str(exc)})
                    ok = False
            manifests = []
            installed = restored / "installed"
            if installed.is_dir():
                for app_dir in sorted(installed.iterdir()):
                    if not app_dir.is_dir():
                        continue
                    try:
                        manifest = load_manifest(app_dir)
                        manifests.append({"id": manifest.id, "ok": True})
                    except ManifestError as exc:
                        manifests.append({"id": app_dir.name, "ok": False, "error": str(exc)})
                        ok = False
            return {
                "name": name,
                "ok": ok,
                "verified_at": datetime.now().isoformat(timespec="seconds"),
                "files": files,
                "manifests": manifests,
            }
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def set_keep(self, keep: int) -> None:
        """How many backups to keep, from the stored schedule."""
        self._keep = max(1, int(keep))

    def stats(self) -> dict[str, Any]:
        """How many backups there are, how much room they take, and the newest."""
        entries = self.list()
        newest = next((entry for entry in entries if not entry["safety"]), None)
        return {
            "count": len(entries),
            "totalSize": sum(entry["size"] for entry in entries),
            "lastSuccessAt": newest["created_at"] if newest else None,
            "lastName": newest["name"] if newest else None,
            "keep": self._keep,
        }

    def restore(self, name: str, *, lifecycle=None, actor: str = "local") -> dict[str, Any]:
        """Put a backup back, after verifying it and copying what it replaces.

        Order matters and is the whole point: verify before anything is
        touched, stop the apps that are writing, take the safety copy, then
        replace. A failure at any step leaves the previous state in place, and
        the safety copy is a real backup that can itself be restored.
        """
        if not _NAME_RE.match(name or ""):
            raise BackupError("invalid backup name")
        source = self._dir / name
        if not source.is_dir():
            raise BackupError(f"backup not found: {name}")

        # 1. Never restore something that does not read. The drill runs in an
        #    isolated temp directory and touches nothing live.
        drill = self.verify(name)
        if not drill["ok"]:
            raise BackupError(
                f"{name} did not verify, so nothing was changed. Try another backup."
            )

        # 2. Stop the apps that are writing to what is about to be replaced,
        #    remembering which ones to start again.
        stopped: list[str] = []
        if lifecycle is not None:
            for app in self._running_process_apps(lifecycle):
                try:
                    lifecycle.stop_app(app)
                    stopped.append(app)
                except Exception:  # noqa: BLE001 - a stop failure is reported below
                    raise BackupError(
                        f"could not stop {app}, so nothing was restored. Stop it and try again."
                    ) from None

        # 3. A copy of what is being replaced, so this is undoable.
        safety = self.create(prefix=SAFETY_PREFIX)

        restored: list[str] = []
        skipped: list[str] = []
        try:
            for filename in BACKED_UP_FILES:
                copy = source / filename
                if copy.is_file():
                    shutil.copy2(copy, self._config.data_dir / filename)
                    restored.append(filename)
            # A theme is read again on the way back in. One that this version
            # no longer accepts is left out and named in the report rather than
            # restored into a dashboard that would skip it silently for ever --
            # and rather than being dropped without saying so, because it is
            # somebody's work.
            for folder in BACKED_UP_DIRECTORIES:
                copied = source / folder
                if not copied.is_dir():
                    continue
                destination = self._config.data_dir / folder
                destination.mkdir(parents=True, exist_ok=True)
                for item in sorted(copied.glob("*.json")):
                    if folder == "themes" and not _theme_reads(item):
                        skipped.append(f"{folder}/{item.name}")
                        continue
                    shutil.copy2(item, destination / item.name)
                    restored.append(f"{folder}/{item.name}")
            installed = source / "installed"
            if installed.is_dir():
                for app_dir in sorted(installed.iterdir()):
                    manifest_file = app_dir / "app.json"
                    if not app_dir.is_dir() or not manifest_file.is_file():
                        continue
                    destination = self._config.installed_dir / app_dir.name
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(manifest_file, destination / "app.json")
                    restored.append(f"installed/{app_dir.name}/app.json")
        except OSError as exc:
            raise BackupError(
                f"restore failed part-way: {exc}. The copy taken first is {safety['name']}."
            ) from exc

        # 4. Start again what was running. A failure here is worth reporting
        #    but the restore itself has already succeeded.
        restarted: list[str] = []
        failed: list[str] = []
        for app in stopped:
            try:
                if lifecycle is not None:
                    lifecycle.launch_app(app)
                restarted.append(app)
            except Exception:  # noqa: BLE001 - the app can be opened by hand
                failed.append(app)

        audit(
            "restore",
            f"backup={name} safety={safety['name']} files={len(restored)} "
            f"skipped={len(skipped)}",
            actor=actor,
        )
        return {
            "name": name,
            "restored": restored,
            # Anything in the backup this version could not take back, named.
            # A restore that quietly drops somebody's theme is a restore that
            # lied about what it did.
            "skipped": skipped,
            "safety": safety["name"],
            "stopped": stopped,
            "restarted": restarted,
            "failedToRestart": failed,
            "restored_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _running_process_apps(self, lifecycle) -> list[str]:
        """The process apps running right now, by id."""
        try:
            return sorted(
                app["id"]
                for app in lifecycle.registry.list_apps()
                if app.get("running") and app.get("runtime") == "process"
            )
        except Exception:  # noqa: BLE001 - a restore must not fail over a listing
            return []

    def _prune(self) -> None:
        entries = self.list()
        # Safety copies are pruned on their own count. Otherwise a few ordinary
        # backups taken after a restore would push out the very copy the
        # restore made to protect the user.
        ordinary = [entry for entry in entries if not entry["safety"]]
        safety = [entry for entry in entries if entry["safety"]]
        for old in ordinary[self._keep :] + safety[KEEP_SAFETY:]:
            shutil.rmtree(self._dir / old["name"], ignore_errors=True)


def _created_at(name: str) -> str:
    """The moment a backup name stands for, prefix and suffix or not."""
    stamp = name[len(SAFETY_PREFIX) :] if name.startswith(SAFETY_PREFIX) else name
    # "20260916-004706-2" is the same second as "20260916-004706".
    stamp = stamp[:15]
    return datetime.strptime(stamp, _TIMESTAMP_FORMAT).isoformat(timespec="seconds")


def validate_schedule(schedule: Any) -> dict[str, Any]:
    """One stored schedule, checked and normalised, or a reason it cannot be.

    Follows ServerKit's `backup_schedule_service.validate_schedule` (MIT, same
    owner) in shape. Vela's schedule is daily only, so the weekday list is not
    carried over, and the arithmetic reuses `automations.schedules` rather than
    a second implementation of the same clock-change handling.
    """
    if schedule is None:
        # Never configured. That is the default, not a mistake to report.
        return dict(DEFAULT_SCHEDULE)
    if not isinstance(schedule, dict):
        raise BackupError("a backup schedule is a set of options")
    time_value = schedule.get("time", DEFAULT_SCHEDULE["time"])
    if not isinstance(time_value, str) or not _TIME_RE.match(time_value):
        raise BackupError("a backup time looks like 03:00")
    try:
        keep = int(schedule.get("keep", DEFAULT_SCHEDULE["keep"]))
    except (TypeError, ValueError):
        raise BackupError("how many backups to keep must be a number") from None
    if not 1 <= keep <= 100:
        raise BackupError("keep between 1 and 100 backups")
    return {"enabled": bool(schedule.get("enabled", False)), "time": time_value, "keep": keep}


def next_run(schedule: Any, *, after: datetime | None = None) -> datetime | None:
    """When the next scheduled backup is due, in this computer's timezone.

    None when the schedule is off or unusable. The arithmetic is Vela's own
    `automations.schedules.next_occurrence`, which already resolves a clock
    change in either direction, so a backup at 03:00 behaves the same way an
    automation at 03:00 does.
    """
    try:
        normalised = validate_schedule(schedule)
    except BackupError:
        return None
    if not normalised["enabled"]:
        return None
    tz, _ = resolve_timezone(None)
    after = after or datetime.now(tz)
    if after.tzinfo is None:
        raise ValueError("after must carry a timezone")
    # Pass None, not the resolved name: on Windows the local zone reports a
    # display name ("Eastern Daylight Time") that is not an IANA key, and
    # `resolve_timezone` would refuse to look it up again.
    due = next_occurrence({"every": 1, "unit": "days", "atTime": normalised["time"]}, None, after)
    return due.astimezone(tz)


def describe_schedule(schedule: Any) -> dict[str, Any]:
    """The schedule as the dashboard shows it, including when it next runs."""
    try:
        normalised = validate_schedule(schedule)
        error = None
    except BackupError as exc:
        normalised = dict(DEFAULT_SCHEDULE)
        error = str(exc)
    _, zone = resolve_timezone(None)
    due = next_run(normalised)
    return {
        **normalised,
        "timezone": zone,
        "nextRunAt": due.isoformat(timespec="seconds") if due else None,
        **({"error": error} if error else {}),
    }
