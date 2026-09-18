"""Binding an app to a service it talks to, and invoking one operation."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=256)


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


def router(connections) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["connections"])

    @api.get("/apps/{app_id}/connection")
    def connection_status(app_id: str):
        return connections.status(app_id)

    @api.put("/apps/{app_id}/connection")
    async def bind_connection(app_id: str, payload: ConnectionRequest):
        return await connections.bind(app_id, payload.endpoint)

    @api.delete("/apps/{app_id}/connection")
    def disconnect_connection(app_id: str):
        return connections.disconnect(app_id)

    @api.get("/app/connection")
    def app_connection(request: Request):
        session = request.state.app_session
        if "connections" not in session["capabilities"]: raise AppServiceError(403, "Connections capability was not granted")
        connections._binding(session["installationId"])
        return connections.status(session["app_id"])

    @api.post("/app/connection/invoke")
    async def invoke_connection(payload: OperationRequest, request: Request):
        return await connections.invoke(request.state.app_session, payload.operation, payload.payload)

    return api
