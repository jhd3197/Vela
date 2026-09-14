"""Generate Vela hub PWA icons into web/public/icons/.

Draws the Vela mark (a flame/sail droplet over two swoosh lines) as a dark
glyph on the amber brand tile, supersampled 4x for clean edges.

Run from anywhere:  python web/scripts/generate-icons.py
Requires Pillow (generation-time only; outputs are committed PNGs).
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

AMBER = (226, 163, 60, 255)        # #e2a33c
AMBER_DIM = (226, 163, 60, 140)    # secondary swoosh
DARK = (26, 18, 6, 255)            # #1a1206 tile glyph

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"
SIZES = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
}

SUPER = 4  # supersampling factor


def draw_mark(size: int) -> Image.Image:
    s = size * SUPER
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded amber tile, full bleed.
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=AMBER)

    # Flame/sail droplet: circle with a triangle crown, centered upper area.
    cx = s * 0.5
    cy = s * 0.35
    r = s * 0.145
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DARK)
    d.polygon(
        [(cx - r * 0.82, cy - r * 0.25), (cx + r * 0.82, cy - r * 0.25), (cx, cy - r * 2.15)],
        fill=DARK,
    )

    # Two swoosh lines below, echoing a sail's foot / flame glow. Drawn on a
    # separate layer and rotated so they rise toward the right.
    def swoosh(box, start, end, color, width):
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(layer).arc(box, start=start, end=end, fill=color, width=width)
        return layer.rotate(20, resample=Image.BICUBIC, center=(cx, s * 0.62))

    w = max(2, int(s * 0.026))
    img.alpha_composite(swoosh([s * 0.06, s * 0.50, s * 0.94, s * 0.76], 10, 170, DARK, w))
    img.alpha_composite(
        swoosh([s * 0.16, s * 0.60, s * 0.84, s * 0.76], 20, 160, AMBER_DIM, max(2, int(w * 0.8)))
    )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        path = OUT_DIR / name
        draw_mark(size).save(path, "PNG")
        print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    main()
