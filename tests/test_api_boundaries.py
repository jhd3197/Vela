"""Structural guards on where the HTTP boundary is allowed to be.

Follows ServerKit's `backend/tests/test_api_controller_boundaries.py` (MIT, same
owner). ServerKit keeps Flask out of its services; the same rule, with FastAPI,
is what keeps a Vela service reusable from a scheduler, a worker or a test
without a request in hand.

These read the source rather than running it. A boundary that holds only when
somebody remembers is not a boundary; each of these fails on a tree where the
rule has slipped, which is the whole point of having them in the suite.

Run with: python -m unittest discover -s tests. Reads files only.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "vela"

#: Modules allowed to import FastAPI, with the reason each one is here.
HTTP_BOUNDARY = {
    "vela/api.py": "the app factory: builds the app and owns the error handlers",
    "vela/router_registry.py": "checks that a factory returned an APIRouter",
    "vela/auth.py": "the authentication middleware answers requests itself",
    "vela/phone_access.py": "wraps the ASGI app to serve the Wi-Fi access listener",
    "vela/webapps.py": "the router that serves /apps/{id}/...",
    "vela/automations/api.py": "a router",
    "vela/desktops/api.py": "a router",
}


def modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def imports_fastapi(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {
            "fastapi", "starlette"
        }:
            return True
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] in {"fastapi", "starlette"} for a in node.names
        ):
            return True
    return False


class BoundaryTests(unittest.TestCase):
    def test_only_the_boundary_imports_fastapi(self):
        offenders = {
            name for name, tree in modules()
            if imports_fastapi(tree)
            and name not in HTTP_BOUNDARY
            and not name.startswith("vela/routers/")
        }
        self.assertFalse(
            offenders,
            "these are service modules and must not import FastAPI — move the "
            f"HTTP part into a router and raise a vela.errors_http error: {sorted(offenders)}",
        )

    def test_the_exemptions_all_still_exist_and_still_need_it(self):
        """An exemption nobody uses is a rule that quietly stopped applying."""
        for name in HTTP_BOUNDARY:
            with self.subTest(module=name):
                path = ROOT / name
                self.assertTrue(path.is_file(), f"{name} is exempted and does not exist")
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    imports_fastapi(tree),
                    f"{name} no longer imports FastAPI; drop its exemption",
                )

    def test_no_http_exception_anywhere_in_the_engine(self):
        offenders = []
        for name, tree in modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "HTTPException":
                    offenders.append(name)
                    break
                if isinstance(node, ast.Attribute) and node.attr == "HTTPException":
                    offenders.append(name)
                    break
        self.assertFalse(
            sorted(set(offenders)),
            "raise a vela.errors_http subclass instead; the one handler in "
            f"create_app renders it: {sorted(set(offenders))}",
        )

    def test_routes_live_in_routers_and_not_in_the_factory(self):
        factory = ast.parse((ROOT / "vela/api.py").read_text(encoding="utf-8"))
        methods = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
        for node in ast.walk(factory):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in methods
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "app"):
                self.fail(f"vela/api.py defines a route at line {node.lineno}; "
                          "it belongs in a module under vela/routers/")


class ErrorCodeTests(unittest.TestCase):
    """Every typed error raised in the engine says which failure it is."""

    #: Classes that take `(status, detail)` and carry a class-level code.
    POSITIONAL = {"AppServiceError", "LifecycleError", "DesktopError", "DesktopConflict",
                  "FileError", "WallpaperError", "WeatherError", "ArtifactError",
                  "WidgetError"}
    #: Subclasses of the base whose own class attribute is the code.
    OWN_CODE = {"DeskError", "LogError", "UpdateError", "ManifestError", "NotifyError",
                "NotifyConfigError", "NotifyAuthError", "NotifyUnreachableError",
                "NotifyRateLimitError", "NotifyRejectedError"}
    #: The generic subclasses: a raise site must name a code for these.
    GENERIC = {"VelaError", "InvalidRequest", "AuthRequired", "Forbidden", "NotFound",
               "NotAllowed", "Conflict", "TooLarge", "Unprocessable", "Locked",
               "TooEarly", "Precondition", "Upstream", "Unavailable"}

    def test_every_generic_raise_carries_a_code(self):
        missing = []
        for name, tree in modules():
            if name == "vela/errors_http.py":
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                    continue
                func = node.exc.func
                cls = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if cls not in self.GENERIC:
                    continue
                if not any(kw.arg == "code" for kw in node.exc.keywords):
                    missing.append(f"{name}:{node.lineno} {cls}")
        self.assertFalse(
            missing,
            "a generic error says only its status; pass code=\"group.reason\" so "
            f"the body names the failure: {missing}",
        )

    def test_the_three_groups_do_not_overlap(self):
        self.assertFalse(self.POSITIONAL & self.OWN_CODE)
        self.assertFalse(self.POSITIONAL & self.GENERIC)
        self.assertFalse(self.OWN_CODE & self.GENERIC)

    def test_the_generic_set_is_the_module_s_own_list(self):
        from vela.errors_http import SUBCLASSES, VelaError
        self.assertEqual(
            self.GENERIC,
            {cls.__name__ for cls in SUBCLASSES} | {VelaError.__name__},
        )


if __name__ == "__main__":
    unittest.main()
