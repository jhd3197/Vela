"""Shared folders.

Every route names a share by id and a path relative to it, and `files.resolve`
is the only thing that turns those into a real path. There is deliberately no
route that accepts a whole path.
"""

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse

from ..errors_http import TooLarge
from ..files import MAX_UPLOAD_BYTES, TRASH_DAYS
from ..logging_setup import request_actor


def router(files, auth) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["files"])

    @api.get("/files")
    def list_shares() -> dict:
        return {"shares": files.shares(), "trashDays": TRASH_DAYS, "maxUpload": MAX_UPLOAD_BYTES}

    @api.get("/files/{share_id}")
    def list_share(share_id: str, path: str = "") -> dict:
        return files.list(share_id, path)

    @api.get("/files/{share_id}/download")
    def download_file(share_id: str, path: str = "", inline: bool = False):
        target, kind = files.open_file(share_id, path)
        # Everything is sent as a download unless the browser asked to show it
        # and it is a type a browser renders without running anything. An SVG is
        # an image that can carry script, so it is never shown inline.
        showable = inline and kind in {"image", "video", "audio", "pdf", "text"}
        if showable and target.suffix.lower() == ".svg":
            showable = False
        return FileResponse(
            target,
            filename=target.name,
            content_disposition_type="inline" if showable else "attachment",
        )

    @api.post("/files/{share_id}/folder")
    def create_folder(share_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict:
        return files.mkdir(
            share_id,
            str(payload.get("path") or ""),
            str(payload.get("name") or ""),
            actor=request_actor(auth, request),
        )

    @api.post("/files/{share_id}/rename")
    def rename_entry(share_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict:
        return files.rename(
            share_id,
            str(payload.get("path") or ""),
            str(payload.get("name") or ""),
            actor=request_actor(auth, request),
        )

    @api.post("/files/{share_id}/move")
    def move_entry(share_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict:
        return files.move(
            share_id,
            str(payload.get("path") or ""),
            str(payload.get("into") or ""),
            actor=request_actor(auth, request),
        )

    @api.delete("/files/{share_id}")
    def delete_entry(share_id: str, request: Request, path: str = "") -> dict:
        return files.delete(share_id, path, actor=request_actor(auth, request))

    @api.post("/files/{share_id}/upload")
    async def upload_file(share_id: str, request: Request, path: str = "", name: str = "") -> dict:
        """Stream one upload into a share.

        The body is the file's bytes and the name rides in the query, which
        keeps a 2 GB upload out of a multipart parser and lets the size cap be
        enforced chunk by chunk rather than after the fact.
        """
        actor = request_actor(auth, request)
        collected: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise TooLarge("That file is larger than 2 GB.",
                               code="files.upload_too_large")
            collected.append(chunk)
        return files.save_upload(share_id, path, name, collected, actor=actor)

    @api.get("/files-trash")
    def list_trash() -> dict:
        files.sweep_trash()
        return files.trash()

    return api
