"""Persistent desktops: workspaces inside Vela.

A desktop is a name, an appearance and the desk's two responsive boards. The
existing desk becomes Desktop 1 on the way up, without losing a widget.

`vela/desktop.py` is a different thing entirely — it is the Windows tray. New
workspace code belongs here.
"""

from .api import router
from .models import (
    MAX_DESKTOPS,
    MAX_NAME,
    DesktopConflict,
    DesktopError,
)
from .service import Desktops

__all__ = [
    "Desktops",
    "DesktopConflict",
    "DesktopError",
    "MAX_DESKTOPS",
    "MAX_NAME",
    "router",
]
