"""App manifest (app.json) loading and validation."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator

SUPPORTED_PLATFORMS = ("posix", "windows", "android")
PLATFORM_KEYS = SUPPORTED_PLATFORMS + ("web",)

DEFAULT_APP_COLOR = "#9184d9"

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_REQUIRED_FIELDS = ("id", "name", "version", "description", "category", "author", "platforms")
SUPPORTED_CAPABILITIES = frozenset({"storage", "connections", "actions"})
_V2_SCHEMA = json.loads((Path(__file__).resolve().parent / "assets/manifest-v2.schema.json").read_text(encoding="utf-8"))


class ManifestError(ValueError):
    """Raised when an app.json manifest is missing or invalid."""


@dataclass(frozen=True)
class PlatformSpec:
    run: str
    port: int | None = None


@dataclass(frozen=True)
class WebSpec:
    entry: str
    theme_color: str | None = None
    background_color: str | None = None


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    category: str
    author: str
    icon: str | None
    color: str | None
    platforms: dict[str, PlatformSpec | None]
    web: WebSpec | None
    raw: dict[str, Any] = field(repr=False, compare=False)
    path: Path = field(repr=False, compare=False)

    @property
    def schema_version(self) -> int:
        return self.raw.get("schemaVersion", 1)

    @property
    def view(self) -> dict:
        if self.schema_version == 1:
            return {"surface": "embedded", "chrome": "compact"}
        view = dict(self.raw["view"])
        if view["surface"] == "embedded":
            view.setdefault("chrome", "compact")
        return view

    @property
    def capabilities(self) -> list[str]:
        requested = self.raw.get("capabilities", {}) if self.schema_version == 2 else {}
        return sorted((set(requested.get("required", [])) | set(requested.get("optional", []))) & SUPPORTED_CAPABILITIES)

    @property
    def unavailable_capabilities(self) -> list[str]:
        optional = self.raw.get("capabilities", {}).get("optional", []) if self.schema_version == 2 else []
        return sorted(set(optional) - SUPPORTED_CAPABILITIES)

    def platform_spec(self, platform: str) -> PlatformSpec | None:
        return self.platforms.get(platform)

    def supports(self, platform: str) -> bool:
        if self.schema_version == 2:
            if self.raw["runtime"].get("process"):
                return self.platform_spec(platform) is not None
            return self.web is not None or self.view["surface"] in ("external", "none")
        # A web entry makes the app supported on every platform.
        return self.web is not None or self.platforms.get(platform) is not None

    def runtimes(self, platform: str) -> list[str]:
        """Runtime kinds this manifest can use here: "web" and/or "process"."""
        kinds = []
        if self.web is not None:
            kinds.append("web")
        if self.platforms.get(platform) is not None:
            kinds.append("process")
        return kinds

    def active_runtime(self, platform: str) -> str | None:
        """The runtime the hub will actually use; web wins when both exist."""
        if self.schema_version == 2 and self.raw["runtime"].get("process"):
            return "process" if self.platform_spec(platform) else None
        if self.schema_version == 2 and self.view["surface"] in ("external", "none") and not self.web:
            return self.view["surface"]
        if self.web is not None:
            return "web"
        if self.platforms.get(platform) is not None:
            return "process"
        return None


def load_manifest(app_dir: Path) -> Manifest:
    manifest_path = app_dir / "app.json"
    if not manifest_path.is_file():
        raise ManifestError(f"{app_dir.name}: missing app.json")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{app_dir.name}: invalid JSON in app.json: {exc}") from exc
    return validate_manifest(data, folder=app_dir.name, path=app_dir)


def validate_manifest(data: Any, folder: str, path: Path) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError(f"{folder}: app.json must be a JSON object")

    raw = data
    version = data.get("schemaVersion", 1)
    if type(version) is not int or version not in (1, 2):
        raise ManifestError(f"{folder}: unsupported manifest schema version: {version!r}")
    if version == 2:
        errors = list(Draft202012Validator(_V2_SCHEMA).iter_errors(data))
        if errors:
            raise ManifestError(f"{folder}: v2 manifest: {errors[0].message}")
        missing_caps = set(data.get("capabilities", {}).get("required", [])) - SUPPORTED_CAPABILITIES
        if missing_caps:
            raise ManifestError(f"{folder}: unsupported required capabilities: {', '.join(sorted(missing_caps))}")
        runtime = data["runtime"]
        grants = data.get('capabilities', {}).get('required', []) + data.get('capabilities', {}).get('optional', [])
        if (data.get('actions') or data.get('actionRequests')) and 'actions' not in grants:
            raise ManifestError(f'{folder}: actions require the actions capability')
        if data.get('actions') and 'storage' not in grants:
            raise ManifestError(f'{folder}: storage actions require storage')
        if len({item['id'] for item in data.get('actions', [])}) != len(data.get('actions', [])):
            raise ManifestError(f'{folder}: action IDs must be unique')
        bundle = data.get('data', {}).get('legacyBundle')
        if bundle and any(not key.startswith(f"vela.{data['id']}.") for key in bundle['keys'].values()):
            raise ManifestError(f'{folder}: legacy keys must belong to this app')
        if bundle and data['data'].get('legacy'):
            raise ManifestError(f'{folder}: choose one legacy import format')
        if data.get("data", {}).get("legacy", {}).get("key") and not data["data"]["legacy"]["key"].startswith(f"vela.{data['id']}."):
            raise ManifestError(f"{folder}: legacy storage key must belong to this app's namespace")
        if "connection" in data and "connections" not in (data.get("capabilities", {}).get("required", []) + data.get("capabilities", {}).get("optional", [])):
            raise ManifestError(f"{folder}: connection requires the connections capability")
        if data["view"]["surface"] == "embedded" and not runtime:
            raise ManifestError(f"{folder}: embedded view requires a runtime")
        if "storage" in (data.get("capabilities", {}).get("required", []) + data.get("capabilities", {}).get("optional", [])) and "data" not in data:
            raise ManifestError(f"{folder}: storage requires a data schemaVersion")
        platforms = dict(runtime.get("process", {}).get("platforms", {}))
        if "static" in runtime:
            platforms["web"] = runtime["static"]
        data = {**data, "platforms": platforms or {"web": None}}

    missing = [key for key in _REQUIRED_FIELDS if key not in data]
    if missing:
        raise ManifestError(f"{folder}: missing required fields: {', '.join(missing)}")

    app_id = data["id"]
    if not isinstance(app_id, str) or not _ID_RE.match(app_id):
        raise ManifestError(f"{folder}: id must be a lowercase slug, got {app_id!r}")
    if app_id != folder:
        raise ManifestError(f"{folder}: id {app_id!r} must match the folder name")

    for key in ("name", "version", "description", "category", "author"):
        if not isinstance(data[key], str):
            raise ManifestError(f"{folder}: {key} must be a string")

    icon = data.get("icon")
    if icon is not None and not isinstance(icon, str):
        raise ManifestError(f"{folder}: icon must be a string")

    color = data.get("color")
    if color is not None and (not isinstance(color, str) or not _COLOR_RE.match(color)):
        raise ManifestError(f"{folder}: color must be a hex color like #8b5cf6")

    platforms = data["platforms"]
    if not isinstance(platforms, dict) or not platforms:
        raise ManifestError(f"{folder}: platforms must be a non-empty object")

    specs: dict[str, PlatformSpec | None] = {}
    web: WebSpec | None = None
    for key, entry in platforms.items():
        if key not in PLATFORM_KEYS:
            raise ManifestError(f"{folder}: unknown platform {key!r}")
        if key == "web":
            if entry is None:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("entry"), str):
                raise ManifestError(f"{folder}: platforms.web requires a string 'entry' file")
            theme = entry.get("theme_color")
            background = entry.get("background_color")
            if theme is not None and not isinstance(theme, str):
                raise ManifestError(f"{folder}: platforms.web.theme_color must be a string")
            if background is not None and not isinstance(background, str):
                raise ManifestError(f"{folder}: platforms.web.background_color must be a string")
            web = WebSpec(entry=entry["entry"], theme_color=theme, background_color=background)
            continue
        if entry is None:
            specs[key] = None
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("run"), str):
            raise ManifestError(f"{folder}: platforms.{key} requires a string 'run' command")
        port = entry.get("port")
        if port is not None and (not isinstance(port, int) or not 0 < port < 65536):
            raise ManifestError(f"{folder}: platforms.{key}.port must be an int in 1..65535")
        specs[key] = PlatformSpec(run=entry["run"], port=port)

    return Manifest(
        id=app_id,
        name=data["name"],
        version=data["version"],
        description=data["description"],
        category=data["category"],
        author=data["author"],
        icon=icon,
        color=color,
        platforms=specs,
        web=web,
        raw=raw,
        path=path,
    )
