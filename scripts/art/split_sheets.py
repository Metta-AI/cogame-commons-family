#!/usr/bin/env python3
"""Turn the nano-banana source sheets into the sprites the viewer loads.

Gemini does not return alpha, and the "pure magenta" backdrop comes back as
*some* magenta with a tinted edge, so the key is a flood fill from the image
border (green/red accents inside a character survive) against the MEDIAN border
colour — corners sometimes carry a smudge.

A flood fill cannot reach backdrop that a figure encloses (the gap between a
cog's arm and its body), so a second pass clears any pixel still within a
tighter tolerance of the backdrop colour.

The row is then split at its THINNEST columns. Splitting on strictly empty
columns fails on the cog sheet, where neighbouring arms touch; instead the
script looks near each evenly-spaced boundary and cuts at the column with the
fewest opaque pixels, which is the gap between two figures.

    python3 scripts/art/split_sheets.py

Owns: data/cog_{red,blue,green,yellow,violet,orange}.png,
      data/{apple,mushroom_red,mushroom_green,mushroom_blue}.png.
Nothing else in data/ is generated — font.ttf and arena_floor.png are the
starter's and are not touched.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "art" / "source"
OUT = ROOT / "data"

SHEETS = [
    ("cogs_sheet.png", ["cog_red", "cog_blue", "cog_green", "cog_yellow", "cog_violet", "cog_orange"], 128),
    ("props_sheet.png", ["apple", "mushroom_red", "mushroom_green", "mushroom_blue"], 96),
]

TOLERANCE = 60
ENCLOSED_TOLERANCE = 64


def median_border(image: Image.Image) -> tuple[int, int, int]:
    width, height = image.size
    pixels = image.load()
    samples: list[tuple[int, int, int]] = []
    for x in range(0, width, max(1, width // 200)):
        samples.append(pixels[x, 0][:3])
        samples.append(pixels[x, height - 1][:3])
    for y in range(0, height, max(1, height // 200)):
        samples.append(pixels[0, y][:3])
        samples.append(pixels[width - 1, y][:3])
    channels = [sorted(sample[index] for sample in samples) for index in range(3)]
    mid = len(samples) // 2
    return (channels[0][mid], channels[1][mid], channels[2][mid])


def close_to(pixel, target, tolerance: int) -> bool:
    return all(abs(pixel[index] - target[index]) <= tolerance for index in range(3))


def key_out(image: Image.Image) -> Image.Image:
    """Flood-fill the backdrop from the border, leaving the figures opaque."""
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    target = median_border(image)

    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        for y in (0, height - 1):
            queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        index = y * width + x
        if seen[index]:
            continue
        if not close_to(pixels[x, y], target, TOLERANCE):
            continue
        seen[index] = 1
        pixels[x, y] = (0, 0, 0, 0)
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # Backdrop the fill could not reach: the holes a figure encloses.
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] and close_to(pixels[x, y], target, ENCLOSED_TOLERANCE):
                pixels[x, y] = (0, 0, 0, 0)
    return image


def content_box(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.getbbox()
    if box is None:
        raise SystemExit("the whole sheet was keyed out; loosen TOLERANCE")
    return box


def column_weights(image: Image.Image, box: tuple[int, int, int, int]) -> list[int]:
    left, top, right, bottom = box
    alpha = image.split()[3].load()
    return [
        sum(1 for y in range(top, bottom) if alpha[x, y] > 24) for x in range(left, right)
    ]


def split_columns(
    image: Image.Image, box: tuple[int, int, int, int], parts: int
) -> list[tuple[int, int]]:
    """Cut the row at its thinnest columns near each evenly-spaced boundary."""
    left, _, right, _ = box
    weights = column_weights(image, box)
    span = (right - left) / parts
    window = max(4, int(span * 0.18))
    cuts = [left]
    for index in range(1, parts):
        centre = int(left + index * span)
        lo = max(left + 1, centre - window)
        hi = min(right - 1, centre + window)
        best = min(range(lo, hi + 1), key=lambda x: (weights[x - left], abs(x - centre)))
        cuts.append(best)
    cuts.append(right)
    return list(zip(cuts[:-1], cuts[1:]))


def pad_square(part: Image.Image, size: int) -> Image.Image:
    box = part.getbbox()
    if box:
        part = part.crop(box)
    side = max(part.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(part, ((side - part.width) // 2, (side - part.height) // 2))
    return square.resize((size, size), Image.LANCZOS)


def split(sheet_name: str, names: list[str], size: int) -> None:
    path = SOURCE / sheet_name
    if not path.exists():
        raise SystemExit(f"missing source sheet {path}")
    image = key_out(Image.open(path))
    box = content_box(image)
    runs = split_columns(image, box, len(names))
    left, top, right, bottom = box
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (start, end) in zip(names, runs):
        part = image.crop((start, top, end, bottom))
        pad_square(part, size).save(OUT / f"{name}.png")
        print(f"  {name}.png  {size}x{size}  from x=[{start},{end})")


def main() -> int:
    for sheet_name, names, size in SHEETS:
        print(sheet_name)
        split(sheet_name, names, size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
