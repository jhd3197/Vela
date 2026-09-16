"""Version parsing, shared by the release tooling and the updater.

This lived in `scripts/release.py`, which is a maintainer tool and is not part
of a packaged server. The updater has to compare versions inside a running
Vela, so the rule about what a version is belongs here, with one definition
both sides use.
"""

import re

VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def version_tuple(value: str) -> tuple[int, int, int]:
    """`"0.2.1"` as `(0, 2, 1)`, refusing anything that is not MAJOR.MINOR.PATCH."""
    if not VERSION_RE.fullmatch(value or ""):
        raise ValueError("Version must be MAJOR.MINOR.PATCH, without a v prefix or suffix")
    return tuple(int(part) for part in value.split("."))


def parse_tag(tag: str) -> str | None:
    """The version inside a release tag (`v0.2.0` → `0.2.0`), or None.

    GitHub tags carry a `v`; a release published by hand might not. Both are
    read, anything else is ignored rather than guessed at.
    """
    candidate = (tag or "").strip()
    if candidate.startswith("v"):
        candidate = candidate[1:]
    try:
        version_tuple(candidate)
    except ValueError:
        return None
    return candidate


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a release after `current`.

    An unreadable version on either side is not newer. Vela will not offer an
    update it cannot name.
    """
    try:
        return version_tuple(candidate) > version_tuple(current)
    except ValueError:
        return False
