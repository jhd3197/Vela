"""Build the published wallpaper masters and their pinned manifest.

Each of the eight painted wallpapers is published once — as a 4x JPEG master
on the `wallpapers-v1` GitHub release — and the server downloads it on first
start and derives the desk picture and picker thumbnail locally (see
`vela/bundled_wallpapers.py`). This script turns the 4x source art into those
masters and records their digests in `vela/assets/wallpapers.json`, the pin
the server verifies every download against.

Run it after the source art changes, then replace the release assets:

    python scripts/build-wallpaper-masters.py path/to/4x-art
    gh release upload wallpapers-v1 .local/wallpaper-masters/wallpaper-*-4x.jpg --clobber

The source directory holds one PNG per wallpaper id: avila, canaima,
castillo, chiguire, choroni, medanos, paramo, pueblo. Requires Pillow
(generation time only; masters and derived files are never committed).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTERS_DIR = ROOT / ".local" / "wallpaper-masters"
MANIFEST = ROOT / "vela" / "assets" / "wallpapers.json"
BASE_URL = "https://github.com/jhd3197/vela/releases/download/wallpapers-v1"
# The masters are the one published artifact, so they get the highest quality
# that still downloads quickly; the server derives everything smaller.
MASTER_QUALITY = 90


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="directory of 4x <id>.png source art")
    args = parser.parse_args()

    manifest = {"version": 1, "base_url": BASE_URL, "wallpapers": []}
    if MANIFEST.is_file():
        manifest["base_url"] = json.loads(MANIFEST.read_text(encoding="utf-8"))["base_url"]
    MASTERS_DIR.mkdir(parents=True, exist_ok=True)

    for source in sorted(args.source.glob("*.png")):
        wallpaper_id = source.stem
        dest = MASTERS_DIR / f"wallpaper-{wallpaper_id}-4x.jpg"
        with Image.open(source) as image:
            image.convert("RGB").save(
                dest, "JPEG", quality=MASTER_QUALITY, optimize=True, progressive=True
            )
        data = dest.read_bytes()
        manifest["wallpapers"].append(
            {
                "id": wallpaper_id,
                "file": dest.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
        print(f"{dest.name}: {len(data)} bytes")

    if not manifest["wallpapers"]:
        print(f"no PNG source art found in {args.source}", file=sys.stderr)
        return 1
    manifest["wallpapers"].sort(key=lambda entry: entry["id"])
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{MANIFEST.relative_to(ROOT)}: {len(manifest['wallpapers'])} wallpapers pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
