"""Typed errors, rendered as one body at one boundary.

Follows ServerKit's `backend/app/exceptions.py` (MIT, same owner). ServerKit
raises these out of Flask services and lets its error handler own `jsonify`;
here the same idea meets FastAPI. A service says *what* went wrong and the app
factory's single handler decides how that reaches the wire, so no module below
the HTTP boundary has to import FastAPI to refuse a request.

The body is always:

    {"detail": "...", "code": "group.reason", "status": 404}

plus `"details": {...}` when a raise site has structured context to add. The
`detail` string is the one a caller already sees; `code` and `status` are
additive. Anything reading `detail` — the dashboard, an app's SDK — keeps
working without knowing this module exists.

Subclassing a built-in (`ValueError`, `LookupError`) is a deliberate
compatibility bridge, the same one ServerKit uses: a service that already
catches `ValueError` keeps catching a typed error that is also one, so a
migration does not have to land everywhere at once.

Codes are lowercase and dotted, `group.reason`, and belong to the raise site.
They are assigned from what the code does, never guessed from the message.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class VelaError(Exception):
    """An expected failure a caller can act on, with the status to answer."""

    #: The HTTP status this class answers with unless a raise site overrides it.
    status: int = 500
    #: `group.reason`. Overridden per class, or per raise site with `code=`.
    code: str = "server.error"
    #: Used when a raise site has nothing more specific to say.
    default_detail: str = "Something went wrong."

    def __init__(
        self,
        detail: Any = None,
        *,
        code: str | None = None,
        status: int | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.detail = str(detail) if detail is not None else type(self).default_detail
        self.code = code or type(self).code
        self.status = int(status) if status is not None else type(self).status
        self.details = dict(details) if isinstance(details, Mapping) else None
        #: Response headers this failure carries, for the few that mean
        #: something to the caller (a desk conflict names the revision to
        #: reload). Empty for almost every error.
        self.headers = dict(headers) if headers else None
        super().__init__(self.detail)

    def to_body(self) -> dict[str, Any]:
        """The public error body. `detail` first, because that is the contract."""
        body: dict[str, Any] = {
            "detail": self.detail,
            "code": self.code,
            "status": self.status,
        }
        if self.details:
            body["details"] = self.details
        return body

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{type(self).__name__}({self.status}, {self.code!r}, {self.detail!r})"


class InvalidRequest(VelaError, ValueError):
    """400. The request is malformed in a way the caller can fix."""

    status = 400
    code = "request.invalid"
    default_detail = "That request could not be understood."


class AuthRequired(VelaError):
    """401. Nobody is signed in."""

    status = 401
    code = "auth.required"
    default_detail = "Sign in to continue."


class Forbidden(VelaError, PermissionError):
    """403. Signed in, and still not allowed to do this."""

    status = 403
    code = "auth.forbidden"
    default_detail = "That is not allowed."


class NotFound(VelaError, LookupError):
    """404. Nothing here by that name."""

    status = 404
    code = "resource.not_found"
    default_detail = "Not found."


class NotAllowed(VelaError):
    """405. The path exists; this method does not belong to it."""

    status = 405
    code = "request.method_not_allowed"
    default_detail = "That method is not allowed here."


class Conflict(VelaError):
    """409. The request is well formed and the current state refuses it."""

    status = 409
    code = "resource.conflict"
    default_detail = "That conflicts with the current state."


class TooLarge(VelaError):
    """413. More bytes than this endpoint accepts."""

    status = 413
    code = "request.too_large"
    default_detail = "That is too large."


class Unprocessable(VelaError, ValueError):
    """422. Understood, and the values in it do not make sense."""

    status = 422
    code = "request.unprocessable"
    default_detail = "That request could not be processed."


class Locked(VelaError):
    """423. The thing exists and is held by something else."""

    status = 423
    code = "resource.locked"
    default_detail = "That is locked."


class TooEarly(VelaError):
    """425. Not yet; ask again."""

    status = 425
    code = "request.too_early"
    default_detail = "That is not ready yet."


class Precondition(VelaError):
    """428. A deliberate confirmation is missing.

    Used by the routes that destroy or replace something, where a stray link or
    a retry must not be enough on its own.
    """

    status = 428
    code = "request.precondition_required"
    default_detail = "Confirm this first."


class Upstream(VelaError):
    """502. Something Vela asked on the caller's behalf did not answer."""

    status = 502
    code = "upstream.failed"
    default_detail = "The service Vela asked did not answer."


class Unavailable(VelaError):
    """503. Vela cannot serve this right now, and it is not the caller's fault."""

    status = 503
    code = "service.unavailable"
    default_detail = "That is not available right now."


#: Every subclass, for the tests that walk the set.
SUBCLASSES: tuple[type[VelaError], ...] = (
    InvalidRequest,
    AuthRequired,
    Forbidden,
    NotFound,
    NotAllowed,
    Conflict,
    TooLarge,
    Unprocessable,
    Locked,
    TooEarly,
    Precondition,
    Upstream,
    Unavailable,
)
