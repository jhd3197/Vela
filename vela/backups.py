"""Timestamped hub backups with retention and an isolated restore drill."""

import json
import re
import shutil
import tempfile
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config, dir_size
from .manifest import ManifestError, load_manifest

KEEP_BACKUPS = 10
_NAME_RE = re.compile(r"^\d{8}-\d{6}$")
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


class BackupError(Exception):
    """Backup creation or verification failure."""


class BackupStore:
    """Backups under data_dir/backups/<YYYYMMDD-HHMMSS>/.

    Each backup copies state.json, settings.json, and every installed app's
    app.json manifest (logs are skipped). Verification is a restore *drill*:
    the backup is copied into an isolated temp dir and validated there — live
    files are never touched.
    """

    def __init__(self, config: Config, keep: int = KEEP_BACKUPS):
        self._config = config
        self._keep = keep
        self._dir = config.data_dir / "backups"

    def create(self) -> dict[str, Any]:
        name = datetime.now().strftime(_TIMESTAMP_FORMAT)
        target = self._dir / name
        if target.exists():
            raise BackupError("a backup with this timestamp already exists; try again in a second")
        target.mkdir(parents=True)
        try:
            for filename in ("state.json", "settings.json"):
                source = self._config.data_dir / filename
                if source.is_file():
                    shutil.copy2(source, target / filename)
            installed = self._config.installed_dir
            database = self._config.data_dir / "app-data.sqlite"
            if database.is_file():
                source_db = sqlite3.connect(database)
                target_db = sqlite3.connect(target / "app-data.sqlite")
                try:
                    source_db.backup(target_db)
                finally:
                    target_db.close()
                    source_db.close()
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
            "created_at": datetime.strptime(name, _TIMESTAMP_FORMAT).isoformat(timespec="seconds"),
            "size": dir_size(target),
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
                    "created_at": datetime.strptime(child.name, _TIMESTAMP_FORMAT).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        return sorted(entries, key=lambda e: e["name"], reverse=True)

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
            database = restored / "app-data.sqlite"
            if database.is_file():
                db = None
                try:
                    db = sqlite3.connect(database)
                    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise sqlite3.DatabaseError(integrity)
                    for row in db.execute("SELECT value FROM documents"):
                        json.loads(row[0])
                    files.append({"file": "app-data.sqlite", "ok": True})
                except (sqlite3.Error, json.JSONDecodeError) as exc:
                    files.append({"file": "app-data.sqlite", "ok": False, "error": str(exc)})
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

    def _prune(self) -> None:
        for old in self.list()[self._keep :]:
            shutil.rmtree(self._dir / old["name"], ignore_errors=True)
