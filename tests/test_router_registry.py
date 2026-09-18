"""The router registry is the mount table, and it is checked rather than trusted.

`vela/router_registry.py` is data: a test can read it, so a router that is added
to the tree and forgotten in the registry, or listed twice, or mounted at a
prefix it does not actually use, is a failing test rather than a route nobody
can reach.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""
import atexit
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-registry-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi.routing import _IncludedRouter

from vela.api import create_app
from vela.config import Config
from vela.router_registry import ROUTERS, RouterSpec

ROOT = Path(__file__).resolve().parent.parent
ROUTER_PACKAGE = ROOT / "vela" / "routers"

#: The only routes FastAPI adds for itself, outside the registry.
FASTAPI_OWN = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def mounted_routers(app):
    """The routers `include_router` added, in mount order.

    This FastAPI version keeps an included router nested behind an
    `_IncludedRouter` entry rather than flattening its routes into the app, so
    the registry's own list is reachable through `original_router`.
    """
    return [route.original_router for route in app.routes
            if isinstance(route, _IncludedRouter)]


def served_routes(container) -> set[tuple[str, str]]:
    """Every `(METHOD, path)` an app or a router serves, schema or not."""
    found = set()

    def walk(routes):
        for route in routes:
            if isinstance(route, _IncludedRouter):
                walk(route.original_router.routes)
            elif getattr(route, "methods", None) and hasattr(route, "path"):
                for method in route.methods:
                    found.add((method, route.path))
            elif hasattr(route, "routes"):
                walk(route.routes)
    walk(container.routes)
    return found


def _build_script():
    """The packaging script, loaded as a module so its own list can be read."""
    spec = importlib.util.spec_from_file_location(
        "vela_build_server", ROOT / "scripts" / "build-server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _disposable_app():
    directory = tempfile.TemporaryDirectory(prefix="vela-registry-")
    atexit.register(directory.cleanup)
    root = Path(directory.name)
    config = Config(root / "data", root / "apps", ROOT / "web/dist")
    config.ensure_dirs()
    return create_app(config)


class RegistryTests(unittest.TestCase):
    def test_every_router_module_is_listed_exactly_once(self):
        on_disk = {
            f"vela.routers.{path.stem}"
            for path in ROUTER_PACKAGE.glob("*.py")
            if path.stem != "__init__"
        }
        listed = [spec.module for spec in ROUTERS]
        self.assertEqual(sorted(listed), sorted(set(listed)), "a module is listed twice")
        missing = on_disk - set(listed)
        self.assertFalse(
            missing,
            f"these router modules exist but nothing mounts them: {sorted(missing)}",
        )

    def test_the_routers_outside_the_package_are_listed_too(self):
        """The two that predate `vela/routers/` are mounted from the same table."""
        listed = {spec.module for spec in ROUTERS}
        self.assertIn("vela.automations.api", listed)
        self.assertIn("vela.desktops.api", listed)
        self.assertIn("vela.webapps", listed)

    def test_the_download_carries_every_module_the_registry_imports(self):
        """A frozen build cannot follow `import_module`, so the build names them.

        The registry imports each router by string. PyInstaller follows real
        import statements, so before the build script read this list a packaged
        server started, reached `create_app`, and died with
        `ModuleNotFoundError: No module named 'vela.routers'` — a failure no
        test in a checkout can see, because a checkout imports from the tree.
        """
        build = _build_script()
        named = set()
        options = build.router_imports()
        for flag, value in zip(options[::2], options[1::2]):
            self.assertEqual(flag, "--hidden-import")
            named.add(value)
        missing = {spec.module for spec in ROUTERS} - named
        self.assertFalse(
            missing,
            f"the download would not carry these routers: {sorted(missing)}",
        )

    def test_every_spec_loads_a_factory(self):
        for spec in ROUTERS:
            with self.subTest(module=spec.module):
                self.assertTrue(callable(spec.load()))

    def test_no_two_specs_share_a_prefix_and_tag(self):
        pairs = [(spec.prefix, spec.tags) for spec in ROUTERS]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_manifest_entries_are_serialisable_and_stable(self):
        for spec in ROUTERS:
            module, attribute, prefix, tags = spec.manifest_entry
            self.assertIsInstance(module, str)
            self.assertIsInstance(attribute, str)
            self.assertIsInstance(prefix, str)
            self.assertIsInstance(tags, tuple)
            self.assertFalse(prefix.endswith("/"), f"{module} prefix ends with a slash")

    def test_the_fallback_router_is_last(self):
        """It owns `/api/{unknown_path}` and the SPA catch-all, so it must be."""
        self.assertEqual(ROUTERS[-1].module, "vela.routers.fallback")
        self.assertEqual(ROUTERS[-2].module, "vela.webapps")

    def test_a_spec_that_asks_for_an_unknown_service_is_refused(self):
        spec = RouterSpec("vela.routers.catalog", "router", "/api", ("catalog",),
                          ("catalog", "nonesuch"))
        with self.assertRaises(KeyError):
            spec.build({"catalog": object(), "lifecycle": object()})

    def test_a_spec_whose_prefix_disagrees_with_its_router_is_refused(self):
        spec = RouterSpec("vela.routers.catalog", "router", "/elsewhere", ("catalog",),
                          ("catalog", "lifecycle"))
        with self.assertRaises(ValueError):
            spec.build({"catalog": object(), "lifecycle": object()})


class ServedSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.app = _disposable_app()

    def test_the_app_mounts_one_router_per_spec_and_no_route_twice(self):
        mounted = mounted_routers(self.app)
        self.assertEqual(len(mounted), len(ROUTERS))
        own = {(method, path) for method, path in served_routes(self.app)
               if path in FASTAPI_OWN}
        from_registry = sum(len(served_routes(router)) for router in mounted)
        self.assertEqual(
            from_registry, len(served_routes(self.app)) - len(own),
            "a route is served by two routers, or the walk missed one",
        )

    def test_every_served_path_sits_under_a_prefix_the_registry_declares(self):
        prefixes = {spec.prefix for spec in ROUTERS}
        for method, path in served_routes(self.app):
            if path in FASTAPI_OWN:
                continue
            with self.subTest(route=f"{method} {path}"):
                self.assertTrue(
                    any(prefix and path.startswith(prefix + "/") for prefix in prefixes)
                    or "" in prefixes,
                    f"{path} is served from no registered prefix",
                )

    def test_nothing_is_mounted_that_no_spec_accounts_for(self):
        """Only FastAPI's own docs routes live outside the registry."""
        outside = {
            route.path for route in self.app.routes
            if not isinstance(route, _IncludedRouter)
            and getattr(route, "path", "").startswith("/")
        }
        self.assertEqual(outside, FASTAPI_OWN)

    def test_the_api_catch_all_is_reachable_only_after_the_real_routes(self):
        served = served_routes(self.app)
        self.assertIn(("GET", "/api/{unknown_path:path}"), served)
        self.assertIn(("GET", "/api/health"), served)


if __name__ == "__main__":
    unittest.main()
