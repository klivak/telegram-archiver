"""Turn a logo generated on a solid background (default magenta #FF00FF) into transparent PNGs and a Windows .ico.

Usage: uv run --with pillow python scripts/make_icons.py generated.png [--bg FF00FF] [--tolerance 60] [--out docs/assets]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def remove_background(img: Image.Image, bg: tuple[int, int, int], tolerance: int) -> Image.Image:
    img = img.convert("RGBA")
    px = img.load()
    assert px is not None
    w, h = img.size
    soft = tolerance * 2  # anti-aliased edge band gets partial alpha
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            d = max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2]))
            if d <= tolerance:
                px[x, y] = (0, 0, 0, 0)
            elif d < soft:
                k = (d - tolerance) / (soft - tolerance)
                # un-mix the background colour from the edge pixel
                un = [min(255, max(0, round((c - bg[i] * (1 - k)) / k))) for i, c in enumerate((r, g, b))]
                px[x, y] = (un[0], un[1], un[2], round(a * k))
    return img


def square_crop(img: Image.Image, pad: float = 0.04) -> Image.Image:
    box = img.getbbox()
    if box:
        img = img.crop(box)
    side = round(max(img.size) * (1 + 2 * pad))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--bg", default="FF00FF", help="background hex colour")
    ap.add_argument("--tolerance", type=int, default=60)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "assets")
    args = ap.parse_args()
    bg = tuple(int(args.bg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    img = square_crop(remove_background(Image.open(args.src), bg, args.tolerance))  # type: ignore[arg-type]
    args.out.mkdir(parents=True, exist_ok=True)
    for size in (1024, 512):
        img.resize((size, size), Image.Resampling.LANCZOS).save(args.out / f"logo-{size}.png", optimize=True)
    img.save(args.out / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
