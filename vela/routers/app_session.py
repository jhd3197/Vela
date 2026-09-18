"""What an app may do with its own session and its own stored document.

Every route under `/api/app` is the app talking about itself: the session on
the request decides which app that is, so nothing in a payload can name
another one. The `/api/apps/{app_id}` routes here are the owner's half —
opening a session and migrating a document — and are hub-authenticated.
"""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class StorageWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Any
    revision: int = Field(ge=0)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


class AppFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Short names an SDK announces. Bounded because this arrives from an app.
    features: list[str] = Field(default_factory=list, max_length=8)


def router(app_services, auth, lifecycle, desktops) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["app-session"])

    @api.post("/apps/{app_id}/session")
    def open_app_session(app_id: str, request: Request):
        with lifecycle.lock:
            return app_services.open(app_id, request.state.hub_token)

    @api.delete("/app/session")
    def close_app_session(request: Request):
        auth.revoke(request.headers.get("authorization", "").removeprefix("Bearer "))
        return {"ok": True}

    @api.get("/app/storage")
    def read_app_storage(request: Request):
        with lifecycle.lock:
            return app_services.read(request.state.app_session)

    @api.put("/app/storage")
    def write_app_storage(payload: StorageWrite, request: Request):
        with lifecycle.lock:
            return app_services.write(request.state.app_session, payload.value, payload.revision)

    @api.post("/app/features")
    def app_features(payload: AppFeatures, request: Request):
        """What this app's SDK announced it understands.

        Recorded so that a limitation is visible before a run walks into it: an
        app whose SDK cannot wait for an approval is one an agent should not be
        asked to make changes in. Nothing here grants anything — the only thing
        this can do is make Vela more careful.
        """
        return desktops.note_view_features(request.state.app_session, payload.features)

    @api.get("/app/approvals/{request_id}")
    def app_approval_status(request_id: str, request: Request):
        """Where the change this app is waiting on has got to.

        The app's own host asks this while it waits. It can see only its own
        request — matched on the desktop, the app and the installation — and
        there is nothing here that resolves anything. Approving is the owner's,
        on an owner-authenticated route this session cannot reach.
        """
        return desktops.approval_for_session(request.state.app_session, request_id)

    @api.post("/app/approvals/{request_id}/extend")
    def app_approval_extend(request_id: str, request: Request):
        """Ask for more time, within limits the app does not choose."""
        return desktops.extend_approval_for_session(request.state.app_session, request_id)

    @api.post("/app/approvals/{request_id}/abandon")
    def app_approval_abandon(request_id: str, request: Request):
        """The app has stopped waiting, so the question is withdrawn.

        Only ever removes authority. An app can withdraw its own question and
        nothing else, and a decision arriving afterwards resolves nothing —
        which is the point: a late click must not revive a write whose caller
        has already given up on it.
        """
        return desktops.abandon_approval_for_session(request.state.app_session, request_id)

    @api.get("/app/storage/snapshots")
    def list_app_snapshots(request: Request):
        with lifecycle.lock:
            return app_services.snapshots(request.state.app_session)

    @api.post("/app/storage/snapshots")
    def create_app_snapshot(request: Request):
        with lifecycle.lock:
            return app_services.snapshot(request.state.app_session)

    @api.post("/app/storage/snapshots/{snapshot_id}/restore")
    def restore_app_snapshot(snapshot_id: str, payload: RevisionRequest, request: Request):
        with lifecycle.lock:
            return app_services.restore(request.state.app_session, snapshot_id, payload.revision)

    @api.post("/apps/{app_id}/migration/preview")
    def preview_migration(app_id: str, payload: StorageWrite):
        with lifecycle.lock:
            return app_services.migrate(app_id, payload.value, payload.revision)

    @api.post("/apps/{app_id}/migration")
    def migrate_app(app_id: str, payload: StorageWrite):
        with lifecycle.lock:
            return app_services.migrate(app_id, payload.value, payload.revision, commit=True)

    return api
