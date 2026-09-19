"""Getting a release artifact onto the disk without letting it choose where.

Two different sets of bounds meet here. The Vela package -- `app.json`, an icon,
an upstream licence -- is small, and `vela/package_files.py` already knows how to
copy one safely. The upstream release archive is not: a single Memos binary is
60 MB, and expanded trees are larger still. So this module keeps its own,
explicitly larger ceilings for the artifact and reuses the portable-path rules
for both.

Nothing here trusts a name inside an archive. Every entry is normalised, checked
against the package-file rules, resolved against the destination and refused if
the result is not inside it. Links, devices, sockets and Windows reparse points
are refused outright: an extracted tree that contains a link is a tree whose
later copy, backup or delete can reach somewhere it was never shown.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import stat
import tarfile
import time
import zipfile
from pathlib import Path

import httpx

from ..errors_http import Conflict, TooLarge, Unprocessable, Upstream
from ..package_files import portable

#: The Vela package itself: metadata, an icon, a licence, maybe a bundled
#: archive. The bundled archive is the only large member, so the ceiling is the
#: artifact ceiling plus room for the rest.
MAX_PACKAGE_FILES = 256

#: One upstream release archive. Large because real ones are: Memos ships a
#: 20 MB zip holding a 60 MB executable.
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024

#: The extracted tree. A package may declare a smaller `expandedSizeLimit`; it
#: can never declare a larger one than this.
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_EXPANDED_FILES = 20000

#: How long a whole artifact download may take, and how long a stalled
#: connection is tolerated.
DOWNLOAD_DEADLINE_SECONDS = 900
DOWNLOAD_READ_TIMEOUT_SECONDS = 60

_CHUNK = 1024 * 1024


class ArtifactError(Unprocessable):
    """A release artifact that cannot be accepted, and why."""

    code = "managed.artifact_invalid"


def digest_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks so a 60 MB binary is not held in memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def digest_tree(folder: Path) -> str:
    """A stable digest of a directory tree: every relative path and its content.

    Used to prove that the bytes approved in an install review are the bytes
    that were activated, and that an installed tree has not been edited since.
    """
    folder = Path(folder)
    digest = hashlib.sha256()
    for item in sorted(folder.rglob("*"), key=lambda path: path.relative_to(folder).as_posix()):
        relative = item.relative_to(folder).as_posix()
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ArtifactError(f"Package contains a link, which is not supported: {relative}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise ArtifactError(f"Package contains a special file: {relative}")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(digest_file(item)))
    return digest.hexdigest()


def copy_package(source: Path, target: Path) -> None:
    """Copy a managed-app package directory, refusing anything nonportable."""
    source, target = Path(source), Path(target)
    if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
        raise ArtifactError("Linked package roots are not supported")
    entries: list[tuple[str, Path]] = []
    total = 0
    names: set[str] = set()
    for item in sorted(source.rglob("*")):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ArtifactError("Package links are not supported")
        name = portable(item.relative_to(source).as_posix())
        if item.is_dir():
            continue
        if not item.is_file():
            raise ArtifactError("Only ordinary package files are supported")
        if name.casefold() in names:
            raise ArtifactError("Package has case-colliding files")
        names.add(name.casefold())
        total += item.stat().st_size
        entries.append((name, item))
        if len(entries) > MAX_PACKAGE_FILES:
            raise TooLarge(
                f"A managed app package holds at most {MAX_PACKAGE_FILES} files",
                code="managed.package_too_large",
            )
        if total > MAX_ARTIFACT_BYTES + (16 * 1024 * 1024):
            raise TooLarge("Package exceeds its size limit", code="managed.package_too_large")
    target.mkdir(parents=True, exist_ok=False)
    for name, item in entries:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, destination)


def unpack_package(archive: Path, target: Path) -> None:
    """Expand a `.zip` holding the Vela package itself (not the upstream artifact)."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as zipped:
            members = zipped.infolist()
            if len(members) > MAX_PACKAGE_FILES:
                raise TooLarge(
                    f"A managed app package holds at most {MAX_PACKAGE_FILES} files",
                    code="managed.package_too_large",
                )
            _extract_zip(zipped, members, target, MAX_ARTIFACT_BYTES + 16 * 1024 * 1024)
    except zipfile.BadZipFile as exc:
        raise ArtifactError("That is not a readable ZIP package") from exc


def download_artifact(url: str, *, sha256: str, size: int, destination: Path) -> Path:
    """Fetch an upstream archive over HTTPS, bounded, and verify it completely.

    The download finishes and the digest matches before anything else happens.
    That is what lets an install review bind approval to bytes: nothing is
    extracted, and nothing is shown as reviewable, until the file on disk is
    known to be the file the manifest named.
    """
    if not url.startswith("https://"):
        raise ArtifactError("A release artifact must be downloaded over HTTPS")
    if size > MAX_ARTIFACT_BYTES:
        raise TooLarge(
            f"That release artifact is larger than the {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB limit",
            code="managed.artifact_too_large",
        )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    started = time.monotonic()
    written = 0
    digest = hashlib.sha256()
    try:
        timeout = httpx.Timeout(DOWNLOAD_READ_TIMEOUT_SECONDS, connect=20.0)
        with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise Upstream(
                        f"The release download answered HTTP {response.status_code}",
                        code="managed.artifact_unavailable",
                    )
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(_CHUNK):
                        if time.monotonic() - started > DOWNLOAD_DEADLINE_SECONDS:
                            raise Upstream(
                                "The release download took too long and was stopped",
                                code="managed.artifact_timeout",
                            )
                        written += len(chunk)
                        if written > size:
                            raise ArtifactError(
                                "The release download is larger than the size the package declared"
                            )
                        digest.update(chunk)
                        output.write(chunk)
    except httpx.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise Upstream(
            f"The release could not be downloaded: {exc}", code="managed.artifact_unavailable"
        ) from exc
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if written != size:
        temporary.unlink(missing_ok=True)
        raise ArtifactError(
            f"The release download stopped at {written} bytes; the package declared {size}"
        )
    if digest.hexdigest() != sha256:
        temporary.unlink(missing_ok=True)
        raise ArtifactError(
            "The release download does not match the checksum in the package. "
            "Nothing was installed."
        )
    temporary.replace(destination)
    return destination


def verify_artifact(path: Path, *, sha256: str, size: int) -> None:
    """Check a local archive against the manifest before it is trusted."""
    path = Path(path)
    if not path.is_file():
        raise ArtifactError(f"The package does not contain its release archive: {path.name}")
    actual = path.stat().st_size
    if actual != size:
        raise ArtifactError(
            f"{path.name} is {actual} bytes; the package declared {size}. Nothing was installed."
        )
    if digest_file(path) != sha256:
        raise ArtifactError(
            f"{path.name} does not match the checksum in the package. Nothing was installed."
        )


def extract_artifact(archive: Path, *, format: str, target: Path, limit: int | None = None) -> None:
    """Expand a verified release archive into a directory it cannot escape."""
    archive, target = Path(archive), Path(target)
    ceiling = min(limit or MAX_EXPANDED_BYTES, MAX_EXPANDED_BYTES)
    target.mkdir(parents=True, exist_ok=False)
    try:
        if format == "zip":
            with zipfile.ZipFile(archive) as zipped:
                members = zipped.infolist()
                if len(members) > MAX_EXPANDED_FILES:
                    raise TooLarge(
                        f"That archive holds more than {MAX_EXPANDED_FILES} entries",
                        code="managed.artifact_too_large",
                    )
                _extract_zip(zipped, members, target, ceiling)
        elif format == "tar.gz":
            with tarfile.open(archive, "r:gz") as tarred:
                _extract_tar(tarred, target, ceiling)
        else:  # pragma: no cover - the schema restricts the enumeration
            raise ArtifactError(f"Unsupported archive format: {format}")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise ArtifactError(f"The release archive could not be read: {exc}") from exc
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _destination(target: Path, name: str) -> Path:
    """Resolve an archive member inside `target`, or refuse it by name.

    Two checks, not one. `portable` refuses the shapes that are wrong on any
    platform -- absolute paths, `..`, drive letters, backslashes, reserved
    Windows device names, control characters. The resolve-and-compare afterwards
    is what catches a name that passes those rules and still lands outside,
    because a parent on this filesystem turned out to be a link.
    """
    try:
        relative = portable(name)
    except Exception as exc:  # noqa: BLE001 - re-raised below with the member named
        raise ArtifactError(
            f"The release archive contains a path this computer cannot store safely: {name}"
        ) from exc
    resolved = (target / relative).resolve()
    if resolved != target.resolve() and not resolved.is_relative_to(target.resolve()):
        raise ArtifactError(f"The release archive tries to write outside the app: {name}")
    return resolved


def _extract_zip(zipped: zipfile.ZipFile, members, target: Path, ceiling: int) -> None:
    declared = sum(member.file_size for member in members)
    if declared > ceiling:
        raise TooLarge(
            f"That archive expands to {declared} bytes, past the {ceiling} byte limit",
            code="managed.artifact_too_large",
        )
    names: set[str] = set()
    written = 0
    for member in members:
        raw = member.filename.rstrip("/")
        if not raw:
            continue
        mode = member.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ArtifactError(
                f"The release archive contains a link or special file: {member.filename}"
            )
        if member.flag_bits & 0x1:
            raise ArtifactError("Encrypted release archives are not supported")
        destination = _destination(target, raw)
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        key = str(destination).casefold()
        if key in names:
            raise ArtifactError(f"The release archive has colliding paths: {member.filename}")
        names.add(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipped.open(member) as source, destination.open("xb") as output:
            while chunk := source.read(_CHUNK):
                written += len(chunk)
                if written > ceiling:
                    raise TooLarge(
                        f"That archive expands past the {ceiling} byte limit",
                        code="managed.artifact_too_large",
                    )
                output.write(chunk)
        # Keep the executable bit the archive recorded. Ignored on Windows;
        # on POSIX it is the difference between a release that starts and one
        # that reports permission denied.
        if mode & 0o111:
            destination.chmod(destination.stat().st_mode | 0o111)


def _extract_tar(tarred: tarfile.TarFile, target: Path, ceiling: int) -> None:
    written = 0
    count = 0
    names: set[str] = set()
    for member in tarred:
        count += 1
        if count > MAX_EXPANDED_FILES:
            raise TooLarge(
                f"That archive holds more than {MAX_EXPANDED_FILES} entries",
                code="managed.artifact_too_large",
            )
        if member.issym() or member.islnk():
            raise ArtifactError(f"The release archive contains a link: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ArtifactError(f"The release archive contains a special file: {member.name}")
        destination = _destination(target, member.name.rstrip("/"))
        if member.isdir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        key = str(destination).casefold()
        if key in names:
            raise ArtifactError(f"The release archive has colliding paths: {member.name}")
        names.add(key)
        written += member.size
        if written > ceiling:
            raise TooLarge(
                f"That archive expands past the {ceiling} byte limit",
                code="managed.artifact_too_large",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = tarred.extractfile(member)
        if source is None:  # pragma: no cover - defended above
            raise ArtifactError(f"The release archive entry could not be read: {member.name}")
        with source, destination.open("xb") as output:
            shutil.copyfileobj(source, output, _CHUNK)
        if member.mode & 0o111:
            destination.chmod(destination.stat().st_mode | 0o111)


def executable_in(tree: Path, relative: str) -> Path:
    """The declared program inside an extracted tree, checked before it is run."""
    tree = Path(tree).resolve()
    candidate = (tree / relative).resolve()
    if not candidate.is_relative_to(tree):
        raise ArtifactError(f"The package's executable is outside its own files: {relative}")
    if not candidate.is_file():
        raise Conflict(
            f"The release does not contain {relative}, which the package says to run",
            code="managed.executable_missing",
        )
    return candidate


def read_text_if_present(path: Path, limit: int = 64 * 1024) -> str | None:
    """A bounded read of a packaged licence or notice, for the install review."""
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        data = data[:limit]
    return io.BytesIO(data).read().decode("utf-8", errors="replace")
