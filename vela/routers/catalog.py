"""The published app catalog this server knows about."""

from fastapi import APIRouter


def router(catalog, lifecycle) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["catalog"])

    @api.get('/catalog')
    def catalog_status():
        return catalog.status()

    @api.post('/catalog/refresh')
    def refresh_catalog():
        with lifecycle.lock:
            return catalog.refresh()

    return api
