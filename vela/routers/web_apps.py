"""Web apps the user added by address rather than installed."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class ConnectedAppRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    color: str = Field(default='#9184d9', pattern=r'^#[0-9a-fA-F]{6}$')


class ConnectedAppUpdate(ConnectedAppRequest):
    revision: int = Field(ge=1)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


def router(connected_apps, config) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["web-apps"])

    @api.post('/web-apps', status_code=201)
    def add_web_app(payload: ConnectedAppRequest, request: Request):
        return connected_apps.save(**payload.model_dump(), hub_origin=config.public_origin or str(request.base_url))

    @api.put('/web-apps/{app_id}')
    def edit_web_app(app_id: str, payload: ConnectedAppUpdate, request: Request):
        return connected_apps.save(**payload.model_dump(), app_id=app_id, hub_origin=config.public_origin or str(request.base_url))

    @api.delete('/web-apps/{app_id}')
    def remove_web_app(app_id: str, payload: RevisionRequest):
        return connected_apps.remove(app_id, payload.revision)

    return api
