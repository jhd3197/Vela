"""HTTP surface for automations.

Everything here is hub-authenticated through the existing middleware, so an app
iframe's session can never manage automations. The one exception is the webhook
ingress, which authenticates with its own per-workflow secret and grants nothing
beyond starting that workflow.
"""
from typing import Any

from fastapi import APIRouter, Body, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError
from . import validate

#: Path prefix the authentication middleware treats as secret-authenticated.
WEBHOOK_PREFIX = '/api/automations/hooks/'


class CreateAutomation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)


class SaveDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    document: dict
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class RenameAutomation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=500)


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    action: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    allow: bool
    requestContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    targetContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class UseBlueprint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=120)


class StartRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: dict | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: bool
    comment: str = Field(default='', max_length=500)


def router(automations) -> APIRouter:
    api = APIRouter(prefix='/api/automations', tags=['automations'])

    def _document(workflow_id):
        workflow = automations.store.get_workflow(workflow_id)
        return automations.store.get_revision(workflow_id, workflow['draft_revision'])['document']

    # Static paths are declared before "/{workflow_id}" so they are not shadowed.

    @api.get('')
    def list_automations(archived: bool = False):
        return automations.summaries(include_archived=archived)

    @api.post('', status_code=201)
    def create_automation(payload: CreateAutomation):
        return automations.create(payload.name)

    @api.get('/catalog')
    def catalog():
        return automations.catalog()

    @api.get('/status')
    def status():
        return automations.status()

    @api.get('/blueprints')
    def blueprints():
        return automations.blueprints()

    @api.post('/blueprints/{blueprint_id}', status_code=201)
    def use_blueprint(blueprint_id: str, payload: UseBlueprint | None = None):
        return automations.create_from_blueprint(blueprint_id, payload.name if payload else None)

    @api.get('/runs')
    def list_runs(workflowId: str | None = None, limit: int = 50, before: str | None = None):
        return automations.runs(workflowId, limit, before)

    @api.get('/runs/{run_id}')
    def run_detail(run_id: str):
        return automations.run_detail(run_id)

    @api.get('/runs/{run_id}/events')
    def run_events(run_id: str, after: int = 0):
        return automations.run_events(run_id, after)

    @api.post('/runs/{run_id}/cancel')
    def cancel_run(run_id: str):
        return automations.cancel_run(run_id)

    @api.get('/approvals')
    def approvals():
        return automations.pending_approvals()

    @api.post('/runs/{run_id}/approvals/{gate_key}')
    def decide(run_id: str, gate_key: str, payload: ApprovalDecision):
        return automations.decide(run_id, gate_key, payload.approved, payload.comment)

    @api.post('/import', status_code=201)
    def import_automation(payload: dict[str, Any] = Body(...)):
        return automations.import_document(payload)

    @api.post(WEBHOOK_PREFIX.removeprefix('/api/automations') + '{token_id}')
    async def receive_hook(token_id: str, request: Request,
                           x_vela_automation_secret: str = Header(default='')):
        body = await request.body()
        return automations.receive_webhook(token_id, x_vela_automation_secret, body,
                                           dict(request.query_params))

    @api.get('/{workflow_id}')
    def detail(workflow_id: str):
        return automations.detail(workflow_id)

    @api.put('/{workflow_id}')
    def save(workflow_id: str, payload: SaveDocument):
        try:
            return automations.save(workflow_id, payload.revision, payload.document,
                                    payload.name, payload.description)
        except validate.DocumentError as exc:
            raise AppServiceError(422, exc.detail) from exc

    @api.patch('/{workflow_id}')
    def rename(workflow_id: str, payload: RenameAutomation):
        return automations.rename(workflow_id, payload.name, payload.description)

    @api.delete('/{workflow_id}', status_code=204)
    def delete(workflow_id: str):
        automations.delete(workflow_id)
        return Response(status_code=204)

    @api.post('/{workflow_id}/duplicate', status_code=201)
    def duplicate(workflow_id: str):
        return automations.duplicate(workflow_id)

    @api.post('/{workflow_id}/archive')
    def archive(workflow_id: str):
        return automations.set_archived(workflow_id, True)

    @api.post('/{workflow_id}/restore')
    def restore(workflow_id: str):
        return automations.set_archived(workflow_id, False)

    @api.post('/{workflow_id}/activate')
    def activate(workflow_id: str):
        return automations.activate(workflow_id)

    @api.post('/{workflow_id}/pause')
    def pause(workflow_id: str):
        return automations.pause(workflow_id)

    @api.get('/{workflow_id}/export')
    def export(workflow_id: str):
        return automations.export(workflow_id)

    @api.put('/{workflow_id}/grants')
    def set_grant(workflow_id: str, payload: GrantRequest):
        automations.effects.grant(workflow_id, _document(workflow_id), payload.app, payload.action,
                                  payload.allow, payload.requestContract, payload.targetContract)
        return automations.detail(workflow_id)

    @api.post('/{workflow_id}/runs', status_code=202)
    def start_run(workflow_id: str, payload: StartRun | None = None):
        return automations.run_now(workflow_id, payload.input if payload else None)

    @api.post('/{workflow_id}/webhook')
    def rotate_webhook(workflow_id: str):
        return automations.rotate_webhook(workflow_id)

    return api
