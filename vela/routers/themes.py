"""The themes a user can pick, import, export and remove.

Reading is open to the dashboard; writing changes what this computer looks like
to everyone who opens it, so it goes through the same owner session everything
else does. A theme arrives as a file the user chose: Vela never fetches one, and
nothing here reaches the network.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..themes import MAX_THEME_BYTES, STOCK_SLUG, ThemeError


def router(themes, settings) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["themes"])

    @api.get("/themes")
    def list_themes() -> dict:
        """Bundled first, then imported, each with the strip Personalise draws."""
        return {"themes": themes.list(), "selected": settings.public_view().get("theme_id")}

    @api.get("/themes/{slug}")
    def get_theme(slug: str) -> dict:
        return themes.get(slug)

    @api.get("/themes/{slug}/export")
    def export_theme(slug: str) -> Response:
        """The stored document as a file. What comes out imports back in."""
        return Response(
            content=themes.export(slug),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{slug}.vela-theme.json"'},
        )

    @api.post("/themes/import")
    async def import_theme(request: Request) -> dict:
        """Validate, sanitize and store. The answer says what was left out.

        The body is the theme document itself, as JSON. The dashboard reads the
        file the user picked and sends its contents: that keeps the upload path
        off the server -- no multipart parser, no temporary file, nothing
        written anywhere until the document has been checked.
        """
        raw = await request.body()
        if len(raw) > MAX_THEME_BYTES:
            raise ThemeError(
                f"a theme is at most {MAX_THEME_BYTES // 1024} KB",
                status=413, code="theme.too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ThemeError("that is not a Vela theme: it is not JSON") from exc
        if not isinstance(payload, dict):
            raise ThemeError("a theme is a JSON object")
        # A document sent on its own, or wrapped as {theme, replace} by the
        # review sheet once the user has confirmed what it is about to apply.
        document = payload.get("theme") if isinstance(payload.get("theme"), dict) else payload
        checked = themes.import_document(document, replace=bool(payload.get("replace")))
        return {
            "ok": True,
            "theme": checked["theme"],
            "dropped": checked["dropped"],
            "unknown": checked["unknown"],
        }

    @api.delete("/themes/{slug}")
    def remove_theme(slug: str) -> dict:
        themes.remove(slug)
        # A theme that was in use cannot simply vanish from under the user, so
        # the selection falls back to the stock look in the same request rather
        # than leaving the dashboard pointed at a file that is gone.
        fell_back = settings.public_view().get("theme_id") == slug
        if fell_back:
            settings.patch({"theme_id": STOCK_SLUG})
        return {"ok": True, "selected": STOCK_SLUG if fell_back else None}

    return api
