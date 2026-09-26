"""Managing installed web apps: review, install, run, back up, update, remove.

These are the hub's own routes and they need a Vela session like every other
`/api` path. They are not how an app is *used*: that happens on the app's own
address, through `vela/managed/gateway.py`, and none of it is in this inventory
because none of it is Vela's API.

The handlers stay thin on purpose. Everything that decides anything -- what an
approval has to match, what order an update happens in, what a failed restore
leaves behind -- is in `vela/managed/service.py`, where it can be tested without
a request.
"""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors_http import InvalidRequest, Precondition
from ..managed.packages import MAX_ARTIFACT_BYTES

_APP_ID = r"^[a-z0-9]+(-[a-z0-9]+)*$"
_UUID = r"^[0-9a-f-]{36}$"


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str | None = Field(default=None, max_length=4096)


class InstallApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Echoed back from the review. Approval is bound to bytes, so an approval
    #: that names a different tree is refused rather than applied to this one.
    artifactDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    packageDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: The person agreeing that this program runs with their own permissions.
    trust: str = Field(max_length=32)
    startWithVela: bool = False


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, max_length=1024)
    start: bool = True


class StartupPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startWithVela: bool


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=200)


class RemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Data survives removal unless this says otherwise, and the dashboard asks
    #: separately before it can be true.
    eraseData: bool = False


class EraseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The exact app id, typed. A destructive action that a stray click or a
    #: replayed request can complete is not a deliberate one.
    confirm: str = Field(max_length=48)


def router(managed, auth) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["managed-apps"])

    def owner(request: Request) -> str | None:
        """Which Vela session is asking, so its gateway access ends with it."""
        return getattr(request.state, "hub_token", None)

    @api.get("/managed")
    def list_managed() -> dict:
        return {"apps": managed.list_apps(), "domains": managed.domains,
                "target": managed.target}

    @api.post("/managed/review")
    def review(payload: ReviewRequest) -> dict:
        if not payload.folder:
            raise InvalidRequest("Choose a package folder", code="managed.no_source")
        return managed.review(folder=payload.folder)

    @api.post("/managed/review/upload")
    async def review_upload(request: Request) -> dict:
        """Take a package archive from the browser and review what is in it."""
        directory = managed.config.data_dir / "staging"
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(suffix=".zip", dir=directory)
        try:
            total = 0
            with os.fdopen(descriptor, "wb") as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_ARTIFACT_BYTES:
                        raise InvalidRequest(
                            "That package is larger than Vela accepts",
                            code="managed.package_too_large", status=413,
                        )
                    output.write(chunk)
            return managed.review(archive=name)
        finally:
            Path(name).unlink(missing_ok=True)

    @api.delete("/managed/review/{review}")
    def cancel_review(review: str) -> dict:
        return managed.cancel_review(review)

    @api.post("/managed/review/{review}/install")
    def install(review: str, payload: InstallApproval) -> dict:
        return managed.commit(
            review,
            artifact_digest=payload.artifactDigest,
            package_digest=payload.packageDigest,
            trust=payload.trust,
            start_with_vela=payload.startWithVela,
        )

    @api.get("/managed/{app_id}")
    def describe(app_id: str) -> dict:
        return managed.describe(app_id)

    @api.get("/managed/{app_id}/status")
    def status(app_id: str) -> dict:
        return managed.status(app_id)

    @api.post("/managed/{app_id}/launch")
    def launch(app_id: str, payload: LaunchRequest, request: Request) -> dict:
        return managed.launch(app_id, owner=owner(request), path=payload.path,
                              start=payload.start)

    @api.post("/managed/{app_id}/start")
    def start(app_id: str) -> dict:
        return managed.start(app_id)

    @api.post("/managed/{app_id}/stop")
    def stop(app_id: str) -> dict:
        return managed.stop(app_id)

    @api.put("/managed/{app_id}/startup")
    def startup(app_id: str, payload: StartupPreference) -> dict:
        return managed.set_start_with_vela(app_id, payload.startWithVela)

    @api.post("/managed/{app_id}/snapshots")
    def snapshot(app_id: str, payload: SnapshotRequest) -> dict:
        return managed.snapshot(app_id, payload.note)

    @api.post("/managed/{app_id}/snapshots/{snapshot_id}/restore")
    def restore(app_id: str, snapshot_id: str) -> dict:
        return managed.restore(app_id, snapshot_id)

    @api.post("/managed/{app_id}/releases/{release_id}/rollback")
    def rollback(app_id: str, release_id: str) -> dict:
        return managed.rollback(app_id, release_id)

    @api.post("/managed/{app_id}/data/erase")
    def erase(app_id: str, payload: EraseRequest) -> dict:
        if payload.confirm != app_id:
            raise Precondition(
                "Type the app's name to erase its data.", code="managed.erase_unconfirmed"
            )
        return managed.erase_data(app_id)

    @api.delete("/managed/{app_id}")
    def remove(app_id: str, payload: RemoveRequest) -> dict:
        return managed.remove(app_id, erase_data=payload.eraseData)

    return api
