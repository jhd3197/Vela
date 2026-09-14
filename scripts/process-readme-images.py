"""Prepare README images: crop the app icon off its black background and
copy the poster next to it. Screenshots are NOT made here — real captures
come from `npm run shots` in web/ (see web/scripts/capture-screenshots.mjs).

Usage: python scripts/process-readme-images.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "docs" / "images"

LOGO_SRC = IMG_DIR / "logo-source.png"
POSTER_SRC = IMG_DIR / "poster-source.png"


def crop_logo() -> None:
    img = Image.open(LOGO_SRC).convert("RGBA")
    gray = img.convert("L")
    # The rounded-square art sits on pure black; find its bounds.
    bbox = gray.point(lambda p: 255 if p > 14 else 0).getbbox()
    icon = img.crop(bbox)
    # Rounded corners -> transparent, so the icon works on light and dark themes.
    mask = Image.new("L", icon.size, 0)
    radius = int(icon.width * 0.225)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (icon.width - 1, icon.height - 1)], radius=radius, fill=255
    )
    icon.putalpha(mask)
    icon = icon.resize((512, 512), Image.LANCZOS)
    icon.save(IMG_DIR / "logo.png")
    print(f"logo.png: {icon.size}")


def copy_poster() -> None:
    poster = Image.open(POSTER_SRC).convert("RGB")
    poster.save(IMG_DIR / "poster.png")
    print(f"poster.png: {poster.size}")


if __name__ == "__main__":
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    crop_logo()
    copy_poster()
