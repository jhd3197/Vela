"""Companion apps: what is running here, connecting one, and its actions.

Listing, opening, starting and removing a connected companion go through the
same `/api/apps/{id}` routes every other app uses. What is here is the part
only a companion has: the ones found on this computer and not yet connected,
the owner's review, and pressing one of its buttons.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The fingerprint the owner was shown. Connecting refuses anything else,
    #: so what is trusted is exactly what was reviewed.
    fingerprint: str = Field(min_length=64, max_length=64)


def router(companions) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["companions"])

    @api.get("/companions")
    def list_companions() -> dict:
        return {"found": companions.found(), "connected": companions.list_apps()}

    @api.post("/companions/found/{companion_id}/connect")
    def connect_companion(companion_id: str, payload: Review) -> dict:
        return companions.connect(companion_id, payload.fingerprint)

    @api.post("/companions/{app_id}/review")
    def review_companion(app_id: str, payload: Review) -> dict:
        return companions.review(app_id, payload.fingerprint)

    @api.post("/companions/{app_id}/actions/{action_id}")
    def run_companion_action(app_id: str, action_id: str) -> dict:
        return companions.run_action(app_id, action_id)

    @api.post("/companions/{app_id}/refresh")
    def refresh_companion(app_id: str) -> dict:
        companions.refresh(app_id)
        return companions.get(app_id)

    return api
