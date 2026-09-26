"""What an app may contribute to Vela's top bar.

Two surfaces, one capability, and the same trust model as desk widgets: the
app declares, the host draws. Nothing an app sends is markup, nothing is a
URL, and no app code runs on the bar.

**Menus** are declared in the manifest, so a person sees them at install and
they cannot change while the app is running. An item's `action` comes from a
closed set the host knows how to perform; an item without one is a label the
host draws and does not act on. That is deliberate: a menu whose action were
an arbitrary string would be a name the host has to interpret, which is the
beginning of a plugin mechanism rather than a contribution contract.

**Status items** are published at runtime over the bridge and are validated
where they are received, in the dashboard's bridge host
(`web/src/bridge/host.js`), because they belong to one open window and are
never stored on this server. The caps and the icon set they are checked
against are the ones written down here and in `docs/CONTRACT.md`, so the two
halves of the contract have one source of prose even though the runtime half
runs in the browser. See `plans/TOP-BAR-PROGRESS.md`, decision D05, for why
they are not engine rows.
"""

from __future__ import annotations

import re
from typing import Any

#: The manifest side of the contract.
MENU_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_MENUS_PER_APP = 4
MAX_MENU_ITEMS = 8
MAX_MENU_LABEL = 24

#: What a menu item may ask the host to do. `return` leaves the app and goes
#: back to the desk, leaving the window open; `close` ends the window. Both are
#: things the host already does on its own controls, so an app cannot reach
#: anything through a menu that it could not reach through the bridge.
MENU_ACTIONS = ("return", "close")

#: The published-item caps, named here so `docs/CONTRACT.md` and the bridge
#: host agree with one another. `tests/test_topbar.py` checks that the bridge
#: host really uses these numbers.
MAX_STATUS_ITEMS = 3
MAX_STATUS_LABEL = 12
MAX_STATUS_TITLE = 200
MAX_STATUS_BYTES = 1024

#: The icons a status item may name. A closed list, not a free string: an icon
#: is drawn by the host from marks it already bundles, so an app cannot point
#: the bar at an image it controls. Chosen for the things a small bar item
#: actually says — a measurement, a queue, a connection, a warning.
STATUS_ICONS = (
    "battery",
    "bell",
    "calendar",
    "check",
    "clock",
    "cloud",
    "database",
    "download",
    "envelope",
    "globe",
    "heart",
    "lock",
    "moon",
    "music",
    "pulse",
    "sun",
    "thermometer",
    "upload",
    "warning",
    "wifi",
)

#: How a status item is coloured. The host maps these onto its own tones; an
#: app never names a colour.
STATUS_TONES = ("neutral", "positive", "caution", "critical")


def validate_menu_declarations(topbar: Any, folder: str) -> list[dict[str, Any]]:
    """Check a manifest's `topbar` block. Raises ValueError with the reason.

    The JSON Schema has already bounded the shape for a v2 manifest; this is
    the half that a schema cannot express — unique ids within the app and
    within each menu — plus the same checks again, so a caller that reaches
    this without the schema still fails closed.
    """
    if not isinstance(topbar, dict):
        raise ValueError(f"{folder}: topbar must be an object")
    unknown = set(topbar) - {"menus"}
    if unknown:
        raise ValueError(f"{folder}: topbar has unknown fields: {', '.join(sorted(unknown))}")
    menus = topbar.get("menus", [])
    if not isinstance(menus, list):
        raise ValueError(f"{folder}: topbar menus must be a list")
    if len(menus) > MAX_MENUS_PER_APP:
        raise ValueError(f"{folder}: at most {MAX_MENUS_PER_APP} top bar menus per app")
    seen: set[str] = set()
    out = []
    for menu in menus:
        if not isinstance(menu, dict):
            raise ValueError(f"{folder}: each top bar menu is an object")
        unknown = set(menu) - {"id", "label", "items"}
        if unknown:
            raise ValueError(f"{folder}: top bar menu has unknown fields: "
                             f"{', '.join(sorted(unknown))}")
        menu_id = menu.get("id")
        if not isinstance(menu_id, str) or not MENU_ID_RE.match(menu_id):
            raise ValueError(f"{folder}: top bar menu id must match [a-z][a-z0-9-]{{0,31}}")
        if menu_id in seen:
            raise ValueError(f"{folder}: duplicate top bar menu id {menu_id!r}")
        seen.add(menu_id)
        label = menu.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > MAX_MENU_LABEL:
            raise ValueError(f"{folder}: top bar menu {menu_id} needs a label of at most "
                             f"{MAX_MENU_LABEL} characters")
        items = menu.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{folder}: top bar menu {menu_id} needs at least one item")
        if len(items) > MAX_MENU_ITEMS:
            raise ValueError(f"{folder}: top bar menu {menu_id} holds at most "
                             f"{MAX_MENU_ITEMS} items")
        item_ids: set[str] = set()
        checked = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{folder}: each top bar menu item is an object")
            unknown = set(item) - {"id", "label", "action"}
            if unknown:
                raise ValueError(f"{folder}: top bar menu item has unknown fields: "
                                 f"{', '.join(sorted(unknown))}")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not MENU_ID_RE.match(item_id):
                raise ValueError(
                    f"{folder}: top bar menu item id must match [a-z][a-z0-9-]{{0,31}}")
            if item_id in item_ids:
                raise ValueError(f"{folder}: duplicate item id {item_id!r} in top bar menu "
                                 f"{menu_id}")
            item_ids.add(item_id)
            item_label = item.get("label")
            if (not isinstance(item_label, str) or not item_label.strip()
                    or len(item_label) > MAX_MENU_LABEL):
                raise ValueError(f"{folder}: top bar menu item {item_id} needs a label of at "
                                 f"most {MAX_MENU_LABEL} characters")
            entry = {"id": item_id, "label": item_label}
            if "action" in item:
                if item["action"] not in MENU_ACTIONS:
                    raise ValueError(f"{folder}: top bar menu action must be one of "
                                     f"{', '.join(MENU_ACTIONS)}")
                entry["action"] = item["action"]
            checked.append(entry)
        out.append({"id": menu_id, "label": label, "items": checked})
    return out
