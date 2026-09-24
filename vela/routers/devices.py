"""Pairing the Vela app, and the devices it is paired on.

A signed-in session asks for a code and shows it as a link and QR code. The app
spends the code on `/api/devices/pair` and gets a device credential, then
spends the credential on `/api/devices/session` for an ordinary hub session.
Those two take no hub session, so the auth middleware lets them through for the
Wi-Fi address only; everything else here is a normal signed-in route.
"""

from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(default="", max_length=200)
    form: str = Field(default="phone", max_length=16)
    platform: str = Field(default="android", max_length=16)
    appVersion: str | None = Field(default=None, max_length=64)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


def router(auth, devices, phone_access, config) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["devices"])

    def address():
        """Where a device reaches this server, and how it checks it is this one."""
        if config.remote_access and config.public_origin:
            return {"origin": config.public_origin, "setup": None, "fingerprint": None}
        if phone_access.origin:
            setup = urlsplit(phone_access.setup_url)
            return {"origin": phone_access.origin, "setup": f"{setup.scheme}://{setup.netloc}",
                    "fingerprint": phone_access.fingerprint}
        return None

    @api.get("/devices")
    def list_devices():
        return {"devices": devices.list(), "available": address() is not None}

    @api.post("/devices/pairing")
    def start_pairing():
        where = address()
        if where is None:
            raise AppServiceError(409, "Turn on Wi-Fi access first, so the app has an address to connect to.")
        issued = devices.new_code()
        query = {"v": "1", "origin": where["origin"]}
        if where["fingerprint"]:
            query.update(setup=where["setup"], fp=where["fingerprint"])
        query["code"] = issued["code"]
        return {**issued, **where, "link": "vela://pair?" + urlencode(query)}

    @api.post("/devices/pair")
    def pair(payload: PairRequest, request: Request):
        peer = auth.throttle(request)
        device, credential = devices.pair(
            payload.code, name=payload.name, form=payload.form,
            platform=payload.platform, app_version=payload.appVersion)
        auth.forgive(peer)
        return {"device": device, "credential": credential}

    @api.post("/devices/session")
    def device_session(payload: SessionRequest, request: Request):
        device = devices.authenticate(request.headers.get("x-vela-device", ""))
        token = auth.start_session(device["id"])
        response = JSONResponse({"token": token, "expiresIn": auth.SESSION_SECONDS})
        response.set_cookie("__Host-vela-session", token, max_age=auth.SESSION_SECONDS,
                            secure=True, httponly=True, samesite="strict", path="/")
        return response

    @api.patch("/devices/{device_id}")
    def rename_device(device_id: str, payload: RenameRequest):
        return devices.rename(device_id, payload.name)

    @api.delete("/devices/{device_id}")
    def remove_device(device_id: str):
        devices.remove(device_id)
        auth.end_device_sessions(device_id)
        return {"devices": devices.list()}

    return api
