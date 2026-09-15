"""Bounded, portable release files. No archive member may escape its package."""
import hashlib
import json
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath

from .app_storage import AppServiceError

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 2048


def portable(name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or '\\' in name or ':' in name or any(
        part in ('.', '..') or part.endswith((' ', '.')) or
        re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
        or any(ord(c) < 32 for c in part) for part in parts
    ):
        raise AppServiceError(422, 'Package contains an unsafe or nonportable path')
    return '/'.join(parts)


def files(folder):
    folder = Path(folder)
    result, total, names = [], 0, set()
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
        raise AppServiceError(422, 'Linked package roots are not supported')
    for item in sorted(folder.rglob('*')):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise AppServiceError(422, 'Package links are not supported')
        name = portable(item.relative_to(folder).as_posix())
        if item.is_dir(): continue
        if not item.is_file(): raise AppServiceError(422, 'Only ordinary package files are supported')
        if name.casefold() in names: raise AppServiceError(422, 'Package has case-colliding files')
        names.add(name.casefold())
        total += item.stat().st_size
        result.append((name, item))
        if len(result) > MAX_FILES or total > MAX_BYTES:
            raise AppServiceError(413, 'Package exceeds 2048 files or 32 MiB')
    return result


def tree_digest(folder):
    digest = hashlib.sha256()
    for name, item in files(folder):
        digest.update(name.encode() + b'\0' + hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


RENAME_RETRY_SECONDS = 2.0


def replace_dir(source, target):
    """Move a staged package directory into place.

    On Windows a virus scanner or indexer can still hold a handle on files that
    were written moments ago, which makes the rename fail with PermissionError
    even though nothing is wrong with the package. Retry briefly instead of
    failing an install that would succeed a moment later.
    """
    source, target = Path(source), Path(target)
    deadline = time.monotonic() + RENAME_RETRY_SECONDS
    while True:
        try:
            source.rename(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def copy_package(source, target):
    entries = files(source)
    Path(target).mkdir(parents=True, exist_ok=False)
    for name, item in entries:
        dest = Path(target) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, dest)


def unpack(archive, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as zipped:
            members = zipped.infolist()
            if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_BYTES:
                raise AppServiceError(413, 'Archive exceeds 2048 entries or 32 MiB')
            names, total = set(), 0
            for member in members:
                name = portable(member.filename.rstrip('/'))
                if name.casefold() in names: raise AppServiceError(422, 'Archive has duplicate paths')
                names.add(name.casefold())
                mode = member.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                    raise AppServiceError(422, 'Archive links, special files and encryption are unsupported')
                dest = target / name
                if member.is_dir(): dest.mkdir(parents=True, exist_ok=True); continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, dest.open('xb') as output:
                    while chunk := source.read(65536):
                        total += len(chunk)
                        if total > MAX_BYTES: raise AppServiceError(413, 'Expanded archive exceeds 32 MiB')
                        output.write(chunk)
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError) as exc:
        raise AppServiceError(422, 'Invalid release ZIP archive') from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
