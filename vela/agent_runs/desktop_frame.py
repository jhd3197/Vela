"""One picture of the whole desktop — the single, deliberate exception.

Everywhere else in Vela the rule is that a frame is one view's picture and a
capture of the whole desktop must not exist (see `viewer.py` for why: the
desktop picture is the one that would contain the owner's approval prompt).
This module exists because a controller showing its bot's computer in a chat
preview needs one image, and it is allowed to exist only under three locks
turned together:

1. **Controller mode.** The route answers 404 in every other mode, the way the
   driven-run routes do — to anything that is not a controller, the address
   simply does not exist.
2. **The controller token.** The auth middleware already accepts nothing else
   on an `/api` route in this mode; no person session, no app session.
3. **An explicit opt-in.** `VELA_CONTROLLER_DESKTOP_FRAME=1`. Off by default
   even in the computer image, so the never-capture rule stays the rule until
   a controller asks for the exception.

And it is a **composite, not a capture**. Nothing here asks the operating
system or the browser for a new kind of picture: each window is drawn from the
same per-view frame the watcher route serves — a page's capture for an app or
site, the recent screen text for a terminal or an editor — placed by the
desktop's own layout. A window kind the frame route refuses (the owner's
`host` surfaces, the task window) is not drawn at all, so whatever per-view
frames hide stays hidden here by construction rather than by a second rule
that could drift.

The composite is rate-limited (one compose per interval per desktop, however
loud the polling), cached in memory only, and named by its content: the ETag
is the digest of the bytes, so a preview that has not changed costs a 304.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..desktops.models import DesktopError, validate_id
from ..desktops.runtime import RuntimeUnavailable

#: The preview's size, 16:10. The work area the layout's window bounds are
#: measured in is taken to be the same size, so placement is a translation,
#: never a guess at somebody's monitor.
PREVIEW_WIDTH = 1280
PREVIEW_HEIGHT = 800

#: How tall a window's title strip is, in preview pixels.
TITLE_BAR = 26

#: At most one compose per desktop per interval, however often the preview is
#: asked for. Inside the interval the cached bytes answer, and the per-window
#: frames underneath are shared with every other watcher anyway.
MIN_COMPOSE_MS = 800

#: The window kinds that have a frame at all. `host` and `agent` views are the
#: owner's own surfaces; the frame route refuses them, and so does this.
FRAMEABLE_KINDS = ("app", "web", "terminal", "editor")

#: How many lines of a terminal's or an editor's text frame a window shows.
MAX_TEXT_LINES = 40

#: The flat palette. Deterministic on purpose: a preview is not a theme, and a
#: test can name these colours.
BACKGROUND = (22, 25, 31)
WINDOW_BORDER = (64, 70, 84)
TITLE_FILL = (36, 41, 51)
TITLE_TEXT = (214, 220, 230)
BODY_FILL = (15, 17, 22)
TEXT_FILL = (10, 12, 16)
TEXT_COLOR = (198, 206, 218)

_FONTS: dict[int, Any] = {}


def _font(size: int):
    """The bundled default face, loaded once per size. Ships with Pillow, so
    every platform draws the same picture."""
    if size not in _FONTS:
        _FONTS[size] = ImageFont.load_default(size=size)
    return _FONTS[size]


def placements(
    views: list[dict[str, Any]], layout: dict[str, Any]
) -> list[tuple[dict[str, Any], tuple[int, int, int, int]]]:
    """Where each visible window goes, in preview pixels, back to front.

    The desktop's own layout decides: a maximized window fills the preview, a
    split puts its two panes side by side at the divider, and floating windows
    stand where their bounds say — or, for a window that never had bounds,
    at a deterministic cascade so the preview never has to invent a place.
    Minimized windows are not drawn: they are not on the desktop either.
    """
    showing = [
        view
        for view in views
        if view["kind"] in FRAMEABLE_KINDS and not view["window"]["minimized"]
    ]
    by_id = {view["id"]: view for view in showing}
    arrangement = layout.get("arrangement")
    if arrangement == "maximized" and layout.get("maximizedView") in by_id:
        view = by_id[layout["maximizedView"]]
        return [(view, (0, 0, PREVIEW_WIDTH, PREVIEW_HEIGHT))]
    if arrangement == "split":
        panes = []
        divider = layout.get("dividerRatio")
        ratio = float(divider) if isinstance(divider, (int, float)) else 0.5
        split_at = int(PREVIEW_WIDTH * ratio)
        if layout.get("primaryView") in by_id:
            panes.append((by_id[layout["primaryView"]], (0, 0, split_at, PREVIEW_HEIGHT)))
        if layout.get("secondaryView") in by_id:
            panes.append(
                (
                    by_id[layout["secondaryView"]],
                    (split_at, 0, PREVIEW_WIDTH - split_at, PREVIEW_HEIGHT),
                )
            )
        if panes:
            return panes
    ordered = sorted(showing, key=lambda view: (view["window"]["stack"], view["position"]))
    placed = []
    for index, view in enumerate(ordered):
        bounds = view["window"]["bounds"]
        if bounds:
            rect = (bounds["x"], bounds["y"], bounds["width"], bounds["height"])
        else:
            # A window nobody placed still gets a stable place: offset by its
            # turn in the stacking order, so two unplaced windows do not sit
            # exactly on top of each other.
            step = index % 6
            rect = (32 + step * 40, 32 + step * 36, 880, 620)
        placed.append((view, rect))
    return placed


class DesktopFrames:
    """The composited preview for every desktop, cached and rate-limited."""

    def __init__(self, desktops, viewer, config):
        self.desktops = desktops
        self.viewer = viewer
        self._config = config
        self._lock = threading.Lock()
        #: desktop id -> the latest composite and what names it.
        self._latest: dict[str, dict[str, Any]] = {}
        #: desktop id -> how many composites have been made. Rises with every
        #: recompose, so a watcher can tell "same picture" from "same bytes".
        self._revisions: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        """Controller mode, and the opt-in. Both, or the address does not exist."""
        config = self._config
        return bool(
            config is not None
            and getattr(config, "controller_mode", False)
            and getattr(config, "controller_desktop_frame", False)
        )

    async def frame(self, desktop_id: str) -> dict[str, Any]:
        """The current composite, recomposed at most once per interval."""
        if not self.enabled:
            raise DesktopError(
                404,
                "The desktop frame exists only in controller mode, and only when "
                "the controller has switched it on.",
            )
        desktop_id = validate_id(desktop_id)
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        with self._lock:
            cached = self._latest.get(desktop_id)
            if cached and (time.time() - cached["composedAt"]) * 1000 < MIN_COMPOSE_MS:
                return cached
        record = await self._compose(desktop_id)
        with self._lock:
            revision = self._revisions.get(desktop_id, 0) + 1
            self._revisions[desktop_id] = revision
            record["revision"] = revision
            self._latest[desktop_id] = record
        return record

    def forget_desktop(self, desktop_id: str) -> None:
        with self._lock:
            self._latest.pop(desktop_id, None)
            self._revisions.pop(desktop_id, None)

    # ------------------------------------------------------------ composing --

    async def _compose(self, desktop_id: str) -> dict[str, Any]:
        """Draw the desktop from the frames the watcher route already serves."""
        listing = self.desktops.views(desktop_id)
        canvas = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), BACKGROUND)
        for view, rect in placements(listing["views"], listing["layout"]):
            content = await self._content(desktop_id, view)
            canvas.paste(self._window(view, rect, content), (rect[0], rect[1]))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        data = buffer.getvalue()
        return {
            "bytes": data,
            "digest": hashlib.sha256(data).hexdigest(),
            "width": PREVIEW_WIDTH,
            "height": PREVIEW_HEIGHT,
            "composedAt": time.time(),
        }

    async def _content(self, desktop_id: str, view: dict[str, Any]) -> dict[str, Any] | None:
        """What goes inside one window: its own frame, or None for a blank body.

        A window whose capture fails — its page is gone, the browser is
        restarting — becomes a blank body rather than failing the whole
        preview: the other windows are still true.
        """
        try:
            frame = await self.viewer.frame(desktop_id, view["id"], max_age_ms=MIN_COMPOSE_MS)
        except (DesktopError, RuntimeUnavailable):
            return None
        if frame.get("kind") in ("terminal", "editor"):
            return {"text": frame.get("text") or ""}
        try:
            path = self.viewer.frame_file(desktop_id, view["id"], frame["digest"])
            with Image.open(path) as opened:
                return {"image": opened.convert("RGB")}
        except (DesktopError, OSError):
            return None

    def _window(
        self, view: dict[str, Any], rect: tuple[int, int, int, int], content: dict[str, Any] | None
    ) -> Image.Image:
        """One window: a title strip and a body, at the rectangle the layout gave."""
        _x, _y, width, height = rect
        window = Image.new("RGB", (max(1, width), max(1, height)), BODY_FILL)
        draw = ImageDraw.Draw(window)
        draw.rectangle(
            (0, 0, window.width - 1, window.height - 1), outline=WINDOW_BORDER
        )
        if height > TITLE_BAR:
            draw.rectangle((0, 0, window.width - 1, TITLE_BAR - 1), fill=TITLE_FILL)
            title = str(view.get("title") or "")
            if title:
                draw.text((8, 6), title, font=_font(13), fill=TITLE_TEXT)
        body = (1, TITLE_BAR, window.width - 1, window.height - 1)
        body_width, body_height = body[2] - body[0], body[3] - body[1]
        if content is None or body_width <= 0 or body_height <= 0:
            return window
        if "image" in content:
            picture = ImageOps.contain(content["image"], (body_width, body_height))
            window.paste(
                picture,
                (body[0] + (body_width - picture.width) // 2,
                 body[1] + (body_height - picture.height) // 2),
            )
            return window
        # A terminal's or an editor's frame is text: the recent screen, drawn
        # as the plain lines it already is. No emulator, no pretending to be a
        # picture of one.
        draw.rectangle(body, fill=TEXT_FILL)
        lines = str(content.get("text") or "").splitlines()[-MAX_TEXT_LINES:]
        line_height = 15
        shown = lines[-max(1, body_height // line_height):]
        for row, line in enumerate(shown):
            draw.text(
                (body[0] + 6, body[1] + 4 + row * line_height),
                line,
                font=_font(12),
                fill=TEXT_COLOR,
            )
        return window
