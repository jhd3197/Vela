"""Named actions one app offers another, and the grants that allow them.

An app calls through its own session, so the caller is the session rather than
anything in the payload; the owner grants and reviews from the `/api/apps`
half of the same surface.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class ActionGrant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    action: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    allow: bool
    sourceContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    targetContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ActionCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    action: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    input: dict
    key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


def router(actions, lifecycle) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["actions"])

    @api.get('/apps/{app_id}/actions')
    def action_status(app_id: str):
        with lifecycle.lock: return actions.status(app_id)

    @api.put('/apps/{app_id}/actions/grant')
    def action_grant(app_id: str, payload: ActionGrant):
        return actions.grant(app_id, payload.app, payload.action, payload.allow, payload.sourceContract, payload.targetContract)

    @api.get('/apps/{app_id}/actions/history')
    def action_history(app_id: str):
        return actions.history(app_id)

    @api.get('/app/actions')
    def own_actions(request: Request):
        with lifecycle.lock: return actions.status(request.state.app_session['app_id'])

    @api.post('/app/actions/invoke')
    def invoke_action(request: Request, payload: ActionCall):
        return actions.invoke(request.state.app_session, payload.app, payload.action, payload.input, payload.key)

    return api
