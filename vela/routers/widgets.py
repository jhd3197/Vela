"""Widget summaries apps publish, and the desk's snooze switch."""

from typing import Any

from fastapi import APIRouter, Body, Request

from ..errors_http import Unprocessable


def router(widgets, snooze) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["widgets"])

    @api.put("/app/widgets/{widget_id}")
    def publish_widget(widget_id: str, request: Request, payload: dict[str, Any] = Body(...)):
        # App session only: an app publishes for itself and nothing else.
        return widgets.publish(request.state.app_session, widget_id, payload.get("summary"))

    @api.get("/apps/{app_id}/widgets")
    def app_widgets(app_id: str) -> dict:
        return widgets.for_app(app_id)

    @api.get("/widgets")
    def all_widgets() -> dict:
        return widgets.all()

    @api.post("/widgets/{app_id}/{widget_id}/snooze")
    def snooze_widget(app_id: str, widget_id: str) -> dict:
        """Put one widget's attention flag aside for eight hours.

        The summary is untouched and the app is told nothing: this only stops
        the desk's Needs you list and the rail's dot from showing it until the
        time is up.
        """
        try:
            return snooze.snooze(app_id, widget_id)
        except ValueError as exc:
            raise Unprocessable(str(exc), code="widgets.snooze_rejected") from exc

    @api.delete("/widgets/{app_id}/{widget_id}/snooze")
    def wake_widget(app_id: str, widget_id: str) -> dict:
        return snooze.wake(app_id, widget_id)

    return api
