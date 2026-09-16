"""Does a newer Vela exist, and could this copy install it?

One anonymous request to the GitHub releases API, at most once every six
hours, and nothing else leaves the computer: no identifier, no version report,
no telemetry. The check is on by default and Settings says so in as many words,
with a switch that turns it off — after which Vela makes no request at all.

Capability answers what this particular copy could do about an update, which
depends on how it was installed:

- `installer`  a Windows Inno Setup install, updated by running the new setup
- `portable`   a Windows folder the user unzipped, updated by swapping it
- `tarball`    a macOS or Linux folder from a .tar.gz, swapped the same way
- `source`     a git checkout; `git pull` is the update
- `container`  inside a container image; the image tag is the update

Stage 6 acts on these. Here they only decide what Settings offers.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import Config
from .version import is_newer, parse_tag

LOG = logging.getLogger(__name__)

#: The one address Vela contacts, overridable so tests never touch GitHub.
RELEASES_API = "https://api.github.com/repos/jhd3197/vela/releases/latest"
ENV_API = "VELA_UPDATE_API"
#: A release is not news six times a day. Six hours keeps the request rare and
#: still notices a release the same day it lands.
CACHE_SECONDS = 6 * 60 * 60
TIMEOUT_SECONDS = 10.0

DEFAULT_UPDATES = {"check": True, "mode": "notify", "hour": 3}
MODES = ("notify", "auto")

INSTALLER_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vela.Server_is1"


def install_root() -> Path:
    """The directory this Vela runs from."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _from_installer() -> bool:
    """Whether this Windows copy was put here by the Inno Setup installer."""
    if sys.platform != "win32":
        return False
    # The uninstaller sits beside the exe in an installed copy and is absent
    # from the portable zip, which is the reliable signal when the registry is
    # unreadable (another user's install, a locked-down profile).
    if (install_root() / "unins000.exe").is_file():
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_UNINSTALL_KEY):
            return True
    except (OSError, ImportError):
        return False


def capability() -> str:
    """What this copy of Vela could do about an update."""
    if os.environ.get("VELA_UPDATE_CAPABILITY"):
        # Set by the distribution checks so they can drive one path at a time.
        return os.environ["VELA_UPDATE_CAPABILITY"]
    if Path("/.dockerenv").exists() or os.environ.get("VELA_CONTAINER") == "1":
        return "container"
    if not getattr(sys, "frozen", False):
        # A checkout updates with git, not by swapping a folder.
        return "source"
    if sys.platform == "win32":
        return "installer" if _from_installer() else "portable"
    return "tarball"


def platform_asset(assets: list[dict[str, Any]], *, kind: str | None = None) -> dict | None:
    """The release asset this computer should download, or None.

    Matching is on the name the build and the installer actually produce:
    `vela-server-<version>-<system>-<arch>[-setup].exe|.zip|.tar.gz`.
    """
    kind = kind or capability()
    if kind in ("source", "container"):
        return None
    machine = platform.machine().lower()
    architecture = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    if kind == "installer":
        suffix = f"-windows-{architecture}-setup.exe"
    elif kind == "portable":
        suffix = f"-windows-{architecture}.zip"
    else:
        system = "macos" if sys.platform == "darwin" else "linux"
        suffix = f"-{system}-{architecture}.tar.gz"
    for asset in assets:
        name = asset.get("name") or ""
        if name.endswith(suffix):
            return {
                "name": name,
                "url": asset.get("browser_download_url"),
                "size": asset.get("size"),
            }
    return None


def checksum_asset(assets: list[dict[str, Any]], asset_name: str) -> dict | None:
    """The `.sha256` sidecar published beside an asset."""
    for asset in assets:
        if (asset.get("name") or "") == f"{asset_name}.sha256":
            return {"name": asset["name"], "url": asset.get("browser_download_url")}
    return None


class UpdateChecker:
    """Asks GitHub, at most every six hours, whether there is a newer Vela."""

    def __init__(self, config: Config, current: str, settings=None):
        self._config = config
        self._current = current
        self._settings = settings
        self._dir = config.data_dir / "updates"
        self._cache = self._dir / "latest.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------- settings

    def preferences(self) -> dict[str, Any]:
        stored = (self._settings.get("updates") if self._settings else None) or {}
        merged = {**DEFAULT_UPDATES, **{k: v for k, v in stored.items() if k in DEFAULT_UPDATES}}
        if merged["mode"] not in MODES:
            merged["mode"] = DEFAULT_UPDATES["mode"]
        try:
            merged["hour"] = max(0, min(23, int(merged["hour"])))
        except (TypeError, ValueError):
            merged["hour"] = DEFAULT_UPDATES["hour"]
        merged["check"] = bool(merged["check"])
        return merged

    # ---------------------------------------------------------------- cache

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            LOG.warning("could not cache the update check", exc_info=True)

    def _fresh(self, cached: dict[str, Any] | None) -> bool:
        if not cached or not cached.get("checkedAt"):
            return False
        try:
            checked = datetime.fromisoformat(cached["checkedAt"])
        except ValueError:
            return False
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - checked).total_seconds() < CACHE_SECONDS

    # ---------------------------------------------------------------- check

    def status(self) -> dict[str, Any]:
        """What Vela currently believes, without making a request."""
        cached = self._read_cache() or {}
        preferences = self.preferences()
        kind = capability()
        latest = cached.get("latest")
        return {
            "current": self._current,
            "latest": latest,
            "available": bool(latest) and is_newer(latest, self._current),
            "notes": cached.get("notes") or "",
            "asset": cached.get("asset"),
            "checksum": cached.get("checksum"),
            "capability": kind,
            "checkedAt": cached.get("checkedAt"),
            "error": cached.get("error"),
            **preferences,
        }

    def check(self, *, force: bool = False, transport=None) -> dict[str, Any]:
        """Ask GitHub, unless the answer is still fresh or checking is off.

        With `check` off this makes no request at all — that is the whole
        promise of the switch, and the tests assert it by failing the call if
        any request is attempted.
        """
        preferences = self.preferences()
        if not preferences["check"]:
            return {**self.status(), "checkedAt": None, "skipped": "off"}
        with self._lock:
            cached = self._read_cache()
            if not force and self._fresh(cached):
                return {**self.status(), "skipped": "cached"}
            url = os.environ.get(ENV_API) or RELEASES_API
            payload: dict[str, Any] = {
                "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")
            }
            try:
                client_args = {"timeout": TIMEOUT_SECONDS}
                if transport is not None:
                    client_args["transport"] = transport
                with httpx.Client(**client_args) as client:
                    response = client.get(
                        url,
                        headers={
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "vela-server",
                        },
                    )
                    response.raise_for_status()
                    release = response.json()
            except Exception as exc:  # noqa: BLE001 - any failure is the same answer
                LOG.info("update check failed: %s", exc)
                # Keep the last good answer rather than blanking it: being
                # offline is not evidence that there is no update.
                if cached:
                    kept = {**cached, "error": str(exc)}
                    self._write_cache(kept)
                    return {**self.status(), "error": str(exc)}
                payload["error"] = str(exc)
                self._write_cache(payload)
                return {**self.status(), "error": str(exc)}

            latest = parse_tag(release.get("tag_name") or "")
            assets = release.get("assets") or []
            asset = platform_asset(assets) if latest else None
            payload.update(
                {
                    "latest": latest,
                    "notes": (release.get("body") or "")[:20000],
                    "asset": asset,
                    "checksum": checksum_asset(assets, asset["name"]) if asset else None,
                    "error": None,
                }
            )
            self._write_cache(payload)
            return self.status()
