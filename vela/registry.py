"""Registry: apps bundled in apps/ merged with install/run state from the data dir."""

import logging
import shutil
import re
import uuid
from pathlib import Path
from typing import Any

from .config import Config
from .manifest import DEFAULT_APP_COLOR, Manifest, ManifestError, load_manifest
from .runners import current_platform
from .state import StateStore
from .package_files import copy_package
from .app_storage import AppServiceError

logger = logging.getLogger(__name__)


class Registry:
    def __init__(self, config: Config, state: StateStore, platform: str | None = None):
        self._config = config
        self._state = state
        self.platform = platform or current_platform()
        self.catalog = None

    def manifests(self) -> dict[str, Manifest]:
        """Installed manifests win over source ones, so installed-only apps stay listed."""
        found: dict[str, Manifest] = {}
        if self.catalog:
            found.update({key: value['parsed'] for key, value in self.catalog.entries.items()})
        if self._config.apps_dir.is_dir():
            for app_dir in sorted(self._config.apps_dir.iterdir()):
                if not app_dir.is_dir():
                    continue
                try:
                    manifest = load_manifest(app_dir)
                except ManifestError:
                    continue
                found[manifest.id] = manifest
        if self._config.installed_dir.is_dir():
            for app_dir in self._config.installed_dir.iterdir():
                if (app_dir / "app.json").exists():
                    found.pop(app_dir.name, None)
        for manifest in self._installed_manifests().values():
            found[manifest.id] = manifest
        return found

    def _installed_manifests(self) -> dict[str, Manifest]:
        found: dict[str, Manifest] = {}
        if not self._config.installed_dir.is_dir():
            return found
        for app_dir in sorted(self._config.installed_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            try:
                manifest = load_manifest(app_dir)
            except ManifestError as exc:
                logger.warning("skipping installed app: %s", exc)
                continue
            found[manifest.id] = manifest
        return found

    def get(self, app_id: str) -> Manifest | None:
        """The installed manifest when installed, else the source manifest."""
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", app_id):
            return None
        installed = self.installed_path(app_id)
        if (installed / "app.json").is_file():
            try:
                return load_manifest(installed)
            except ManifestError:
                logger.warning("invalid installed manifest for %s; refusing source fallback", app_id)
                return None
        app_dir = self._config.apps_dir / app_id
        if not app_dir.is_dir():
            entry = self.catalog.entries.get(app_id) if self.catalog else None
            return entry['parsed'] if entry else None
        try:
            return load_manifest(app_dir)
        except ManifestError:
            return None

    def installed_path(self, app_id: str) -> Path:
        return self._config.installed_dir / app_id

    def is_installed(self, app_id: str) -> bool:
        return (self.installed_path(app_id) / "app.json").is_file()

    def install(self, app_id: str) -> None:
        """Stage trusted bundled code before exposing the installation directory."""
        source = self._config.apps_dir / app_id
        target = self.installed_path(app_id)
        if target.exists(): raise AppServiceError(409, 'Installation directory already exists; refusing to overwrite it')
        stage = self._config.data_dir / 'staging' / str(uuid.uuid4()) / app_id
        try:
            copy_package(source, stage)
            load_manifest(stage)
            stage.rename(target)
        finally:
            shutil.rmtree(stage.parent, ignore_errors=True)

    def uninstall(self, app_id: str) -> None:
        shutil.rmtree(self.installed_path(app_id), ignore_errors=True)

    def _summary(self, manifest: Manifest) -> dict[str, Any]:
        installed = self.is_installed(manifest.id)
        if manifest.active_runtime(self.platform) == "web":
            # Web apps spawn no process: running mirrors installed.
            running = installed
            url = f"/apps/{manifest.id}/" if installed else None
        else:
            running = self._state.is_running(manifest.id)
            entry = self._state.get(manifest.id) if running else None
            port = entry.get("port") if entry else None
            url = f"/apps/{manifest.id}/" if port else None
            if running and manifest.web:
                url = f"/apps/{manifest.id}/"
        if manifest.view["surface"] in ("external", "none"):
            if manifest.active_runtime(self.platform) != "process":
                running = installed
            url = manifest.view.get("url") if running else None
        upgrade = False
        if installed and manifest.schema_version == 1:
            try:
                upgrade = load_manifest(self._config.apps_dir / manifest.id).schema_version == 2
            except ManifestError:
                pass
        return {
            "id": manifest.id,
            "schemaVersion": manifest.schema_version,
            "upgradeAvailable": upgrade,
            "releaseAvailable": ({key: entry[key] for key in ('publisher', 'sha256')} | {'version': entry['manifest']['version']}) if self.catalog and (entry := self.catalog.entries.get(manifest.id)) else None,
            "legacyStorage": manifest.raw.get("data", {}).get("legacy") or manifest.raw.get('data', {}).get('legacyBundle'),
            "actionRequests": manifest.raw.get('actionRequests', []),
            "connection": manifest.raw.get("connection"),
            "view": manifest.view,
            "capabilities": manifest.capabilities,
            "unavailableCapabilities": manifest.unavailable_capabilities,
            "isolation": "sandbox" if manifest.schema_version == 2 else "trusted-legacy",
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "category": manifest.category,
            "author": manifest.author,
            "color": manifest.color or DEFAULT_APP_COLOR,
            "installed": installed,
            "running": running,
            "url": url,
            "supported": manifest.supports(self.platform),
            "runtimes": manifest.runtimes(self.platform),
            "runtime": manifest.active_runtime(self.platform),
        }

    def list_apps(self) -> list[dict[str, Any]]:
        self._state.cleanup()
        return [self._summary(manifest) for manifest in self.manifests().values()]

    def describe(self, app_id: str) -> dict[str, Any] | None:
        manifest = self.get(app_id)
        if manifest is None:
            return None
        summary = self._summary(manifest)
        summary["manifest"] = manifest.raw
        return summary
