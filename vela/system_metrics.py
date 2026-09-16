"""Host metrics for the desk: CPU, memory, uptime and the volumes the user chose.

The psutil calls are lifted from ServerKit's `backend/app/services/system_service.py`
(MIT, same owner): `get_cpu_metrics`, `get_memory_metrics`, `get_disk_metrics`,
`get_system_info` and `_format_uptime`. Three things are different here.

* `cpu_percent` is called with `interval=None`. ServerKit blocks the request for
  100 ms to get a fresh reading; a desk widget polling every 10 s would pay that
  on every request. `interval=None` returns the usage since the previous call,
  which the sampler below makes meaningful.
* Disks are not enumerated. Listing every partition on the machine tells the
  user about drives they did not ask about; the desk shows the volumes named in
  `settings.desk.volumes`, plus the one Vela's own data lives on.
* psutil is imported lazily. It is a real dependency (`requirements.txt`) and
  the packaged server bundles it, but a source checkout that has not installed
  it, or a platform where it fails to import, must degrade to
  `{"available": False}` rather than turning the desk into a 500.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import write_json_atomic

# How often the sampler records a CPU reading, and how many it keeps. Ten
# minutes of history at one sample per 10 s is enough for the sparkline on a
# 2x1 widget and small enough to hold in memory without a store.
SAMPLE_INTERVAL_SECONDS = 10
HISTORY_LENGTH = 60

# Network totals are counted in memory and written at most this often: the
# sampler runs every 10 s, and six writes a minute to say "a few more kilobytes"
# is a lot of disk for a number nobody reads that fast. A crash costs at most a
# minute of the day's total.
NET_FLUSH_SECONDS = 60

# How many daily network files are kept. One small JSON per day, the same
# window the Launchpad's open counts use.
NET_KEEP_DAYS = 30

#: Addresses that mean "this computer only".
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", ""}


def _psutil():
    """psutil, or None when it is not installed or will not import here."""
    try:
        import psutil  # noqa: PLC0415 - deliberately lazy; see the module docstring
    except Exception:
        return None
    return psutil


def format_uptime(seconds: float) -> str:
    """"41d 6h 12m" — the same shape ServerKit prints."""
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "0m"


class SystemMetrics:
    """One snapshot of what this computer is doing, plus a short CPU history.

    The history is in memory only. It starts empty on every restart, which is
    honest: Vela does not store metrics, so a sparkline can only show what this
    process has seen.
    """

    def __init__(
        self,
        config,
        settings,
        *,
        interval: int = SAMPLE_INTERVAL_SECONDS,
        secure: Any = None,
    ):
        self._config = config
        self._settings = settings
        self._interval = interval
        # Whether a secure listener is up. Passed in because the certificate
        # belongs to phone access, which this module has no business importing.
        self._secure = secure
        self._lock = threading.Lock()
        self._history: deque[tuple[str, float]] = deque(maxlen=HISTORY_LENGTH)
        self._task: asyncio.Task | None = None
        # Network counters are cumulative since boot, so only the difference
        # between two readings belongs to today.
        self._net_last: tuple[int, int] | None = None
        self._net_day: str | None = None
        self._net_totals = {"bytes_sent": 0, "bytes_recv": 0}
        self._net_flushed = 0.0

    # ------------------------------------------------------------ sampling --

    def start(self) -> None:
        """Begin recording CPU samples on the server's event loop."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        with self._lock:
            self._flush_locked()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        # The first psutil reading after import covers the time since boot, so
        # it is thrown away rather than recorded as a sample.
        self.sample()
        self._history.clear()
        while True:
            await asyncio.sleep(self._interval)
            try:
                self.sample()
            except Exception:
                # A transient psutil failure must not kill the sampler.
                pass

    def sample(self) -> float | None:
        """Record one CPU reading. Returns the percentage, or None without psutil."""
        psutil = _psutil()
        if psutil is None:
            return None
        self._sample_network(psutil)
        percent = psutil.cpu_percent(interval=None)
        if percent is None:
            return None
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._history.append((stamp, round(float(percent), 1)))
        return float(percent)

    # ------------------------------------------------------------- network --

    @property
    def _metrics_dir(self) -> Path:
        return self._config.data_dir / "metrics"

    def _net_path(self, day: str) -> Path:
        return self._metrics_dir / f"net-{day}.json"

    def _read_day(self, day: str) -> dict[str, int]:
        """A day's stored totals, so a restart resumes rather than starting over."""
        try:
            stored = json.loads(self._net_path(day).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"bytes_sent": 0, "bytes_recv": 0}
        if not isinstance(stored, dict):
            return {"bytes_sent": 0, "bytes_recv": 0}
        out = {}
        for field in ("bytes_sent", "bytes_recv"):
            value = stored.get(field)
            out[field] = int(value) if isinstance(value, int) and value >= 0 else 0
        return out

    def _flush_locked(self) -> None:
        if self._net_day is None:
            return
        try:
            self._metrics_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._net_path(self._net_day), dict(self._net_totals))
        except OSError:
            # A full or read-only disk must not stop the sampler; the number is
            # a nicety and the next flush will try again.
            return
        self._net_flushed = time.time()
        self._prune_days()

    def _prune_days(self) -> None:
        cutoff = (date.today() - timedelta(days=NET_KEEP_DAYS)).isoformat()
        try:
            for path in self._metrics_dir.glob("net-*.json"):
                if path.stem[4:] < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def _sample_network(self, psutil) -> None:
        try:
            counters = psutil.net_io_counters()
        except Exception:
            return
        if counters is None:
            return
        reading = (int(counters.bytes_sent), int(counters.bytes_recv))
        today = date.today().isoformat()
        with self._lock:
            if self._net_day != today:
                # Midnight, or the first sample of this process. Either way the
                # day that just ended is written out and the new one starts from
                # whatever is already stored for it.
                self._flush_locked()
                self._net_day = today
                self._net_totals = self._read_day(today)
                self._net_last = reading
                self._flush_locked()
                return
            if self._net_last is None:
                self._net_last = reading
                return
            sent = reading[0] - self._net_last[0]
            recv = reading[1] - self._net_last[1]
            self._net_last = reading
            if sent < 0 or recv < 0:
                # The counters went backwards: a reboot, or an interface reset.
                # What happened in between is not ours to invent.
                return
            self._net_totals["bytes_sent"] += sent
            self._net_totals["bytes_recv"] += recv
            if time.time() - self._net_flushed >= NET_FLUSH_SECONDS:
                self._flush_locked()

    def network_today(self) -> dict[str, Any]:
        """What this computer has sent and received since local midnight."""
        today = date.today().isoformat()
        with self._lock:
            totals = dict(self._net_totals) if self._net_day == today else self._read_day(today)
        return {
            "day": today,
            "bytesSent": totals["bytes_sent"],
            "bytesRecv": totals["bytes_recv"],
            "total": totals["bytes_sent"] + totals["bytes_recv"],
        }

    def connection_mode(self) -> str:
        """How this server can be reached: `https`, `lan` or `local`.

        Read from what the server was actually started with rather than from a
        setting, so it cannot claim to be private while listening to the network.
        """
        if os.environ.get("VELA_CERT_FILE"):
            return "https"
        try:
            if self._secure is not None and self._secure():
                return "https"
        except Exception:
            pass
        if self._config.remote_access:
            return "lan"
        host = (os.environ.get("VELA_HOST") or "127.0.0.1").strip().strip("[]").lower()
        return "local" if host in LOOPBACK else "lan"

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"t": stamp, "cpu": value} for stamp, value in self._history]

    # ------------------------------------------------------------ snapshot --

    def volumes(self) -> list[dict[str, str]]:
        """The volumes the user configured, plus the one Vela's data sits on.

        The data volume is always offered because it is the one that filling up
        stops Vela working, and the user never has to know its path to care.
        """
        desk = self._settings.get("desk") or {}
        configured = desk.get("volumes") if isinstance(desk, dict) else None
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in configured if isinstance(configured, list) else []:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            out.append({"path": path, "label": str(entry.get("label") or "").strip() or path})
        data_dir = str(self._config.data_dir)
        if data_dir not in seen:
            out.append({"path": data_dir, "label": "Vela data"})
        return out

    def snapshot(self) -> dict[str, Any]:
        psutil = _psutil()
        if psutil is None:
            # Everything the desk needs to say "this server cannot report it".
            return {
                "available": False,
                "host": platform.node() or "",
                "disks": [],
                "history": [],
                "network": {"today": self.network_today(), "mode": self.connection_mode()},
            }

        memory = psutil.virtual_memory()
        boot = psutil.boot_time()
        uptime = max(0.0, time.time() - boot)
        return {
            "available": True,
            "host": platform.node() or socket.gethostname() or "",
            "platform": platform.system(),
            "cpu": {
                "percent": round(float(psutil.cpu_percent(interval=None)), 1),
                "cores": psutil.cpu_count(logical=True) or psutil.cpu_count() or 0,
            },
            "memory": {
                "total": int(memory.total),
                "used": int(memory.used),
                "percent": round(float(memory.percent), 1),
            },
            "uptime": {
                "seconds": int(uptime),
                "since": datetime.fromtimestamp(boot, timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "human": format_uptime(uptime),
            },
            "disks": self._disks(psutil),
            "history": self.history(),
            "network": {"today": self.network_today(), "mode": self.connection_mode()},
        }

    def _disks(self, psutil) -> list[dict[str, Any]]:
        disks = []
        for volume in self.volumes():
            path = volume["path"]
            try:
                usage = psutil.disk_usage(path)
            except (OSError, PermissionError, ValueError):
                # A removable drive that is not plugged in, or a path the user
                # removed. Say so instead of dropping the volume silently: the
                # widget they added is still on their board.
                disks.append({**volume, "reachable": False})
                continue
            disks.append(
                {
                    **volume,
                    "reachable": True,
                    "total": int(usage.total),
                    "used": int(usage.used),
                    "free": int(usage.free),
                    "percent": round(float(usage.percent), 1),
                }
            )
        return disks


def validate_volumes(value: Any) -> list[dict[str, str]]:
    """Check a `desk.volumes` patch, raising ValueError with a usable message.

    A volume the user cannot see the contents of is not worth putting on a
    board, so the path has to exist and be a directory right now.
    """
    if not isinstance(value, list):
        raise ValueError("volumes must be a list")
    if len(value) > 12:
        raise ValueError("at most 12 volumes")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("each volume is an object with a path and a label")
        path = str(entry.get("path") or "").strip()
        if not path:
            raise ValueError("each volume needs a path")
        if not Path(path).is_dir():
            raise ValueError(f"{path} is not a folder on this computer")
        resolved = str(Path(path))
        if resolved in seen:
            raise ValueError(f"{path} is listed twice")
        seen.add(resolved)
        label = str(entry.get("label") or "").strip()[:60]
        out.append({"path": resolved, "label": label or Path(resolved).name or resolved})
    return out
