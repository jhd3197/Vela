"""The v3 manifest: what a managed-web-app package declares, and what it cannot.

The JSON Schema in `vela/assets/manifest-v3.schema.json` is the vendored copy of
the canonical one in `vela-contracts`, adopted through
`scripts/sync-runtime-assets.py`. It carries the shape. This module carries the
rules a schema cannot express: that a bundled archive really is inside the
package, that a data directory cannot climb out of the app's own storage, that
the placeholders in an argument vector are ones the host knows how to fill, and
that this host implements the service revision the package asks for.

An older Vela refuses a v3 manifest already: `validate_manifest` accepts only
schema versions 1 and 2 and says so. That is the fail-closed behaviour the
contract wants, and it is why this is a new version rather than a v2 addition.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from ..errors_http import Unprocessable

#: The `compatibility.managedService` revision this host implements. A package
#: that asks for a different one is refused rather than run with fields this
#: host would silently ignore.
HOST_MANAGED_SERVICE_REVISION = 1

#: Placeholders a package may use in `command.args` and `service.environment`.
#: Bounded on purpose: substitution is the only templating, there is no shell,
#: and a name outside this set is a manifest error rather than a literal.
SUBSTITUTIONS = ("port", "host", "dataDir", "codeDir", "appId", "publicUrl")

_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "assets/manifest-v3.schema.json").read_text(
        encoding="utf-8"
    )
)
_VALIDATOR = Draft202012Validator(_SCHEMA)
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z]+)\}")
_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: How this host names its own operating system and CPU in artifact terms.
_OS_NAMES = {"win32": "windows", "darwin": "macos", "linux": "linux"}
_ARCH_NAMES = {
    "amd64": "x64",
    "x86_64": "x64",
    "x64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "armv7l": "armv7",
    "armv7": "armv7",
    "armv8l": "armv7",
}


class ManagedManifestError(Unprocessable, ValueError):
    """A managed-service package that cannot be accepted, and the reason.

    422 rather than 500: an invalid package is the package author's to fix, and
    the message is written for whoever is looking at the install review.
    """

    code = "managed.manifest_invalid"


def host_target() -> tuple[str | None, str | None]:
    """This computer as an artifact target: `(os, arch)`, either possibly None.

    Deliberately two values rather than one `posix`. Choosing a binary by
    operating system alone is how an arm64 host ends up running an x64 build,
    and the failure arrives as an unreadable exec error rather than a refusal
    before anything ran.
    """
    system = _OS_NAMES.get(sys.platform)
    machine = platform.machine().lower()
    return system, _ARCH_NAMES.get(machine)


def describe_target(target: tuple[str | None, str | None]) -> str:
    """`windows x64`, or an honest `unknown` for a machine we cannot name."""
    return " ".join(part or "unknown" for part in target)


@dataclass(frozen=True, slots=True)
class Artifact:
    """One release archive, for exactly one operating system and architecture."""

    os: str
    arch: str
    format: str
    sha256: str
    size: int
    executable: str
    file: str | None = None
    url: str | None = None
    expanded_size_limit: int | None = None

    @property
    def target(self) -> tuple[str, str]:
        return self.os, self.arch

    @property
    def label(self) -> str:
        return f"{self.os} {self.arch}"

    @property
    def bundled(self) -> bool:
        """Whether the archive travels inside the package rather than being fetched."""
        return self.file is not None

    def summary(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "arch": self.arch,
            "format": self.format,
            "sha256": self.sha256,
            "size": self.size,
            "executable": self.executable,
            "delivery": "bundled" if self.bundled else "download",
            "url": self.url,
        }


@dataclass(frozen=True)
class ManagedManifest:
    """A validated v3 manifest, with the package directory it was read from."""

    id: str
    name: str
    version: str
    description: str
    category: str
    author: str
    icon: str | None
    color: str | None
    artifacts: tuple[Artifact, ...]
    raw: dict[str, Any] = field(repr=False, compare=False)
    path: Path = field(repr=False, compare=False)

    schema_version = 3

    # ------------------------------------------------------------- sections --

    @property
    def service(self) -> dict[str, Any]:
        return self.raw["service"]

    @property
    def source(self) -> dict[str, Any]:
        return self.raw["source"]

    @property
    def view(self) -> dict[str, Any]:
        view = dict(self.raw["view"])
        view.setdefault("chrome", "compact")
        view.setdefault("appearance", "auto")
        view.setdefault("embedding", "auto")
        return view

    @property
    def endpoint(self) -> dict[str, Any]:
        endpoint = dict(self.service["endpoint"])
        endpoint.setdefault("bind", "127.0.0.1")
        endpoint.setdefault("basePath", "/")
        endpoint.setdefault("websocket", "unsupported")
        return endpoint

    @property
    def readiness(self) -> dict[str, Any]:
        readiness = dict(self.service["readiness"])
        readiness.setdefault("expectStatus", [200])
        readiness.setdefault("startTimeoutSeconds", 60)
        readiness.setdefault("intervalSeconds", 0.5)
        return readiness

    @property
    def lifetime(self) -> dict[str, Any]:
        lifetime = dict(self.service.get("lifetime") or {})
        lifetime.setdefault("startWithVela", False)
        lifetime.setdefault("stopTimeoutSeconds", 15)
        restart = dict(lifetime.get("restart") or {})
        restart.setdefault("maxRetries", 3)
        restart.setdefault("windowSeconds", 300)
        restart.setdefault("backoffSeconds", 2)
        lifetime["restart"] = restart
        return lifetime

    @property
    def data(self) -> dict[str, Any]:
        data = dict(self.service["data"])
        data.setdefault("backup", "stop-and-copy")
        return data

    @property
    def command_args(self) -> list[str]:
        return list(self.service["command"]["args"])

    @property
    def working_directory(self) -> str:
        return self.service["command"].get("workingDirectory", "code")

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.service.get("environment") or {})

    # ------------------------------------------------------------- selection --

    def artifact_for(self, target: tuple[str | None, str | None]) -> Artifact | None:
        """The one artifact matching this OS *and* architecture, or None."""
        for artifact in self.artifacts:
            if artifact.target == target:
                return artifact
        return None

    def supports(self, target: tuple[str | None, str | None]) -> bool:
        return self.artifact_for(target) is not None

    @property
    def supported_targets(self) -> list[str]:
        return [artifact.label for artifact in self.artifacts]

    # --------------------------------------------------------------- review --

    def review(self, target: tuple[str | None, str | None]) -> dict[str, Any]:
        """What an install review shows before anything is downloaded or run."""
        artifact = self.artifact_for(target)
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "category": self.category,
            "author": self.author,
            "color": self.color,
            "schemaVersion": 3,
            "profile": "managed-web",
            "source": dict(self.source),
            "trust": {
                "execution": "trusted-native",
                # Said plainly, in the review, because a checkbox list of SDK
                # capabilities would imply a confinement that does not exist.
                "summary": (
                    f"{self.name} runs as a program on this computer with your own "
                    "permissions. It can read and write the files you can, and reach "
                    "the network you can. Vela limits what a browser can reach, not "
                    "what this program can do."
                ),
                "grants": [],
            },
            "integration": {"sdk": False, "agent": False, "storage": False},
            "supportedTargets": self.supported_targets,
            "target": describe_target(target),
            "artifact": artifact.summary() if artifact else None,
            "supported": artifact is not None,
            "command": {
                "args": self.command_args,
                "workingDirectory": self.working_directory,
                "environment": sorted(self.environment),
            },
            "endpoint": self.endpoint,
            "readiness": self.readiness,
            "lifetime": self.lifetime,
            "data": self.data,
            "view": self.view,
        }


def _relative(value: str, what: str) -> str:
    """A package-relative POSIX path, or a refusal naming what was wrong.

    The schema already rejects the obvious shapes. This repeats the check
    against the parsed parts, because the value is about to be joined onto a
    real directory and a pattern is a weaker promise than a walk.
    """
    if not isinstance(value, str) or not value:
        raise ManagedManifestError(f"{what} must be a relative path inside the package")
    if value.startswith("/") or "\\" in value or ":" in value:
        raise ManagedManifestError(f"{what} must be a relative path inside the package: {value!r}")
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ManagedManifestError(f"{what} must not climb out of the package: {value!r}")
    return "/".join(parts)


def _check_placeholders(value: str, what: str) -> None:
    unknown = sorted(set(_PLACEHOLDER_RE.findall(value)) - set(SUBSTITUTIONS))
    if unknown:
        raise ManagedManifestError(
            f"{what} uses {', '.join('{' + name + '}' for name in unknown)}, which this host "
            f"cannot fill. Known values are {', '.join('{' + name + '}' for name in SUBSTITUTIONS)}."
        )


def validate_managed_manifest(data: Any, *, folder: str, path: Path) -> ManagedManifest:
    """Validate a v3 manifest and return it, or raise `ManagedManifestError`."""
    if not isinstance(data, dict):
        raise ManagedManifestError(f"{folder}: app.json must be a JSON object")
    if data.get("schemaVersion") != 3:
        raise ManagedManifestError(
            f"{folder}: this is not a managed web app package "
            f"(schemaVersion {data.get('schemaVersion')!r})"
        )
    errors = sorted(_VALIDATOR.iter_errors(data), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.absolute_path) or "manifest"
        raise ManagedManifestError(f"{folder}: {where}: {first.message}")

    # The id names the app, not the folder it arrived in. A v1/v2 package is
    # installed into a directory named after its folder, so the two have to
    # agree there; a managed app's storage is created from the id itself, and
    # requiring an author to name a zip's inner folder correctly would be a rule
    # with nothing behind it.
    app_id = data["id"]
    if not _ID_RE.match(app_id):
        raise ManagedManifestError(f"{folder}: id must be a lowercase slug, got {app_id!r}")

    revision = data["compatibility"]["managedService"]
    if revision != HOST_MANAGED_SERVICE_REVISION:
        raise ManagedManifestError(
            f"{folder}: this package needs managed-service revision {revision}; "
            f"this Vela implements {HOST_MANAGED_SERVICE_REVISION}"
        )

    service = data["service"]
    artifacts: list[Artifact] = []
    seen: set[tuple[str, str]] = set()
    for entry in service["artifacts"]:
        target = (entry["os"], entry["arch"])
        if target in seen:
            raise ManagedManifestError(
                f"{folder}: two artifacts claim {entry['os']} {entry['arch']}; "
                "a target must select exactly one archive"
            )
        seen.add(target)
        executable = _relative(entry["executable"], f"artifact {entry['os']} {entry['arch']} executable")
        file = entry.get("file")
        if file is not None:
            file = _relative(file, f"artifact {entry['os']} {entry['arch']} file")
        artifacts.append(
            Artifact(
                os=entry["os"],
                arch=entry["arch"],
                format=entry["format"],
                sha256=entry["sha256"],
                size=entry["size"],
                executable=executable,
                file=file,
                url=entry.get("url"),
                expanded_size_limit=entry.get("expandedSizeLimit"),
            )
        )

    for index, argument in enumerate(service["command"]["args"]):
        _check_placeholders(argument, f"command argument {index + 1}")
    for name, value in (service.get("environment") or {}).items():
        _check_placeholders(value, f"environment value {name}")

    _relative(service["data"]["directory"], "service.data.directory")
    if data.get("icon"):
        _relative(data["icon"], "icon")
    if data["source"].get("licenseFile"):
        _relative(data["source"]["licenseFile"], "source.licenseFile")

    return ManagedManifest(
        id=app_id,
        name=data["name"],
        version=data["version"],
        description=data["description"],
        category=data["category"],
        author=data["author"],
        icon=data.get("icon"),
        color=data.get("color"),
        artifacts=tuple(artifacts),
        raw=data,
        path=path,
    )


def is_managed_package(folder: Path) -> bool:
    """Whether this directory holds a v3 manifest, without validating the rest."""
    manifest = Path(folder) / "app.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("schemaVersion") == 3
    except (OSError, ValueError, AttributeError):
        return False


def load_managed_manifest(folder: Path) -> ManagedManifest:
    """Read and validate `app.json` from a staged or installed package folder."""
    folder = Path(folder)
    manifest = folder / "app.json"
    if not manifest.is_file():
        raise ManagedManifestError(f"{folder.name}: missing app.json")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedManifestError(f"{folder.name}: invalid JSON in app.json: {exc}") from exc
    return validate_managed_manifest(data, folder=folder.name, path=folder)
