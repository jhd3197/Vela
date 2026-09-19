"""The ordered list of routers `create_app` mounts, as data.

Follows ServerKit's `backend/app/core_blueprints.py` (MIT, same owner). ServerKit
curates its blueprints in one manifest rather than discovering them from the
filesystem; the same reasons apply here. The factory reads, and a structural
test can assert that every module under `vela/routers/` appears exactly once,
that each one loads, and that the served route count is the sum of what this
list produces.

Order is the mount order, and it is load-bearing at the bottom of the list:
FastAPI matches the first route that fits, so `web_serving` (`/apps/{id}/...`)
must come before `fallback`, which owns `/api/{unknown_path}` and the SPA's
catch-all. Everywhere above that the paths are disjoint and the order simply
records how `vela/api.py` used to read.

`needs` names the services each factory takes, by the factory's own parameter
names. `create_app` builds the services and hands each spec the ones it asked
for, so a router receives what it uses rather than reaching into `app.state`.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from fastapi import APIRouter


@dataclass(frozen=True, slots=True)
class RouterSpec:
    """One mounted router: where it lives, what it is called, what it needs."""

    module: str
    attribute: str
    prefix: str
    tags: tuple[str, ...]
    needs: tuple[str, ...]

    def load(self):
        """Import and return the factory this spec names."""
        factory = getattr(import_module(self.module), self.attribute)
        if not callable(factory):
            raise TypeError(f"{self.module}.{self.attribute} is not a router factory")
        return factory

    def build(self, services: dict[str, Any]) -> APIRouter:
        """Call the factory with the services it named, and check what it built."""
        missing = [name for name in self.needs if name not in services]
        if missing:
            raise KeyError(f"{self.module} asked for unknown services: {missing}")
        router = self.load()(**{name: services[name] for name in self.needs})
        if not isinstance(router, APIRouter):
            raise TypeError(f"{self.module}.{self.attribute} did not return an APIRouter")
        if router.prefix != self.prefix:
            raise ValueError(
                f"{self.module} mounts at {router.prefix!r}, registry says {self.prefix!r}"
            )
        return router

    @property
    def manifest_entry(self) -> tuple[str, str, str, tuple[str, ...]]:
        """The stable, serialisable form the structural tests compare."""
        return self.module, self.attribute, self.prefix, self.tags


#: Mount order. Keep new routers above `web_serving` unless they must not be.
ROUTERS: tuple[RouterSpec, ...] = (
    RouterSpec("vela.automations.api", "router", "/api/automations", ("automations",),
               ("automations",)),
    RouterSpec("vela.desktops.api", "router", "/api/desktops", ("desktops",),
               ("desktops", "runs")),
    RouterSpec("vela.routers.security", "router", "/api", ("security",),
               ("auth", "phone_access")),
    RouterSpec("vela.routers.catalog", "router", "/api", ("catalog",),
               ("catalog", "lifecycle")),
    RouterSpec("vela.routers.actions", "router", "/api", ("actions",),
               ("actions", "lifecycle")),
    RouterSpec("vela.routers.releases", "router", "/api", ("releases",),
               ("releases", "config")),
    RouterSpec("vela.routers.core", "router", "/api", ("core",),
               ("auth", "registry", "connected_apps", "config", "platform")),
    RouterSpec("vela.routers.app_session", "router", "/api", ("app-session",),
               ("app_services", "auth", "lifecycle", "desktops")),
    RouterSpec("vela.routers.widgets", "router", "/api", ("widgets",),
               ("widgets", "snooze")),
    RouterSpec("vela.routers.connections", "router", "/api", ("connections",),
               ("connections",)),
    RouterSpec("vela.routers.web_apps", "router", "/api", ("web-apps",),
               ("connected_apps", "config")),
    RouterSpec("vela.routers.managed", "router", "/api", ("managed-apps",),
               ("managed", "auth")),
    RouterSpec("vela.routers.apps", "router", "/api", ("apps",),
               ("registry", "connected_apps", "lifecycle", "usage", "snooze", "managed")),
    RouterSpec("vela.routers.desk", "router", "/api", ("desk",),
               ("desktops", "weather")),
    RouterSpec("vela.routers.files", "router", "/api", ("files",),
               ("files", "auth")),
    RouterSpec("vela.routers.themes", "router", "/api", ("themes",),
               ("themes", "settings")),
    RouterSpec("vela.routers.settings", "router", "/api", ("settings",),
               ("settings", "desktops", "config", "backups", "conversations",
                "agent_runs", "notifier", "themes")),
    RouterSpec("vela.routers.chat", "router", "/api", ("chat",),
               ("assistant", "bots", "conversations", "rooms", "settings")),
    RouterSpec("vela.routers.diagnostics", "router", "/api", ("diagnostics",),
               ("doctor", "updates", "errors", "support", "logs", "auth",
                "system_metrics", "usage")),
    RouterSpec("vela.routers.updates", "router", "/api", ("updates",),
               ("updates", "update_job", "config", "request_shutdown",
                "update_report_state")),
    RouterSpec("vela.routers.backups", "router", "/api", ("backups",),
               ("backups", "settings", "lifecycle", "auth", "agent_runs")),
    # /apps/{id}/... is served here and must be matched before the SPA below.
    RouterSpec("vela.webapps", "router", "", ("web-app-serving",),
               ("registry", "state")),
    # Last, always: this one owns `/api/{unknown_path}` and `/{full_path}`.
    RouterSpec("vela.routers.fallback", "router", "", ("fallback",),
               ("config",)),
)
