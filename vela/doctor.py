"""Health checks with plain-language findings and, where possible, a repair.

The registry — `register`, `collect`, `repair`, the per-check timeout and the
normalising `_clean` — follows ServerKit's `doctor_check_registry.py` (MIT,
same owner). ServerKit registers checks from plugins into module-level globals
and runs them inside a Flask app context; Vela's checks are its own and are
registered on an instance, so a test builds a doctor over a disposable data
directory instead of mutating shared state.

Every check answers one question a person could otherwise only answer with a
terminal, and says what it means rather than what it measured.
"""

import json
import logging
import os
import shutil
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .config import Config
from .logging_setup import audit

LOG = logging.getLogger(__name__)

# A run must stay inside a person's patience, so the whole sweep is bounded as
# well as each check.
DEFAULT_TIMEOUT = 5.0
TOTAL_BUDGET = 15.0

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIPPED = "skipped"
STATUSES = (OK, WARN, FAIL, SKIPPED)

# Free space on the data directory's volume.
DISK_WARN = 2 * 1024**3
DISK_FAIL = 500 * 1024**2
CERT_WARN_DAYS = 14
# A backup older than this with a schedule enabled is worth mentioning.
BACKUP_STALE_DAYS = 2


class Doctor:
    """Vela's checks, their last result, and the repairs that fix them."""

    def __init__(
        self,
        config: Config,
        *,
        state=None,
        registry=None,
        settings=None,
        backups=None,
        assistant=None,
        catalog=None,
    ):
        self._config = config
        self._state = state
        self._registry = registry
        self._settings = settings
        self._backups = backups
        self._assistant = assistant
        self._catalog = catalog
        self._checks: dict[str, dict[str, Any]] = {}
        self._last: dict[str, Any] | None = None
        self.register_defaults()

    # ---------------------------------------------------------------- registry

    def register(
        self,
        key: str,
        title: str,
        run: Callable[[], Any],
        repair: Callable[[], Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not key or not callable(run):
            raise ValueError("a check needs a key and a callable")
        if repair is not None and not callable(repair):
            raise ValueError("a repair must be callable")
        self._checks[key] = {
            "title": title,
            "run": run,
            "repair": repair,
            "timeout": float(timeout),
        }

    def keys(self) -> list[str]:
        return list(self._checks)

    # ------------------------------------------------------------------ result

    def last(self) -> dict[str, Any] | None:
        """The last sweep, or None when nothing has run in this process yet."""
        return self._last

    def collect(self) -> dict[str, Any]:
        """Run every check and remember the result."""
        started = datetime.now()
        checks: list[dict[str, Any]] = []
        # Checks are independent and mostly wait on the filesystem or a socket,
        # so they run together: a sweep costs about as long as its slowest
        # check rather than the sum of all of them.
        pool = ThreadPoolExecutor(max_workers=min(8, len(self._checks) or 1))
        try:
            futures = {
                key: pool.submit(self._run_one, key, entry)
                for key, entry in self._checks.items()
            }
            # One wall-clock budget for the whole sweep, not one per check: with
            # a per-check deadline thirteen slow checks would still take
            # thirteen times as long as the budget allows.
            deadline = monotonic() + TOTAL_BUDGET
            for key, future in futures.items():
                entry = self._checks[key]
                try:
                    checks.append(future.result(timeout=max(0.0, deadline - monotonic())))
                except FutureTimeout:
                    checks.append(
                        self._clean(
                            key,
                            entry,
                            {
                                "status": WARN,
                                "detail": "This check did not finish in time, so Vela cannot say.",
                            },
                        )
                    )
        finally:
            # Deliberately not waiting: a check that overran is still running
            # and cannot be killed from here, but the sweep must not block on
            # it — which is exactly what the executor's context manager would
            # do on exit, making the timeouts above worthless.
            pool.shutdown(wait=False)
        ran_at = started.isoformat(timespec="seconds")
        for check in checks:
            check["ranAt"] = ran_at
        checks.sort(key=lambda check: (_ORDER.index(check["status"]), check["key"]))
        self._last = {
            "checks": checks,
            "ranAt": ran_at,
            "summary": summarise(checks),
        }
        return self._last

    def repair(self, key: str, *, actor: str = "local") -> dict[str, Any]:
        entry = self._checks.get(key)
        if entry is None:
            return {"ok": False, "detail": f"There is no check called {key}."}
        if not entry.get("repair"):
            return {"ok": False, "detail": "Vela cannot repair this one for you."}
        try:
            result = entry["repair"]() or {}
        except Exception as exc:  # noqa: BLE001 - a repair must not take the hub down
            LOG.exception("doctor repair for %s failed", key)
            return {"ok": False, "detail": f"The repair failed: {exc}"}
        if not isinstance(result, dict):
            result = {"ok": bool(result)}
        result.setdefault("ok", True)
        audit("repair", f"check={key} ok={result['ok']}", actor=actor)
        # Re-run the one check so the caller sees the state it is now in
        # rather than the state that prompted the repair.
        result["check"] = self._run_one(key, entry)
        result["check"]["ranAt"] = datetime.now().isoformat(timespec="seconds")
        if self._last:
            self._last["checks"] = [
                result["check"] if item["key"] == key else item
                for item in self._last["checks"]
            ]
            self._last["summary"] = summarise(self._last["checks"])
        return result

    # ----------------------------------------------------------------- running

    def _run_one(self, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            raw = pool.submit(entry["run"]).result(timeout=entry["timeout"])
        except FutureTimeout:
            raw = {
                "status": WARN,
                "detail": "This check did not finish in time, so Vela cannot say.",
            }
        except Exception as exc:  # noqa: BLE001 - one bad check must not end the sweep
            LOG.exception("doctor check %s raised", key)
            raw = {"status": WARN, "detail": f"This check could not run: {exc}"}
        finally:
            pool.shutdown(wait=False)
        return self._clean(key, entry, raw)

    def _clean(self, key: str, entry: dict[str, Any], raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {"status": WARN, "detail": "This check answered with nothing usable."}
        status = raw.get("status")
        if status not in STATUSES:
            # A status the dashboard cannot render would show as a blank dot.
            # A bug should be visible, not invisible.
            status = WARN
        return {
            "key": key,
            "title": entry["title"],
            "status": status,
            "detail": str(raw.get("detail") or ""),
            # A repair is only offered where there is something to repair.
            "repairable": bool(entry.get("repair")) and status in (WARN, FAIL),
        }

    # ------------------------------------------------------------ the checks

    def register_defaults(self) -> None:
        self.register("data-dir", "Room to work", self._check_data_dir)
        self.register("port", "Reachable", self._check_port)
        self.register("certificate", "Certificate", self._check_certificate)
        self.register(
            "stale-apps", "App state", self._check_stale_apps, repair=self._repair_stale_apps
        )
        self.register("orphans", "Installed folders", self._check_orphans, repair=self._repair_orphans)
        self.register("worker", "Automation runtime", self._check_worker)
        self.register("node", "Node runtime", self._check_node)
        self.register("psutil", "System readings", self._check_psutil)
        self.register("catalog", "App catalog", self._check_catalog)
        self.register("ollama", "Local AI", self._check_ollama)
        self.register("backups", "Backups", self._check_backups)
        self.register("settings", "Saved settings", self._check_settings, repair=self._repair_settings)
        self.register("update", "Vela version", self._check_update)

    def _check_data_dir(self) -> dict[str, Any]:
        data_dir = self._config.data_dir
        if not data_dir.is_dir():
            return {"status": FAIL, "detail": f"{data_dir} is missing."}
        probe = data_dir / ".vela-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return {
                "status": FAIL,
                "detail": f"Vela cannot write to {data_dir}: {exc}. Apps cannot save anything.",
            }
        try:
            free = shutil.disk_usage(data_dir).free
        except OSError:
            return {"status": OK, "detail": f"{data_dir} is writable."}
        human = _human_size(free)
        if free < DISK_FAIL:
            return {
                "status": FAIL,
                "detail": f"Only {human} free where Vela keeps your data. Free some space now.",
            }
        if free < DISK_WARN:
            return {
                "status": WARN,
                "detail": f"{human} free where Vela keeps your data. Consider freeing some space.",
            }
        return {"status": OK, "detail": f"{human} free where Vela keeps your data."}

    def _check_port(self) -> dict[str, Any]:
        host = os.environ.get("VELA_HOST", "127.0.0.1")
        port = int(os.environ.get("VELA_PORT", "7700"))
        # This check runs inside the server, so the useful question is not "is
        # something listening" but "can something reach it here".
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
        except OSError as exc:
            return {
                "status": FAIL,
                "detail": f"Vela is not answering on {host}:{port} ({exc}).",
            }
        origin = self._config.public_origin
        if not origin:
            return {"status": OK, "detail": f"Answering on {host}:{port}."}
        try:
            import httpx

            response = httpx.get(f"{origin}/api/health", timeout=3.0, verify=False)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - any failure is the same answer here
            return {
                "status": WARN,
                "detail": (
                    f"Answering on {host}:{port}, but {origin} did not answer from this "
                    f"computer ({exc}). Devices on your Wi-Fi may not reach Vela."
                ),
            }
        return {"status": OK, "detail": f"Answering on {host}:{port} and at {origin}."}

    def _check_certificate(self) -> dict[str, Any]:
        cert = os.environ.get("VELA_CERT_FILE")
        if not cert:
            return {"status": SKIPPED, "detail": "Vela is not using HTTPS on this computer."}
        path = Path(cert)
        if not path.is_file():
            return {"status": FAIL, "detail": f"The certificate file {path} is missing."}
        try:
            expires = _certificate_expiry(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable cert is one answer
            return {"status": FAIL, "detail": f"Vela could not read that certificate: {exc}"}
        days = (expires - datetime.now(timezone.utc)).days
        when = expires.date().isoformat()
        if days < 0:
            return {"status": FAIL, "detail": f"The certificate expired on {when}."}
        if days <= CERT_WARN_DAYS:
            return {
                "status": WARN,
                "detail": f"The certificate expires on {when}, in {days} day{'' if days == 1 else 's'}.",
            }
        return {"status": OK, "detail": f"The certificate is valid until {when}."}

    def _stale_apps(self) -> list[str]:
        if self._state is None:
            return []
        from .state import pid_alive

        stale = []
        for app_id, entry in (self._state.all() or {}).items():
            if not isinstance(entry, dict):
                continue
            pid = entry.get("pid")
            if pid and not pid_alive(pid, entry.get("pid_ctime")):
                stale.append(app_id)
        return sorted(stale)

    def _check_stale_apps(self) -> dict[str, Any]:
        if self._state is None:
            return {"status": SKIPPED, "detail": "No run state on this server."}
        stale = self._stale_apps()
        if not stale:
            return {"status": OK, "detail": "Every app Vela lists as running really is."}
        names = ", ".join(stale)
        return {
            "status": WARN,
            "detail": (
                f"Vela still lists {names} as running, but the process is gone. "
                "Repairing clears the record; it does not stop or start anything."
            ),
        }

    def _repair_stale_apps(self) -> dict[str, Any]:
        stale = self._stale_apps()
        for app_id in stale:
            self._state.clear(app_id)
        if not stale:
            return {"ok": True, "detail": "There was nothing to clear."}
        return {"ok": True, "detail": f"Cleared the stale record for {', '.join(stale)}."}

    def _orphans(self) -> list[Path]:
        installed = self._config.installed_dir
        if not installed.is_dir():
            return []
        return sorted(
            child
            for child in installed.iterdir()
            if child.is_dir() and not (child / "app.json").is_file()
        )

    def _check_orphans(self) -> dict[str, Any]:
        orphans = self._orphans()
        if not orphans:
            return {"status": OK, "detail": "Every installed folder has its app description."}
        names = ", ".join(path.name for path in orphans)
        return {
            "status": WARN,
            "detail": (
                f"{names} {'has' if len(orphans) == 1 else 'have'} no app description, so "
                "Vela cannot run anything there. Repairing backs Vela up first, then removes "
                f"{'it' if len(orphans) == 1 else 'them'}."
            ),
        }

    def _repair_orphans(self) -> dict[str, Any]:
        orphans = self._orphans()
        if not orphans:
            return {"ok": True, "detail": "There was nothing to remove."}
        # Nothing is deleted before there is a copy to go back to.
        if self._backups is not None:
            try:
                self._backups.create()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "detail": f"Vela did not back up first, so nothing was removed: {exc}"}
        removed = []
        for path in orphans:
            try:
                shutil.rmtree(path)
                removed.append(path.name)
            except OSError as exc:
                return {"ok": False, "detail": f"Could not remove {path.name}: {exc}"}
        return {"ok": True, "detail": f"Backed Vela up, then removed {', '.join(removed)}."}

    def _check_worker(self) -> dict[str, Any]:
        try:
            from .automations.worker import availability
        except Exception as exc:  # noqa: BLE001
            return {"status": WARN, "detail": f"Vela could not check the automation runtime: {exc}"}
        state = availability()
        if state.get("available"):
            return {"status": OK, "detail": "Automations can run on this computer."}
        return {
            "status": WARN,
            "detail": state.get("detail") or "Automations cannot run on this computer.",
        }

    def _check_node(self) -> dict[str, Any]:
        try:
            from .automations.worker import node_executable
        except Exception as exc:  # noqa: BLE001
            return {"status": WARN, "detail": f"Vela could not look for Node: {exc}"}
        node = node_executable()
        if node:
            return {"status": OK, "detail": f"Node is available at {node}."}
        return {
            "status": WARN,
            "detail": (
                "Vela has no Node runtime, so automations and apps that need one cannot run. "
                "Reinstall the Vela download, or install Node.js 20 or later."
            ),
        }

    def _check_psutil(self) -> dict[str, Any]:
        try:
            import psutil  # noqa: F401,PLC0415
        except Exception:  # noqa: BLE001
            return {
                "status": WARN,
                "detail": "System readings are unavailable, so the desk cannot show CPU or memory.",
            }
        return {"status": OK, "detail": "CPU, memory and disk readings are available."}

    def _check_catalog(self) -> dict[str, Any]:
        if self._catalog is None or not self._config.catalog_source:
            return {"status": SKIPPED, "detail": "No app catalog is configured."}
        try:
            status = self._catalog.status()
        except Exception as exc:  # noqa: BLE001
            return {"status": WARN, "detail": f"Vela could not read the catalog: {exc}"}
        if status.get("error"):
            return {
                "status": WARN,
                "detail": f"The app catalog could not be reached: {status['error']}",
            }
        count = len(status.get("releases") or [])
        return {"status": OK, "detail": f"The app catalog is reachable, with {count} app{'' if count == 1 else 's'}."}

    def _check_ollama(self) -> dict[str, Any]:
        if self._assistant is None:
            return {"status": SKIPPED, "detail": "Local AI is not set up."}
        model = None
        if self._settings is not None:
            model = self._settings.get("chat_model")
        if not model:
            return {"status": SKIPPED, "detail": "No chat model is chosen, so Vela asks nothing of Ollama."}
        try:
            import httpx

            url = os.environ.get("VELA_OLLAMA_URL", "http://127.0.0.1:11434")
            response = httpx.get(f"{url}/api/tags", timeout=3.0)
            response.raise_for_status()
            models = [
                entry.get("name")
                for entry in response.json().get("models", [])
                if isinstance(entry, dict)
            ]
        except Exception as exc:  # noqa: BLE001
            return {
                "status": WARN,
                "detail": f"Ollama did not answer ({exc}), so Ask cannot reply.",
            }
        if model not in models:
            return {
                "status": WARN,
                "detail": f"Ollama is running but does not have {model}. Pull it, or choose another model.",
            }
        return {"status": OK, "detail": f"Ollama is running with {model}."}

    def _check_backups(self) -> dict[str, Any]:
        if self._backups is None:
            return {"status": SKIPPED, "detail": "Backups are not set up on this server."}
        try:
            entries = self._backups.list()
        except Exception as exc:  # noqa: BLE001
            return {"status": WARN, "detail": f"Vela could not read its backups: {exc}"}
        if not entries:
            return {
                "status": WARN,
                "detail": "Vela has never backed itself up. One copy is better than none.",
            }
        newest = entries[0]
        try:
            created = datetime.fromisoformat(newest["created_at"])
        except (KeyError, ValueError):
            return {"status": OK, "detail": f"The newest backup is {newest.get('name', 'unknown')}."}
        age = datetime.now() - created
        when = created.strftime("%d %b %H:%M")
        # With a schedule on, "stale" means it missed its window; without one,
        # a couple of days is the point at which it is worth mentioning.
        scheduled = False
        if self._settings is not None:
            schedule = (self._settings.get("backups") or {}).get("schedule") or {}
            scheduled = bool(schedule.get("enabled"))
        window = timedelta(days=1, hours=6) if scheduled else timedelta(days=BACKUP_STALE_DAYS)
        if age > window:
            missed = " even though a daily backup is scheduled" if scheduled else ""
            return {
                "status": WARN,
                "detail": f"The newest backup is from {when}, {age.days} days ago{missed}.",
            }
        return {"status": OK, "detail": f"Vela last backed itself up on {when}."}

    def _settings_files(self) -> list[Path]:
        return [
            self._config.settings_file,
            self._config.data_dir / "desk.json",
            self._config.state_file,
        ]

    def _broken_settings(self) -> list[Path]:
        broken = []
        for path in self._settings_files():
            if not path.is_file():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                broken.append(path)
        return broken

    def _check_settings(self) -> dict[str, Any]:
        broken = self._broken_settings()
        if not broken:
            return {"status": OK, "detail": "Your settings, desk and app state all read correctly."}
        names = ", ".join(path.name for path in broken)
        recoverable = [path for path in broken if path.with_suffix(path.suffix + ".bak").is_file()]
        detail = f"{names} could not be read, so Vela fell back to its defaults."
        if recoverable:
            detail += " Repairing puts back the last copy that worked."
        return {"status": FAIL, "detail": detail}

    def _repair_settings(self) -> dict[str, Any]:
        restored = []
        for path in self._broken_settings():
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.is_file():
                continue
            try:
                json.loads(backup.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # Keep the unreadable file rather than deleting it: it is the only
            # copy of whatever was lost, and someone may want to look at it.
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".broken"))
                shutil.copy2(backup, path)
            except OSError as exc:
                return {"ok": False, "detail": f"Could not put back {path.name}: {exc}"}
            restored.append(path.name)
        if not restored:
            return {
                "ok": False,
                "detail": "There is no earlier copy to put back. Restore a backup instead.",
            }
        return {
            "ok": True,
            "detail": f"Put back the last working {', '.join(restored)}. The unreadable file is kept as .broken.",
        }

    def _check_update(self) -> dict[str, Any]:
        # Stage 5 gives this one a real answer.
        return {"status": SKIPPED, "detail": "Vela does not check for updates yet."}


_ORDER = [FAIL, WARN, OK, SKIPPED]


def summarise(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and one line of plain English for the checks given."""
    counts = {status: sum(1 for check in checks if check["status"] == status) for status in STATUSES}
    considered = len(checks) - counts[SKIPPED]
    attention = counts[FAIL] + counts[WARN]
    if not checks:
        text = "Vela has not checked itself yet."
    elif attention == 0:
        text = f"All {considered} checks passed."
    else:
        text = f"{attention} of {considered} checks need attention."
    return {**counts, "considered": considered, "attention": attention, "text": text}


def _human_size(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _certificate_expiry(path: Path) -> datetime:
    """When a PEM certificate stops being valid, as an aware UTC datetime."""
    text = path.read_text(encoding="utf-8", errors="replace")
    # `ssl` can decode a PEM without a connection, which keeps this check off
    # the network and free of a cryptography dependency.
    decoded = ssl._ssl._test_decode_cert  # noqa: SLF001 - the only stdlib reader
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        temporary = handle.name
    try:
        info = decoded(temporary)
    finally:
        os.unlink(temporary)
    return datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
