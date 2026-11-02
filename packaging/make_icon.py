"""Draw the app icon (two phones and an arrow) into an .iconset folder for `iconutil`.

    python packaging/make_icon.py build/icon.iconset
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn at full size, then scaled down


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Background: macOS-style rounded square with a vertical purple gradient.
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (138, 120, 250), (88, 64, 214)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100, 100, S - 100, S - 100], radius=185, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    soft = (255, 255, 255, 120)
    # Source phone (left) and target phone (right).
    for x0, fill in [(225, white), (585, soft)]:
        d.rounded_rectangle([x0, 300, x0 + 215, 724], radius=38, outline=white, width=26, fill=None)
        d.rounded_rectangle([x0 + 40, 360, x0 + 175, 600], radius=10, fill=fill)
        d.ellipse([x0 + 92, 640, x0 + 123, 671], fill=white)
    # Arrow between them.
    d.rounded_rectangle([440, 497, 560, 527], radius=15, fill=white)
    d.polygon([(545, 452), (600, 512), (545, 572)], fill=white)
    return img


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    big = draw()
    for size in (16, 32, 128, 256, 512):
        big.resize((size, size), Image.LANCZOS).save(out / f"icon_{size}x{size}.png")
        big.resize((size * 2, size * 2), Image.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
