"""The eight bundled wallpapers: one published master each, the rest derived here.

Each painted wallpaper is published once, as a 4x master attached to the
`wallpapers-v1` GitHub release; the installer and the server bundle carry none
of them. On first start `ensure()` downloads whatever is missing, checks it
against the digest pinned in `assets/wallpapers.json`, and derives the two
sizes the dashboard actually draws:

    <data_dir>/wallpapers/masters/wallpaper-<id>-4x.jpg   the published original
    <data_dir>/wallpapers/<id>.jpg                        1672x941, the desk picture
    <data_dir>/wallpapers/thumbs/<id>.jpg                 480x270, the picker preview

Everything under `wallpapers/` can be thrown away: the next start downloads
and derives it again. That is why only digests are pinned, never derived
files, and why backups and support bundles have nothing to do with this
directory. `VELA_WALLPAPER_BASE_URL` points the download somewhere else;
tests use it with a local stand-in for the release.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import httpx

LOG = logging.getLogger(__name__)

MANIFEST_FILE = Path(__file__).resolve().parent / "assets" / "wallpapers.json"
ENV_BASE_URL = "VELA_WALLPAPER_BASE_URL"
DISPLAY_SIZE = (1672, 941)
THUMB_SIZE = (480, 270)
# The desk picture fills the window behind the whole shell; the thumbnail is a
# small picker swatch. Both stay JPEG like their master.
DISPLAY_QUALITY = 88
THUMB_QUALITY = 85
CHUNK = 256 * 1024


def load_manifest() -> dict:
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _needed(root: Path, entries: list[dict]) -> list[dict]:
    return [
        entry
        for entry in entries
        if not (root / f"{entry['id']}.jpg").is_file()
        or not (root / "thumbs" / f"{entry['id']}.jpg").is_file()
    ]


def _master_ok(master: Path, entry: dict) -> bool:
    if not master.is_file() or master.stat().st_size != entry["bytes"]:
        return False
    digest = hashlib.sha256()
    with master.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest() == entry["sha256"]


def _download(client: httpx.Client, base_url: str, entry: dict, master: Path) -> None:
    part = master.with_suffix(".part")
    digest = hashlib.sha256()
    total = 0
    with client.stream("GET", f"{base_url}/{entry['file']}") as response:
        response.raise_for_status()
        with part.open("wb") as out:
            for chunk in response.iter_bytes(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                total += len(chunk)
    if total != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
        part.unlink(missing_ok=True)
        raise ValueError(f"{entry['file']}: download did not match the pinned digest")
    part.replace(master)


def _derive(master: Path, root: Path, entry: dict) -> None:
    # Imported lazily so the module still loads where Pillow is absent; the
    # failure then lands on the one wallpaper being derived, not on startup.
    from PIL import Image

    with Image.open(master) as source:
        image = source.convert("RGB")
        image.resize(DISPLAY_SIZE, Image.LANCZOS).save(
            root / f"{entry['id']}.jpg", "JPEG", quality=DISPLAY_QUALITY, optimize=True
        )
        image.resize(THUMB_SIZE, Image.LANCZOS).save(
            root / "thumbs" / f"{entry['id']}.jpg", "JPEG", quality=THUMB_QUALITY, optimize=True
        )


def ensure(
    data_dir: Path,
    *,
    manifest: dict | None = None,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Download missing wallpaper masters and derive the dashboard's sizes.

    Idempotent: a start that finds every display image and thumbnail in place
    touches neither the network nor the disk. One wallpaper failing — offline,
    a truncated download, a master that no longer matches its pin — is
    reported and skipped, never a reason to keep the server down; the next
    start tries again.
    """
    manifest = manifest or load_manifest()
    entries = manifest["wallpapers"]
    root = data_dir / "wallpapers"
    result: dict = {"downloaded": [], "derived": [], "failed": {}}
    needed = _needed(root, entries)
    if not needed:
        return result
    base_url = (base_url or os.environ.get(ENV_BASE_URL) or manifest["base_url"]).rstrip("/")
    (root / "masters").mkdir(parents=True, exist_ok=True)
    (root / "thumbs").mkdir(parents=True, exist_ok=True)
    own_client = client is None
    if own_client:
        client = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        for entry in needed:
            try:
                master = root / "masters" / entry["file"]
                if not _master_ok(master, entry):
                    _download(client, base_url, entry, master)
                    result["downloaded"].append(entry["id"])
                _derive(master, root, entry)
                result["derived"].append(entry["id"])
            except Exception as exc:  # one picture must not hold the rest
                LOG.warning("wallpapers: %s unavailable: %s", entry["id"], exc)
                result["failed"][entry["id"]] = str(exc)
    finally:
        if own_client:
            client.close()
    if result["downloaded"]:
        LOG.info("wallpapers: fetched %d master(s), derived %s, failed %s",
                 len(result["downloaded"]), result["derived"], sorted(result["failed"]))
    return result
