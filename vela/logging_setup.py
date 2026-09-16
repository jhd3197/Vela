"""One logging setup for both entry points: the console server and the tray.

`python -m vela` and the packaged tray used to configure logging differently —
the tray attached a rotating file handler, the console server wrote nothing to
disk — so the log a person could read depended on how Vela had been started.
Both now call `configure_logging(config)` and get the same files.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 2 MB x 5 keeps roughly a week of ordinary use without letting a chatty run
# fill the data directory.
MAX_BYTES = 2_000_000
BACKUP_COUNT = 5
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# httpx logs one INFO line per request. The assistant, the catalog and every
# app status poll go through it, so at INFO those lines crowd out everything a
# person opens the log to find.
QUIET_LOGGERS = ("httpx", "httpcore")

SERVER_LOG = "server.log"
AUDIT_LOG = "audit.log"

# Actions worth a permanent record, written to audit.log by `audit()`.
AUDIT_EVENTS = (
    "install",
    "uninstall",
    "launch",
    "stop",
    "update",
    "backup",
    "restore",
    "repair",
    "clear-log",
    # Anything that changes a file in a share: upload, rename, move, delete,
    # mkdir. A file the user cannot find should be traceable to who moved it.
    "files",
)

_configured: list[logging.Handler] = []


def _rotating(path: Path) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler


def configure_logging(config) -> list[logging.Handler]:
    """Attach Vela's file handlers and quiet the request noise.

    Safe to call twice: the second call removes the handlers the first one
    added rather than writing every line to the file twice.
    """
    teardown_logging()
    root = logging.getLogger()
    server = _rotating(config.logs_dir / SERVER_LOG)
    root.addHandler(server)
    root.setLevel(logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    audit_logger = logging.getLogger("vela.audit")
    audit_handler = _rotating(config.logs_dir / AUDIT_LOG)
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    # The audit trail is its own file. Without this every audited action would
    # also land in server.log, where it would be lost among the request lines.
    audit_logger.propagate = False
    _configured.extend((server, audit_handler))
    return [server, audit_handler]


def teardown_logging() -> None:
    """Detach and close the handlers `configure_logging` added."""
    root = logging.getLogger()
    audit_logger = logging.getLogger("vela.audit")
    while _configured:
        handler = _configured.pop()
        root.removeHandler(handler)
        audit_logger.removeHandler(handler)
        handler.close()


def audit(event: str, detail: str, *, actor: str = "local") -> None:
    """Record one operator action.

    `actor` is `local` when the request came from the Vela computer and
    `remote` when it arrived over the Wi-Fi listener, so a surprising entry can
    be traced to where it was asked for.
    """
    logging.getLogger("vela.audit").info("%s actor=%s %s", event, actor, detail)


def request_actor(auth, request) -> str:
    """`remote` for a request off the Wi-Fi listener, `local` otherwise."""
    try:
        return "remote" if auth.is_remote_request(request) else "local"
    except Exception:  # pragma: no cover - never fail an action over its log line
        return "local"
