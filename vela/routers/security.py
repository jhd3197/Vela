"""App lock and Wi-Fi access.

Reauthentication for a session that is already signed in is enforced by the
middleware rather than by the page; these routes are what the page calls to
enrol, lock, unlock and time out. Wi-Fi access is managed from the Vela
computer only, which is checked here rather than assumed.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError


class QuickEnrollRequest(BaseModel):
    """Enable or change app lock. The secret is validated by `vela.access`."""
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    method: str = Field(pattern="^(pin|pattern)$")
    # A six-digit string, or the drawn dot order. Never stored or logged.
    secret: str | list[int] = Field(union_mode="left_to_right")


class QuickTimeoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    timeout: int


class QuickDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class QuickUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: str | list[int] | None = Field(default=None, union_mode="left_to_right")
    password: str | None = Field(default=None, max_length=256)


class PhoneAccessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    address: str = Field(min_length=1, max_length=64)
    password: str = Field(default='', max_length=256)


def router(auth, phone_access) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["security"])

    @api.get("/security")
    def security_status(request: Request):
        return auth.quick_status(request)

    @api.post("/security/enroll")
    def security_enroll(payload: QuickEnrollRequest, request: Request):
        return auth.enroll_quick(request, payload.password, payload.method, payload.secret)

    @api.patch("/security")
    def security_timeout(payload: QuickTimeoutRequest, request: Request):
        return auth.update_quick(request, payload.password, payload.timeout)

    @api.delete("/security")
    def security_disable(payload: QuickDisableRequest, request: Request):
        return auth.disable_quick(request, payload.password)

    @api.post("/security/lock")
    def security_lock(request: Request):
        return auth.lock_now(request)

    @api.post("/security/unlock")
    def security_unlock(payload: QuickUnlockRequest, request: Request):
        if (payload.secret is None) == (payload.password is None):
            raise AppServiceError(422, "Send either the unlock code or the Vela password")
        return auth.unlock(request, secret=payload.secret, password=payload.password)

    @api.post("/security/activity")
    def security_activity(request: Request):
        return auth.record_activity(request)

    @api.get('/phone-access')
    def phone_access_status():
        return phone_access.status()

    @api.post('/phone-access')
    async def enable_phone_access(payload: PhoneAccessRequest, request: Request):
        if auth.is_remote_request(request) or not auth.local_request(request):
            raise AppServiceError(403, 'Manage Wi-Fi access from the Vela computer')
        return await phone_access.start(payload.address, payload.password)

    @api.delete('/phone-access')
    async def disable_phone_access(request: Request):
        if auth.is_remote_request(request) or not auth.local_request(request):
            raise AppServiceError(403, 'Manage Wi-Fi access from the Vela computer')
        await phone_access.stop(disable=True)
        return phone_access.status()

    return api
