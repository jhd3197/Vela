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

Applying one is the second half of this file. Everything up to the moment the
files are replaced happens in this process and is undoable — a bad checksum, a
failed backup or an unwritable install directory all stop before anything is
touched. The replacing itself is a detached script, because a process cannot
overwrite the files it is running from.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

import httpx

from .config import Config
from .logging_setup import audit
from .version import is_newer, parse_tag
from .errors_http import Conflict

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


def platform_architecture() -> str:
    """The architecture name Vela's release assets are built and matched with.

    One definition, shared with `scripts/build-server.py`: the build names the
    download with it and the updater finds the download by it, so the two must
    never drift apart.
    """
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)


def platform_asset(assets: list[dict[str, Any]], *, kind: str | None = None) -> dict | None:
    """The release asset this computer should download, or None.

    Matching is on the name the build and the installer actually produce:
    `vela-server-<version>-<system>-<arch>[-setup].exe|.zip|.tar.gz`.
    """
    kind = kind or capability()
    if kind in ("source", "container"):
        return None
    architecture = platform_architecture()
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


# ---------------------------------------------------------------------------
# Applying an update
# ---------------------------------------------------------------------------

#: The states an update passes through, in order. `done` and `error` end it.
STATES = (
    "idle",
    "downloading",
    "verifying",
    "backing-up",
    "applying",
    "restarting",
    "done",
    "error",
)
JOURNAL = "update.log"
#: A downloaded asset is kept this long, so a rollback has something to go back
#: to without asking GitHub for it again.
KEEP_DOWNLOAD_DAYS = 7
CHUNK = 256 * 1024


class UpdateError(Conflict):
    """An update could not go ahead, with the reason to show the user."""

    code = "updates.refused"


def read_journal(config: Config) -> dict[str, Any]:
    """What the last update attempt was doing when this process started.

    The new Vela reads this on startup: it is the only way "Updated to 0.2.0"
    or "the update did not finish" can be known, because the process that was
    applying the update is gone by then.
    """
    try:
        return json.loads((config.data_dir / "updates" / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_journal(config: Config, entry: dict[str, Any]) -> None:
    path = config.data_dir / "updates" / JOURNAL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except OSError:
        LOG.warning("could not write the update journal", exc_info=True)


def startup_report(config: Config, current: str) -> dict[str, Any] | None:
    """What to tell the user about an update that ran before this start.

    None when nothing was in flight. Otherwise `outcome` is `updated` (the
    version is now the one that was being installed), `failed` (the apply ran
    and the version did not change) or `error` (it gave up before restarting).
    Reported once: the entry is marked so a later restart does not repeat it.
    """
    journal = read_journal(config)
    if not journal or journal.get("reported"):
        return None
    state = journal.get("state")
    target = journal.get("version")
    if state == "error":
        report = {"outcome": "error", "message": journal.get("error") or "", "to": target}
    elif state in ("applying", "restarting") and target:
        outcome = "updated" if current == target else "failed"
        report = {"outcome": outcome, "from": journal.get("from"), "to": target}
    else:
        return None
    write_journal(config, {**journal, "reported": True})
    return report


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sidecar(text: str, asset_name: str) -> str:
    """The digest from a `.sha256` sidecar, checked against the name it covers.

    A sidecar is `<digest>  <filename>`. The filename is checked too: a sidecar
    for a different file is not a checksum for this one, however valid its
    digest looks on its own.
    """
    stripped = (text or "").strip()
    line = stripped.splitlines()[0] if stripped else ""
    parts = line.split()
    if len(parts) < 2 or len(parts[0]) != 64:
        raise UpdateError("that checksum file is not readable")
    if parts[-1] != asset_name:
        raise UpdateError(f"the checksum file is for {parts[-1]}, not {asset_name}")
    return parts[0].lower()


def auto_mode_allowed(
    *, apps_running: int, automation_running: bool, doctor_failing: bool
) -> tuple[bool, str]:
    """Whether an unattended update may start now, and why not if not.

    Automatic means nobody is watching, so the bar is higher than for a person
    pressing a button: nothing of theirs may be interrupted, and Vela must not
    already be known-broken before it replaces itself.
    """
    if apps_running:
        return False, f"{apps_running} app{'' if apps_running == 1 else 's'} still running"
    if automation_running:
        return False, "an automation is running"
    if doctor_failing:
        return False, "a health check is failing"
    return True, ""


def relaunch_command() -> str:
    """How to start Vela again after the swap, keeping its arguments."""
    from .desktop import startup_command

    return startup_command()


def windows_installer_script(*, pid, setup, updates_dir, relaunch):
    """The batch file that installs a Windows setup exe once Vela has exited.

    It must outlive this process, so it waits on the pid rather than being a
    child that dies with it, and it relaunches Vela itself: the installer's own
    post-install Run entry is `skipifsilent`, and this install is silent.

    Paths are written with `PureWindowsPath`: a `Path` formats with the
    separator of the machine it was built on, and this script only ever runs
    on a Windows one.
    """
    setup, updates_dir = PureWindowsPath(str(setup)), PureWindowsPath(str(updates_dir))
    wait = 'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul'.format(pid=pid)
    lines = [
        "@echo off",
        "rem Written by Vela to install an update. Safe to delete.",
        ":wait",
        wait,
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        '"{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /LOG="{log}"'.format(
            setup=setup, log=updates_dir / "setup.log"
        ),
        "if errorlevel 1 (",
        '  echo installer exited with %errorlevel%> "{marker}"'.format(
            marker=updates_dir / "apply-error.txt"
        ),
        ")",
        'start "" {relaunch}'.format(relaunch=relaunch),
        "",
    ]
    return "\r\n".join(lines)


def windows_swap_script(*, pid, install_dir, staged, previous, relaunch):
    """The batch file that swaps a portable Windows folder once Vela has exited.

    The old folder is moved aside rather than deleted, so a swap that fails
    half-way can be put back — and so a rollback has something to go back to.

    Paths are written with `PureWindowsPath`: a `Path` formats with the
    separator of the machine it was built on, and this script only ever runs
    on a Windows one.
    """
    install_dir, staged, previous = (
        PureWindowsPath(str(install_dir)),
        PureWindowsPath(str(staged)),
        PureWindowsPath(str(previous)),
    )
    wait = 'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul'.format(pid=pid)
    lines = [
        "@echo off",
        "rem Written by Vela to swap in an update. Safe to delete.",
        ":wait",
        wait,
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        'if exist "{previous}" rmdir /s /q "{previous}"'.format(previous=previous),
        'move "{install_dir}" "{previous}"'.format(install_dir=install_dir, previous=previous),
        "if errorlevel 1 exit /b 1",
        'move "{staged}" "{install_dir}"'.format(staged=staged, install_dir=install_dir),
        "if errorlevel 1 (",
        '  move "{previous}" "{install_dir}"'.format(previous=previous, install_dir=install_dir),
        "  exit /b 1",
        ")",
        'start "" {relaunch}'.format(relaunch=relaunch),
        "",
    ]
    return "\r\n".join(lines)


def posix_swap_script(*, pid, install_dir, staged, previous, relaunch):
    """The shell script that swaps a macOS or Linux folder once Vela has exited.

    Paths are written with `as_posix()`: a `Path` formats with the separator of
    the machine it was built on, and this script only ever runs on a POSIX one.
    """
    install_dir, staged, previous = (
        Path(install_dir).as_posix(),
        Path(staged).as_posix(),
        Path(previous).as_posix(),
    )
    lines = [
        "#!/bin/sh",
        "# Written by Vela to swap in an update. Safe to delete.",
        "while kill -0 {pid} 2>/dev/null; do sleep 1; done".format(pid=pid),
        'rm -rf "{previous}"'.format(previous=previous),
        'mv "{install_dir}" "{previous}"'.format(install_dir=install_dir, previous=previous),
        'if ! mv "{staged}" "{install_dir}"; then mv "{previous}" "{install_dir}"; exit 1; fi'.format(
            staged=staged, install_dir=install_dir, previous=previous
        ),
        "{relaunch} &".format(relaunch=relaunch),
        "",
    ]
    return "\n".join(lines)


def rollback_available(config: Config, kind: str) -> bool:
    """Whether there is something to roll back to for this kind of install."""
    if kind in ("portable", "tarball"):
        return (install_root().parent / (install_root().name + ".previous")).is_dir()
    if kind == "installer":
        journal = read_journal(config)
        previous = journal.get("previousAsset")
        return bool(previous) and (config.data_dir / "updates" / previous).is_file()
    return False


class UpdateJob:
    """One update, from download to the moment Vela hands over and exits.

    Everything up to `applying` happens here, in this process, and is undoable:
    a bad checksum, a failed backup or an unwritable staging directory all stop
    before anything on disk is replaced. `applying` writes a script and starts
    it detached, because a process cannot replace the files it is running from.
    """

    def __init__(self, config: Config, checker: "UpdateChecker", *, backups=None):
        self._config = config
        self._checker = checker
        self._backups = backups
        self._dir = config.data_dir / "updates"
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle", "percent": 0, "message": "", "version": None}

    # ----------------------------------------------------------------- state

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, state: str, *, percent: int | None = None, message: str = "", **extra) -> None:
        with self._lock:
            self._state = {
                **self._state,
                "state": state,
                "message": message,
                **({"percent": percent} if percent is not None else {}),
                **extra,
            }
            snapshot = dict(self._state)
        # The journal is what the *next* process reads, so it is written on
        # every step rather than only at the end. It is merged, not replaced:
        # the asset names a rollback needs must survive a later state change.
        write_journal(self._config, {**read_journal(self._config), **snapshot, "reported": False})

    def busy(self) -> bool:
        return self.state()["state"] in ("downloading", "verifying", "backing-up", "applying", "restarting")

    # -------------------------------------------------------------- download

    def _download(self, url: str, target: Path, *, transport=None) -> None:
        client_args: dict[str, Any] = {"timeout": 60.0, "follow_redirects": True}
        if transport is not None:
            client_args["transport"] = transport
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        with httpx.Client(**client_args) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                written = 0
                with partial.open("wb") as handle:
                    for block in response.iter_bytes(CHUNK):
                        handle.write(block)
                        written += len(block)
                        if total:
                            self._set("downloading", percent=min(99, int(written * 100 / total)))
        partial.replace(target)

    def prepare(self, *, transport=None) -> dict[str, Any]:
        """Download, verify and back up. Returns where the asset landed.

        Everything here is reversible. Nothing on this computer has been
        replaced when it finishes.
        """
        status = self._checker.status()
        if not status["available"]:
            raise UpdateError("There is no newer version to install.")
        kind = status["capability"]
        if kind in ("source", "container"):
            raise UpdateError(
                "This copy of Vela updates a different way — see Settings › Updates."
            )
        asset = status.get("asset")
        checksum = status.get("checksum")
        if not asset or not asset.get("url"):
            raise UpdateError("That release has no download for this computer.")
        if not checksum or not checksum.get("url"):
            raise UpdateError("That release has no checksum, so Vela will not install it.")

        self._set("downloading", percent=0, message=f"Downloading Vela {status['latest']}…",
                  version=status["latest"], **{"from": status["current"]})
        target = self._dir / asset["name"]
        try:
            self._download(asset["url"], target, transport=transport)
            sidecar = self._dir / checksum["name"]
            self._download(checksum["url"], sidecar, transport=transport)
        except Exception as exc:  # noqa: BLE001 - any failure is the same answer
            raise UpdateError(f"The download did not finish: {exc}") from exc

        self._set("verifying", percent=100, message="Checking the download…")
        expected = parse_sidecar(sidecar.read_text(encoding="utf-8", errors="replace"), asset["name"])
        actual = sha256_file(target)
        if actual != expected:
            # Refuse before anything is backed up or replaced.
            target.unlink(missing_ok=True)
            raise UpdateError(
                "The download did not match its checksum, so Vela did not install it."
            )

        self._prune_downloads()

        self._set("backing-up", message="Backing up before the update…")
        if self._backups is not None:
            try:
                self._backups.create()
            except Exception as exc:  # noqa: BLE001
                raise UpdateError(f"Vela did not back up first, so it did not update: {exc}") from exc

        return {"asset": target, "checksum": expected, "capability": kind, "version": status["latest"]}

    def _prune_downloads(self) -> None:
        """Drop downloads older than a week, keeping the rollback window."""
        cutoff = datetime.now().timestamp() - KEEP_DOWNLOAD_DAYS * 86400
        keep = {read_journal(self._config).get("previousAsset"), read_journal(self._config).get("asset")}
        for child in self._dir.glob("vela-server-*"):
            try:
                if child.name not in keep and child.is_file() and child.stat().st_mtime < cutoff:
                    child.unlink()
            except OSError:
                continue

    # ----------------------------------------------------------------- stage

    def _stage(self, archive: Path) -> Path:
        """Unpack the new Vela beside the current one, ready to swap in."""
        root = install_root()
        staged = root.parent / (root.name + ".new")
        if not os.access(root.parent, os.W_OK):
            raise UpdateError(
                f"Vela cannot write to {root.parent}, so it cannot update itself here. "
                "Download the new version and replace this folder by hand."
            )
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        try:
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(staged)
            else:
                with tarfile.open(archive) as bundle:
                    bundle.extractall(staged)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            shutil.rmtree(staged, ignore_errors=True)
            raise UpdateError(f"The download could not be unpacked: {exc}") from exc

        # The archives contain one top-level folder; the swap wants its
        # contents to *be* the install directory.
        entries = [child for child in staged.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for child in list(inner.iterdir()):
                shutil.move(str(child), str(staged / child.name))
            inner.rmdir()

        executable = staged / ("Vela.exe" if sys.platform == "win32" else "Vela")
        if not executable.exists():
            shutil.rmtree(staged, ignore_errors=True)
            raise UpdateError("The download does not look like a Vela server; nothing was changed.")
        return staged

    # ----------------------------------------------------------------- apply

    def script_for(self, kind: str, prepared: dict[str, Any], *, pid: int | None = None) -> tuple[Path, str]:
        """The script that will do the replacing, and its text.

        Generated here and tested here; running it is the only step this
        process cannot supervise, because it replaces the files this process is
        running from.
        """
        pid = pid if pid is not None else os.getpid()
        root = install_root()
        self._dir.mkdir(parents=True, exist_ok=True)
        relaunch = relaunch_command()
        if kind == "installer":
            path = self._dir / "apply.cmd"
            text = windows_installer_script(
                pid=pid, setup=prepared["asset"], updates_dir=self._dir, relaunch=relaunch
            )
        elif kind == "portable":
            path = self._dir / "apply.cmd"
            text = windows_swap_script(
                pid=pid,
                install_dir=root,
                staged=prepared["staged"],
                previous=root.parent / (root.name + ".previous"),
                relaunch=relaunch,
            )
        elif kind == "tarball":
            path = self._dir / "apply.sh"
            text = posix_swap_script(
                pid=pid,
                install_dir=root,
                staged=prepared["staged"],
                previous=root.parent / (root.name + ".previous"),
                relaunch=relaunch,
            )
        else:
            raise UpdateError("This copy of Vela cannot replace itself.")
        return path, text

    def apply(self, *, transport=None, stop=None, spawn=None) -> dict[str, Any]:
        """Install the newer Vela. Returns once the handover has been started.

        `stop` is how the server is asked to exit; `spawn` starts the detached
        script. Both are injectable so the whole path can be exercised without
        this machine's Vela being replaced.
        """
        if self.busy():
            raise UpdateError("An update is already running.")
        try:
            prepared = self.prepare(transport=transport)
            kind = prepared["capability"]
            if kind in ("portable", "tarball"):
                prepared["staged"] = self._stage(prepared["asset"])
            self._set("applying", message="Installing…")
            path, text = self.script_for(kind, prepared)
            path.write_text(text, encoding="utf-8")
            if sys.platform != "win32":
                path.chmod(0o755)
            journal = read_journal(self._config)
            write_journal(
                self._config,
                {
                    **journal,
                    "state": "applying",
                    "version": prepared["version"],
                    "script": str(path),
                    "previousAsset": journal.get("asset"),
                    "asset": prepared["asset"].name,
                    "reported": False,
                },
            )
            (spawn or _spawn_detached)(path)
            self._set("restarting", message="Vela is restarting…")
            audit("update", f"to={prepared['version']} via={kind}")
            if stop is not None:
                stop()
            return self.state()
        except UpdateError as exc:
            self._set("error", message=str(exc), error=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - never leave the job wedged
            LOG.exception("update failed")
            self._set("error", message=str(exc), error=str(exc))
            raise UpdateError(str(exc)) from exc

    def rollback(self, *, spawn=None, stop=None) -> dict[str, Any]:
        """Put the previous version back, where there is one to put back."""
        kind = capability()
        if not rollback_available(self._config, kind):
            raise UpdateError("There is no previous version to go back to.")
        root = install_root()
        previous = root.parent / (root.name + ".previous")
        if kind in ("portable", "tarball"):
            # The swap script works in either direction: the "new" folder to
            # move in is the one kept from last time.
            staged = root.parent / (root.name + ".rollback")
            shutil.rmtree(staged, ignore_errors=True)
            shutil.move(str(previous), str(staged))
            prepared = {"staged": staged}
            path, text = self.script_for(kind, prepared)
        else:
            journal = read_journal(self._config)
            asset = self._dir / journal["previousAsset"]
            path, text = self.script_for("installer", {"asset": asset})
        path.write_text(text, encoding="utf-8")
        if sys.platform != "win32":
            path.chmod(0o755)
        self._set("restarting", message="Going back to the previous version…")
        audit("update", "rollback")
        (spawn or _spawn_detached)(path)
        if stop is not None:
            stop()
        return self.state()


def _spawn_detached(script: Path) -> None:
    """Start the apply script so it outlives this process."""
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            creationflags=flags,
            close_fds=True,
            cwd=str(script.parent),
        )
    else:
        subprocess.Popen(
            ["/bin/sh", str(script)],
            start_new_session=True,
            close_fds=True,
            cwd=str(script.parent),
        )
