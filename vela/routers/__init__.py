"""One module per group of routes.

Each module exposes `router(...) -> APIRouter`, a factory that takes the
services its routes use and returns the mounted router. Nothing here reaches
into `app.state`: what a route needs arrives as an argument, which is what
makes a router testable on its own and its dependencies readable from the
`vela/router_registry.py` entry that mounts it.

A route body translates transport and nothing else — parse, call a service,
shape the response. A refusal is a `vela.errors_http` subclass, never a
FastAPI `HTTPException`; `tests/test_api_boundaries.py` keeps it that way.

The automations and desktops routers predate this package and stay in their
own subpackages. They are listed in the same registry.
"""
