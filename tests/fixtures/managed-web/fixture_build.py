"""Build a real managed-app package around the fixture service, for one machine.

A managed app package is a small Vela package -- `app.json`, an icon, the
upstream licence -- plus one release archive per operating system and CPU. The
fixture's "release" is the service script beside a launcher this computer can
execute, zipped and pinned by digest exactly as a real one is. It is built for
the machine running the test and thrown away afterwards, which is why the
launcher bakes in the interpreter currently running: a fixture is allowed to
know where it is, and a shipped package is not.

Nothing here is imported by the engine. Tests call `build_package` and get a
directory they can review and install, or `build_archive` for the upload path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = HERE / "service.py"
APP_ID = "fixture-notes"

WINDOWS_LAUNCHER = '@echo off\r\n"{python}" "%~dp0service.py" %*\r\n'
POSIX_LAUNCHER = '#!/bin/sh\nexec "{python}" "$(dirname "$0")/service.py" "$@"\n'


def host_target() -> tuple[str, str]:
    """The `(os, arch)` pair the engine will look for, for this machine."""
    from vela.managed.contract import host_target as engine_target

    system, arch = engine_target()
    if system is None or arch is None:  # pragma: no cover - unsupported CI machine
        raise RuntimeError("this computer has no managed-app target")
    return system, arch


def _launcher_name(system: str) -> str:
    return "run.cmd" if system == "windows" else "run.sh"


def build_artifact(target_dir: Path, *, service: Path | None = None) -> Path:
    """Write the release archive this computer can run, and return its path."""
    system, _arch = host_target()
    target_dir.mkdir(parents=True, exist_ok=True)
    staging = target_dir / "_artifact"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copyfile(service or SERVICE, staging / "service.py")
    name = _launcher_name(system)
    template = WINDOWS_LAUNCHER if system == "windows" else POSIX_LAUNCHER
    launcher = staging / name
    launcher.write_text(template.format(python=sys.executable), encoding="utf-8")
    archive = target_dir / "fixture-release.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for item in sorted(staging.iterdir()):
            info = zipfile.ZipInfo(item.name)
            mode = stat.S_IFREG | 0o755 if item.name == name else stat.S_IFREG | 0o644
            info.external_attr = mode << 16
            zipped.writestr(info, item.read_bytes())
    shutil.rmtree(staging, ignore_errors=True)
    return archive


def manifest(
    *,
    version: str = "1.0.0",
    archive: Path,
    delivery: str = "bundled",
    url: str | None = None,
    start_timeout: float = 30,
    websocket: str = "unsupported",
    extra_args: list[str] | None = None,
    environment: dict[str, str] | None = None,
) -> dict:
    system, arch = host_target()
    data = archive.read_bytes()
    artifact = {
        "os": system,
        "arch": arch,
        "format": "zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "executable": _launcher_name(system),
        "expandedSizeLimit": 8 * 1024 * 1024,
    }
    if delivery == "bundled":
        artifact["file"] = "artifacts/fixture-release.zip"
    else:
        artifact["url"] = url or ""
    return {
        "schemaVersion": 3,
        "id": APP_ID,
        "name": "Fixture Notes",
        "version": version,
        "description": "A disposable web server used to test managed app hosting.",
        "category": "Testing",
        "author": "Vela tests",
        "color": "#3b82f6",
        "compatibility": {"managedService": 1},
        "source": {
            "upstream": "https://example.invalid/fixture/releases/" + version,
            "license": "MIT",
            "licenseFile": "LICENSE-upstream.txt",
        },
        "service": {
            "trust": "trusted-native",
            "artifacts": [artifact],
            "command": {
                "args": [
                    "--addr", "{host}",
                    "--port", "{port}",
                    "--data", "{dataDir}",
                    "--public-url", "{publicUrl}",
                    "--version", version,
                    *(extra_args or []),
                ],
                "workingDirectory": "code",
            },
            "environment": {"FIXTURE_VERSION": version, **(environment or {})},
            "endpoint": {
                "protocol": "http",
                "bind": "127.0.0.1",
                "basePath": "/",
                "websocket": websocket,
            },
            "readiness": {
                "path": "/healthz",
                "expectStatus": [200],
                "startTimeoutSeconds": start_timeout,
                "intervalSeconds": 0.1,
            },
            "lifetime": {
                "startWithVela": False,
                "stopTimeoutSeconds": 3,
                "restart": {"maxRetries": 2, "windowSeconds": 60, "backoffSeconds": 0.5},
            },
            "data": {
                "directory": "store",
                "backup": "stop-and-copy",
                "describe": "Notes database and uploaded attachments.",
            },
        },
        "view": {
            "surface": "managed-web",
            "chrome": "compact",
            "appearance": "auto",
            "embedding": "auto",
        },
        "integration": {"sdk": False, "agent": False},
    }


def build_package(root: Path, **options) -> Path:
    """A package directory ready to be reviewed. Returns the folder to import."""
    root = Path(root)
    package = root / APP_ID
    shutil.rmtree(package, ignore_errors=True)
    package.mkdir(parents=True)
    archive = build_artifact(root)
    delivery = options.get("delivery", "bundled")
    if delivery == "bundled":
        (package / "artifacts").mkdir()
        shutil.copyfile(archive, package / "artifacts/fixture-release.zip")
    (package / "LICENSE-upstream.txt").write_text(
        "MIT License\n\nCopyright (c) the fixture authors.\n", encoding="utf-8"
    )
    (package / "app.json").write_text(
        json.dumps(manifest(archive=archive, **options), indent=2), encoding="utf-8"
    )
    return package


def build_archive(root: Path, **options) -> Path:
    """The same package, zipped, for the upload path."""
    package = build_package(root, **options)
    archive = Path(root) / f"{APP_ID}-package.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for item in sorted(package.rglob("*")):
            if item.is_file():
                zipped.write(item, item.relative_to(package).as_posix())
    return archive


def write_broken(package: Path, mutate) -> Path:
    """Edit a built package's manifest in place, for the refusal tests."""
    path = Path(package) / "app.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return Path(package)


def service_environment(**values: str) -> dict[str, str]:
    """Environment values a manifest can carry to steer the fixture's behaviour."""
    return {name: str(value) for name, value in values.items() if value is not None}


if __name__ == "__main__":  # pragma: no cover - a convenience for people
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else ".local/fixture-package")
    sys.path.insert(0, str(HERE.parent.parent.parent))
    print(build_package(destination))
    print(f"interpreter baked in: {os.path.basename(sys.executable)}")
