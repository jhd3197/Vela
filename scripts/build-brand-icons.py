"""Derive Vela's shipped icons from the one source mark.

The mark is drawn once, as `web/public/vela-mark.svg`, and everything else Vela
shows — the PWA icons, the Apple touch icon, the documentation logo and, through
that, the Windows `.ico` and the tray icon — is generated from it here. Keeping
one source and one script is what stops the eight places the sail appears from
drifting apart the next time it is redrawn.

The app icon is the brand kit's dark treatment: the mark centred on a Deep Navy
rounded square. Run it after replacing the mark:

    python scripts/build-brand-icons.py

It reads `web/public/vela-mark.png` (the rasterised mark, transparent) and
rewrites the derived files in place.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# Deep Navy from the brand kit. The app icon sits on this at every size.
NAVY = (0x11, 0x1A, 0x27, 0xFF)

# The corner radius as a share of the icon's edge, and how much of the edge the
# mark itself takes. Both are eyeballed against the brand kit's app icon: enough
# padding that the sail never touches the rounded corner at 16px.
RADIUS = 0.225
MARK = 0.66

# Drawing at four times the final size and shrinking is what keeps the rounded
# corner smooth; Pillow does not antialias `rounded_rectangle` on its own.
SUPERSAMPLE = 4

# Every derived file: the destination, its edge in pixels, and whether it takes
# the dark app-icon treatment or ships as the bare mark.
DERIVED = [
    ("web/public/icons/icon-512.png", 512, True),
    ("web/public/icons/icon-192.png", 192, True),
    ("web/public/icons/apple-touch-icon.png", 180, True),
    # The documentation logo is also the source `scripts/build-server.py` turns
    # into `vela.ico`, which becomes the Windows executable and tray icon.
    ("docs/images/logo.png", 512, True),
]


def trimmed_mark(path: Path) -> Image.Image:
    """The mark with its transparent margin removed, so scaling is predictable."""
    mark = Image.open(path).convert("RGBA")
    box = mark.getbbox()
    return mark.crop(box) if box else mark


def app_icon(mark: Image.Image, edge: int) -> Image.Image:
    big = edge * SUPERSAMPLE
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * RADIUS), fill=NAVY)

    # The mark keeps its aspect ratio and is centred on the square's optical
    # middle, which for a sail leaning right is the geometric middle.
    target = int(big * MARK)
    scale = min(target / mark.width, target / mark.height)
    placed = mark.resize((max(1, round(mark.width * scale)), max(1, round(mark.height * scale))), Image.LANCZOS)
    canvas.alpha_composite(placed, ((big - placed.width) // 2, (big - placed.height) // 2))
    return canvas.resize((edge, edge), Image.LANCZOS)


def main() -> None:
    source = ROOT / "web/public/vela-mark.png"
    if not source.is_file():
        raise SystemExit(f"The rasterised mark is missing: {source}")
    mark = trimmed_mark(source)
    for relative, edge, dark in DERIVED:
        target = ROOT / relative
        image = app_icon(mark, edge) if dark else mark.resize((edge, edge), Image.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", optimize=True)
        print(f"wrote {relative} ({edge}px)")


if __name__ == "__main__":
    main()
