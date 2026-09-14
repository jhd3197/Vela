"""Persistent run state (state.json) with cross-platform PID liveness checks."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


def pid_alive(pid: int, ctime: int | str | None = None) -> bool:
    """Liveness check; when ctime is given, PID reuse reads as dead."""
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        alive = _pid_alive_windows(pid)
    else:
        alive = _pid_alive_posix(pid)
    if not alive or ctime is None:
        return alive
    current = pid_ctime(pid)
    return current is not None and current == ctime


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def pid_ctime(pid: int) -> int | None:
    """Creation/start-time token identifying this process instance, or None.

    Runners store the token at launch so a later PID reuse cannot impersonate
    the original process. None means the platform cannot provide one (liveness
    then degrades to a bare PID check).
    """
    if pid is None or pid <= 0:
        return None
    if os.name == "nt":
        return _pid_ctime_windows(pid)
    return _pid_ctime_posix(pid)


def _pid_ctime_posix(pid: int) -> int | None:
    # Linux: /proc/<pid>/stat field 22 (starttime). Fields after the comm
    # column (which may contain spaces/parens) begin at field 3, so starttime
    # is index 19 of the remainder. macOS has no /proc, so this returns None
    # and liveness degrades to the bare PID check.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        rest = stat[stat.rindex(")") + 1 :].split()
        return int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_ctime_windows(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class StateStore:
    """state.json: { "<app-id>": { "pid": int, "port": int, "pid_ctime": int | null, "started_at": iso8601 } }."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, app_id: str) -> dict[str, Any] | None:
        entry = self._load().get(app_id)
        return entry if isinstance(entry, dict) else None

    def set(
        self, app_id: str, pid: int, port: int | None, pid_ctime: int | str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            entry = {
                "pid": pid,
                "port": port,
                "pid_ctime": pid_ctime,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }
            data[app_id] = entry
            self._save(data)
            return entry

    def clear(self, app_id: str) -> None:
        with self._lock:
            data = self._load()
            if app_id in data:
                del data[app_id]
                self._save(data)

    def is_running(self, app_id: str) -> bool:
        entry = self.get(app_id)
        return bool(entry and pid_alive(entry.get("pid", -1), entry.get("pid_ctime")))

    def cleanup(self) -> None:
        """Drop entries whose process is gone (or whose PID was reused)."""
        with self._lock:
            data = self._load()
            stale = [
                app_id
                for app_id, entry in data.items()
                if not pid_alive(entry.get("pid", -1), entry.get("pid_ctime"))
            ]
            if stale:
                for app_id in stale:
                    del data[app_id]
                self._save(data)
