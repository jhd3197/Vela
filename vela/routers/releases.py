"""Reviewing and committing one app release.

A release is prepared, reviewed and then committed as a separate step, so the
capabilities and operations a new version asks for are approved by a person
rather than adopted because the package said so.
"""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError
from ..package_files import MAX_BYTES


class ReleasePrepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    folder: str | None = Field(default=None, max_length=4096)
    app_id: str | None = Field(default=None, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    rollback: str | None = Field(default=None, pattern=r'^[a-f0-9-]{36}$')


class ReleaseApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capabilities: list[str]
    operations: list[str]


def router(releases, config) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["releases"])

    @api.post('/releases/prepare')
    def prepare_release(payload: ReleasePrepare):
        if bool(payload.folder) == bool(payload.app_id) or (payload.rollback and not payload.app_id):
            raise AppServiceError(422, 'Select a folder or catalog app, optionally a rollback for that app')
        return releases.prepare(**payload.model_dump())

    @api.post('/releases/upload')
    async def upload_release(request: Request):
        directory = config.data_dir / 'staging'
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(suffix='.zip', dir=directory)
        try:
            total = 0
            with os.fdopen(descriptor, 'wb') as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_BYTES: raise AppServiceError(413, 'Release archive exceeds 32 MiB')
                    output.write(chunk)
            return releases.prepare(archive=name)
        finally:
            Path(name).unlink(missing_ok=True)

    @api.post('/releases/{review}/commit')
    def commit_release(review: str, payload: ReleaseApproval):
        return releases.commit(review, payload.capabilities, payload.operations)

    @api.delete('/releases/{review}')
    def cancel_release(review: str):
        return releases.cancel(review)

    @api.get('/apps/{app_id}/releases')
    def release_history(app_id: str):
        return releases.history(app_id)

    return api
