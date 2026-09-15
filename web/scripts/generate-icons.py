"""Resize the existing Vela logo into the hub's install icons.

Run from anywhere:  python web/scripts/generate-icons.py
Requires Pillow (generation-time only; outputs are committed PNGs).
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "images" / "logo.png"
OUT_DIR = ROOT / "web" / "public" / "icons"
SIZES = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as source:
        logo = source.convert("RGBA")
    # Preserve the source logo's alpha channel, including transparent corners.
    for name, size in SIZES.items():
        path = OUT_DIR / name
        logo.resize((size, size), Image.Resampling.LANCZOS).save(path, "PNG")
        print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    main()
