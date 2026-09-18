"""Themes: colours a user can pick, import, export and remove.

A theme is **data, never code**. It is a JSON document naming canonical CSS
custom properties and their values, validated here before it is stored and
applied one property at a time in the browser. No CSS, no JavaScript, no
`url(`, and no font outside Vela's own list — the moment any of those are
allowed, a theme is a program somebody else wrote running on your dashboard.

Origin: ServerKit `backend/app/services/theme_tokens.py` (MIT, same owner) —
the whitelist, the forbidden-substring rule, the per-type validators and
`sanitize_tokens`. Two things are different.

ServerKit kept its whitelist in two files, a Python one and a JavaScript one,
with a comment asking the reader to keep them in step. Here there is one list:
`web/scripts/build-tokens.mjs` exports `web/src/design/tokens.js` to
`vela/assets/theme-tokens.json` and this module reads that file. A test compares
them, so the two cannot drift even for a commit.

ServerKit also let a theme set every step of every ramp, so a theme was eighty
values and a bad one was eighty ways to be unreadable. Here a theme sets one
base colour per role and the browser derives the ramp, so a theme is small and
step 600 of one role always weighs what step 600 of another does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors_http import VelaError

ASSETS = Path(__file__).resolve().parent / "assets"
WHITELIST_PATH = ASSETS / "theme-tokens.json"
BUNDLED_DIR = ASSETS / "themes"

#: The stock theme. Selecting it means "no inline tokens, the stylesheet as
#: generated" — the stock look is the stylesheet itself and never a copy of it,
#: so it cannot drift from what the build produces.
STOCK_SLUG = "vela"

SCHEMA_VERSION = 1
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")
BASES = ("light", "dark")

#: The wallpapers that ship, which is the only set a theme may suggest one
#: from. The dashboard keeps the same list in `web/src/desk/wallpaper.js`, where
#: it also knows each one's tone; `tests/test_themes.py` compares the two, so a
#: wallpaper added on one side and not the other fails the suite rather than
#: silently making every theme that suggests it unimportable.
BUNDLED_WALLPAPERS = (
    "choroni", "paramo", "medanos", "chiguire",
    "pueblo", "avila", "castillo", "canaima",
)

#: A theme file is a few dozen short strings. 32 KB is generous for that and
#: small enough that an import cannot be a way to write to the data directory.
MAX_THEME_BYTES = 32 * 1024
MAX_VALUE_LENGTH = 200
MAX_IMPORTED = 32
MAX_NAME = 60
MAX_DESCRIPTION = 200


class ThemeError(VelaError):
    """A theme that cannot be stored, with the reason and the failing rule.

    422 by default: almost every refusal here is "this document is not a theme
    Vela can use", and the detail names the rule it broke so the review sheet
    can say so rather than showing the user a shrug.
    """

    status = 422
    code = "theme.invalid"


def _whitelist() -> dict[str, Any]:
    """The token tables the dashboard's build exported. One list, not two."""
    try:
        return json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - a broken install
        raise ThemeError(
            "theme-tokens.json is missing; run web/scripts/build-tokens.mjs",
            status=500,
            code="theme.whitelist_missing",
        ) from exc


WHITELIST = _whitelist()
TOKEN_TYPE: dict[str, str] = WHITELIST["tokens"]
CANONICAL_TOKENS: tuple[str, ...] = tuple(TOKEN_TYPE)
ALIASES: dict[str, str] = WHITELIST["aliases"]
FONT_ALLOW_LIST: tuple[str, ...] = tuple(WHITELIST["fonts"])
SWATCH_TOKENS: tuple[str, ...] = tuple(WHITELIST["swatches"])

# ---------------------------------------------------------------- values --

# Anything that could turn a value into a rule, a request or a script. `url(`
# is the important one: it is the only way a theme could reach the network, and
# a theme that reaches the network is a theme that can tell somebody what you
# are looking at.
FORBIDDEN = re.compile(r"url\(|expression\(|javascript:|@import|[@;{}<>]|/\*|\\", re.IGNORECASE)

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC_COLOR = re.compile(r"^(?:rgba?|hsla?)\(\s*[0-9.,%\s/]+\)$", re.IGNORECASE)
_COLOR_MIX = re.compile(
    r"^color-mix\(\s*in\s+srgb\s*,\s*[#0-9a-zA-Z.,%()\s/-]{1,150}\)$", re.IGNORECASE
)
_LENGTH = re.compile(r"^(?:0|\d{1,4}(?:\.\d{1,3})?)(px|rem|em)$")
_SHADOW = re.compile(r"^[0-9a-zA-Z.,%()#\s/-]{1,200}$")
_GRADIENT = re.compile(
    r"^(?:linear|radial|conic)-gradient\([#0-9a-zA-Z.,%()\s/-]{1,400}\)"
    r"(?:\s*,\s*(?:linear|radial|conic)-gradient\([#0-9a-zA-Z.,%()\s/-]{1,400}\))*$",
    re.IGNORECASE,
)


def _is_color(value: str) -> bool:
    return bool(_HEX.match(value) or _FUNC_COLOR.match(value) or _COLOR_MIX.match(value))


def _is_font(value: str) -> bool:
    # An allow-list rather than a pattern. A font stack is the one token that
    # could name something the browser has to go and fetch, so a theme may only
    # choose from the faces Vela already loads.
    return value in FONT_ALLOW_LIST


def _is_shadow(value: str) -> bool:
    return value == "none" or bool(_SHADOW.match(value))


VALIDATORS = {
    "color": _is_color,
    "length": lambda value: bool(_LENGTH.match(value)),
    "font": _is_font,
    "shadow": _is_shadow,
    "gradient": lambda value: value == "none" or bool(_GRADIENT.match(value)),
}


def validate_token(token: str, value: Any) -> str | None:
    """The trimmed value if this token may be set to it, else None.

    Returning None rather than raising is deliberate: an import drops what it
    cannot accept and tells the user what it dropped, which is friendlier than
    refusing a theme wholesale over one stray key somebody's editor added.
    """
    kind = TOKEN_TYPE.get(token)
    if kind is None or not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_VALUE_LENGTH:
        return None
    if FORBIDDEN.search(trimmed):
        return None
    return trimmed if VALIDATORS[kind](trimmed) else None


def sanitize_tokens(raw: Any) -> tuple[dict[str, str], list[str]]:
    """Keep what is canonical and valid; return it with what was dropped."""
    if not isinstance(raw, dict):
        raise ThemeError("each base holds an object of token names and values")
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for token in CANONICAL_TOKENS:
        if token not in raw:
            continue
        checked = validate_token(token, raw[token])
        if checked is None:
            dropped.append(token)
        else:
            kept[token] = checked
    dropped.extend(sorted(set(raw) - set(CANONICAL_TOKENS)))
    return kept, dropped


def expand_aliases(tokens: dict[str, str]) -> dict[str, str]:
    """The legacy names the stylesheet still reads, from the canonical ones.

    The browser does this too, from the same table. It is here so the server can
    answer with a complete set for anything that reads a theme without a
    browser — a test, a backup report, a future export format.
    """
    out = dict(tokens)
    for alias, canonical in ALIASES.items():
        if canonical in tokens:
            out[alias] = tokens[canonical]
    return out


# --------------------------------------------------------------- document --


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ThemeError(f"{field} is required")
    trimmed = value.strip()
    if len(trimmed) > limit:
        raise ThemeError(f"{field} is at most {limit} characters")
    if FORBIDDEN.search(trimmed):
        raise ThemeError(f"{field} contains something a theme may not carry")
    return trimmed


def validate_document(
    raw: Any, *, wallpapers: tuple[str, ...] = BUNDLED_WALLPAPERS
) -> dict[str, Any]:
    """Check one theme document and return the shape that gets stored.

    Raises `ThemeError` for anything structural — a theme with no slug, no
    bases, or a base that sets nothing is not a theme with a mistake in it. The
    token level is forgiving: unknown and invalid tokens are dropped and listed
    under `dropped` so the review sheet can say what was left out.
    """
    if not isinstance(raw, dict):
        raise ThemeError("a theme is a JSON object")

    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ThemeError(f"schema_version must be {SCHEMA_VERSION}")

    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ThemeError("slug must match [a-z][a-z0-9-]{0,31}")

    bases = raw.get("bases")
    if not isinstance(bases, list) or not bases:
        raise ThemeError("bases lists at least one of light or dark")
    if any(base not in BASES for base in bases) or len(set(bases)) != len(bases):
        raise ThemeError("bases holds light, dark, or both, without repeats")

    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise ThemeError("tokens holds one object per base")

    version = raw.get("version", "1.0.0")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ThemeError("version looks like 1.0.0")

    checked: dict[str, dict[str, str]] = {}
    dropped: dict[str, list[str]] = {}
    for base in bases:
        kept, left_out = sanitize_tokens(tokens.get(base, {}))
        if not kept:
            raise ThemeError(f"the {base} base sets no tokens Vela recognises")
        checked[base] = kept
        if left_out:
            dropped[base] = left_out

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "name": _text(raw.get("name") or slug, "name", MAX_NAME),
        "author": _text(raw.get("author") or "Unknown", "author", MAX_NAME),
        "version": version,
        "bases": list(bases),
        "tokens": checked,
    }
    if raw.get("description"):
        document["description"] = _text(raw["description"], "description", MAX_DESCRIPTION)

    # A theme may *suggest* a wallpaper. It never sets one: a wallpaper is a
    # picture the user chose, and a theme arriving from a friend does not get to
    # replace it.
    suggests = raw.get("suggests")
    if isinstance(suggests, dict) and suggests.get("wallpaper"):
        wallpaper = suggests["wallpaper"]
        if not isinstance(wallpaper, str) or (wallpapers and wallpaper not in wallpapers):
            raise ThemeError("suggests.wallpaper must name a wallpaper Vela ships")
        document["suggests"] = {"wallpaper": wallpaper}

    # Unknown top-level keys are dropped rather than refused, and named, so a
    # theme from a newer Vela still applies on an older one.
    unknown = sorted(set(raw) - {
        "schema_version", "slug", "name", "author", "version", "description",
        "bases", "tokens", "suggests",
    })
    return {"theme": document, "dropped": dropped, "unknown": unknown}


def swatches(document: dict[str, Any], base: str | None = None) -> list[str]:
    """The strip Personalise draws a theme with, from the theme's own tokens."""
    chosen = base if base in document.get("bases", []) else document["bases"][0]
    tokens = document["tokens"][chosen]
    return [tokens[name] for name in SWATCH_TOKENS if name in tokens]


def summary(document: dict[str, Any], *, imported: bool) -> dict[str, Any]:
    """What the theme list shows, without the tokens themselves."""
    return {
        "slug": document["slug"],
        "name": document["name"],
        "author": document["author"],
        "bases": document["bases"],
        "description": document.get("description", ""),
        "suggests": document.get("suggests", {}),
        "swatches": swatches(document),
        "imported": imported,
    }


# ---------------------------------------------------------------- storage --


class Themes:
    """The bundled set, the imported ones, and the rules for adding to them."""

    def __init__(self, data_dir: Path, wallpapers: tuple[str, ...] = BUNDLED_WALLPAPERS):
        self._dir = Path(data_dir) / "themes"
        self._wallpapers = wallpapers
        self._bundled: dict[str, dict[str, Any]] = {}
        self._load_bundled()

    def _load_bundled(self) -> None:
        """Read the themes that ship with the server.

        A bundled theme that fails its own validator is raised here, at startup,
        rather than discovered by a user picking it: it is a build mistake, and
        the contrast gate in CI is what is supposed to catch it first.
        """
        for path in sorted(BUNDLED_DIR.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ThemeError(f"bundled theme {path.name} is not JSON: {exc}",
                                 status=500, code="theme.bundled_invalid") from exc
            checked = validate_document(raw, wallpapers=self._wallpapers)
            document = checked["theme"]
            if document["slug"] != path.stem:
                raise ThemeError(
                    f"bundled theme {path.name} calls itself {document['slug']!r}",
                    status=500, code="theme.bundled_invalid")
            if checked["dropped"]:
                raise ThemeError(
                    f"bundled theme {path.name} sets tokens Vela does not accept: "
                    f"{checked['dropped']}", status=500, code="theme.bundled_invalid")
            self._bundled[document["slug"]] = document

    # ------------------------------------------------------------ reading --

    def _imported_paths(self) -> list[Path]:
        return sorted(self._dir.glob("*.json")) if self._dir.is_dir() else []

    def imported(self) -> dict[str, dict[str, Any]]:
        """Every theme the user brought in, skipping any that no longer pass.

        A file that fails re-validation is left on disk and left out of the
        list. Deleting somebody's theme because this version reads it more
        strictly would be losing their work to solve our problem.
        """
        out: dict[str, dict[str, Any]] = {}
        for path in self._imported_paths():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                document = validate_document(raw, wallpapers=self._wallpapers)["theme"]
            except (json.JSONDecodeError, ThemeError, OSError):
                continue
            if document["slug"] in self._bundled or document["slug"] != path.stem:
                continue
            out[document["slug"]] = document
        return out

    def rejected(self) -> list[str]:
        """The imported files that no longer read as themes, for a report."""
        good = set(self.imported())
        return [path.stem for path in self._imported_paths() if path.stem not in good]

    def list(self) -> list[dict[str, Any]]:
        """Bundled first, then imported, each with its swatch strip."""
        return [
            *(summary(theme, imported=False) for theme in self._bundled.values()),
            *(summary(theme, imported=True) for theme in self.imported().values()),
        ]

    def get(self, slug: str) -> dict[str, Any]:
        if slug in self._bundled:
            return self._bundled[slug]
        found = self.imported().get(slug)
        if found is None:
            raise ThemeError(f"no theme called {slug!r}", status=404, code="theme.not_found")
        return found

    def exists(self, slug: str) -> bool:
        return slug in self._bundled or slug in self.imported()

    def slugs(self) -> list[str]:
        return [*self._bundled, *self.imported()]

    def is_bundled(self, slug: str) -> bool:
        return slug in self._bundled

    # ------------------------------------------------------------ writing --

    def import_document(self, raw: Any, *, replace: bool = False) -> dict[str, Any]:
        """Validate, sanitize and store a theme the user chose to bring in."""
        checked = validate_document(raw, wallpapers=self._wallpapers)
        document = checked["theme"]
        slug = document["slug"]
        if slug in self._bundled:
            raise ThemeError(
                f"{slug!r} is the name of a theme Vela ships; rename it to import it",
                status=409, code="theme.slug_taken")
        existing = self.imported()
        if slug not in existing and len(existing) >= MAX_IMPORTED:
            raise ThemeError(
                f"this server keeps at most {MAX_IMPORTED} imported themes",
                status=409, code="theme.too_many")
        if slug in existing and not replace:
            raise ThemeError(
                f"a theme called {slug!r} is already here; remove it first or send replace",
                status=409, code="theme.slug_taken")

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{slug}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return checked

    def remove(self, slug: str) -> None:
        if slug in self._bundled:
            raise ThemeError(f"{slug!r} ships with Vela and cannot be removed",
                             status=409, code="theme.bundled")
        path = self._dir / f"{slug}.json"
        if not path.is_file():
            raise ThemeError(f"no theme called {slug!r}", status=404, code="theme.not_found")
        path.unlink()

    def export(self, slug: str) -> str:
        """The stored document as a file. What comes out imports back in."""
        return json.dumps(self.get(slug), indent=2) + "\n"
