"""Read the logs Vela writes, from the dashboard rather than a file manager.

List/tail/search/clear follow ServerKit's `log_service.py` (MIT, same owner).
ServerKit shells out to `tail`, `grep` and `truncate` and reads journald,
syslog and Docker; Vela reads its own `logs_dir` with Python I/O, because the
only logs it may show are the ones it wrote, on every platform it runs on.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_setup import AUDIT_LOG, SERVER_LOG, audit

# A name may address a log file and nothing else: no separators, no drive
# letters, no `..`. Rotation suffixes (`server.log.1`) are part of the name.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_ROTATED_RE = re.compile(r"^(?P<base>.+\.log)\.(?P<index>\d+)$")

MAX_LINES = 5000
DEFAULT_LINES = 200
# Reading a log must not depend on the log being small. Tailing walks the file
# from the end in blocks so a 2 MB file costs one block, not 2 MB of memory.
_BLOCK = 64 * 1024


class LogError(Exception):
    """A log could not be read, and why, in words worth showing a person."""


def _kind(name: str) -> str:
    base = _ROTATED_RE.match(name)
    stem = base.group("base") if base else name
    if stem == SERVER_LOG:
        return "server"
    if stem == AUDIT_LOG:
        return "audit"
    if stem.startswith("worker"):
        return "worker"
    return "app"


def _count_and_tail(path: Path, lines: int, from_end: bool) -> tuple[list[str], int, bool]:
    """Return the requested lines, the file's total line count and whether it was cut."""
    total = 0
    with path.open("rb") as handle:
        for _ in handle:
            total += 1
    if lines <= 0:
        return [], total, total > 0
    if not from_end:
        collected: list[bytes] = []
        with path.open("rb") as handle:
            for raw in handle:
                collected.append(raw)
                if len(collected) >= lines:
                    break
        selected = collected
    else:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            buffer = b""
            while end > 0 and buffer.count(b"\n") <= lines:
                step = min(_BLOCK, end)
                end -= step
                handle.seek(end)
                buffer = handle.read(step) + buffer
        selected = buffer.splitlines(keepends=True)[-lines:]
    text = [raw.decode("utf-8", errors="replace").rstrip("\r\n") for raw in selected]
    return text, total, total > len(text)


class LogStore:
    """The files under `logs_dir`, and nothing else on the computer.

    Every method takes a bare file name. `_resolve` is the single gate: it
    rejects anything that is not a plain name inside `logs_dir`, so a caller
    cannot walk out of the directory with `..` or an absolute path.
    """

    def __init__(self, logs_dir: Path):
        self._dir = Path(logs_dir)

    def _resolve(self, name: str) -> Path:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise LogError("unknown log")
        path = (self._dir / name).resolve()
        root = self._dir.resolve()
        if path.parent != root or not path.is_file():
            raise LogError("unknown log")
        return path

    def files(self) -> list[dict[str, Any]]:
        """Every readable log, newest first, with rotated files under their base."""
        entries: list[dict[str, Any]] = []
        if not self._dir.is_dir():
            return entries
        for child in sorted(self._dir.iterdir()):
            if not child.is_file() or not _NAME_RE.match(child.name):
                continue
            rotated = _ROTATED_RE.match(child.name)
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "kind": _kind(child.name),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    # The dashboard groups a rotated file under the log it came
                    # from instead of listing `server.log.1` as its own log.
                    "base": rotated.group("base") if rotated else child.name,
                    "rotated": bool(rotated),
                }
            )
        entries.sort(key=lambda entry: (entry["base"], entry["rotated"], entry["name"]))
        return entries

    def read(self, name: str, lines: int = DEFAULT_LINES, from_end: bool = True) -> dict[str, Any]:
        path = self._resolve(name)
        lines = max(0, min(int(lines), MAX_LINES))
        try:
            text, total, truncated = _count_and_tail(path, lines, from_end)
        except OSError as exc:
            raise LogError(f"could not read {name}: {exc}") from exc
        return {"name": name, "lines": text, "total": total, "truncated": truncated}

    def search(self, name: str, pattern: str, lines: int = DEFAULT_LINES) -> dict[str, Any]:
        """Case-insensitive substring search; `/…/` runs the inside as a regex."""
        path = self._resolve(name)
        lines = max(0, min(int(lines), MAX_LINES))
        pattern = pattern or ""
        matcher = None
        if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
            try:
                matcher = re.compile(pattern[1:-1], re.IGNORECASE)
            except re.error as exc:
                raise LogError(f"that is not a valid pattern: {exc}") from exc
        needle = pattern.casefold()
        matches: list[str] = []
        count = 0
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    hit = matcher.search(line) if matcher else (needle in line.casefold())
                    if not hit:
                        continue
                    count += 1
                    if len(matches) < lines:
                        matches.append(line)
        except OSError as exc:
            raise LogError(f"could not read {name}: {exc}") from exc
        return {
            "name": name,
            "lines": matches,
            "total": count,
            "truncated": count > len(matches),
            "pattern": pattern,
        }

    def clear(self, name: str, *, actor: str = "local") -> dict[str, Any]:
        """Truncate a log in place so the open handler keeps writing to it."""
        path = self._resolve(name)
        try:
            with path.open("r+b") as handle:
                handle.truncate(0)
        except OSError as exc:
            raise LogError(f"could not clear {name}: {exc}") from exc
        audit("clear-log", f"log={name}", actor=actor)
        return {"name": name, "cleared": True}

    def path(self, name: str) -> Path:
        """The file behind a name, for a download response."""
        return self._resolve(name)
