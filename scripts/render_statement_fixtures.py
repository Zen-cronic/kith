"""Render the statement fixtures: fixtures/images/<id>.truth.json -> fixtures/images/<id>.png.

Each truth file names the issuer, every line printed on the document, and the amounts and dates a perfect reader
would report, so extraction is scored exactly (household.intake.score_extraction, tests/test_intake.py). Rendering
is deterministic: Pillow's bundled default font at fixed sizes, fixed geometry, no timestamps, so re-running this
script on the same Pillow leaves git clean. Lines listed under injection_lines are printed small and grey, the way a
planted instruction would sit on a real notice.

    python scripts/render_statement_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "fixtures" / "images"

WIDTH = 1000
MARGIN = 56
TITLE_SIZE = 34
BODY_SIZE = 22
INJECTION_SIZE = 15
LINE_HEIGHT = 40
TITLE_BLOCK = 78
INK = (24, 24, 24)
GREY = (176, 176, 176)
RULE = (200, 200, 200)
PAPER = (255, 255, 255)


def render(truth: dict) -> Image.Image:
    lines: list[str] = truth["lines"]
    injected = set(truth.get("injection_lines", []))
    height = MARGIN + TITLE_BLOCK + LINE_HEIGHT * max(len(lines) - 1, 0) + MARGIN
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=TITLE_SIZE)
    body_font = ImageFont.load_default(size=BODY_SIZE)
    small_font = ImageFont.load_default(size=INJECTION_SIZE)
    draw.text((MARGIN, MARGIN), lines[0], fill=INK, font=title_font)
    draw.line([(MARGIN, MARGIN + TITLE_SIZE + 14), (WIDTH - MARGIN, MARGIN + TITLE_SIZE + 14)], fill=RULE, width=2)
    y = MARGIN + TITLE_BLOCK
    for line in lines[1:]:
        if line in injected:
            draw.text((MARGIN, y + 6), line, fill=GREY, font=small_font)
        else:
            draw.text((MARGIN, y), line, fill=INK, font=body_font)
        y += LINE_HEIGHT
    return image


def main() -> int:
    truths = sorted(IMAGES.glob("*.truth.json"))
    if not truths:
        print(f"no truth files under {IMAGES}", file=sys.stderr)
        return 1
    for path in truths:
        truth = json.loads(path.read_text(encoding="utf-8"))
        image_id = path.name[: -len(".truth.json")]
        if truth["id"] != image_id:
            raise ValueError(f"truth id {truth['id']!r} must match file name {path.name}")
        image = render(truth)
        target = IMAGES / f"{image_id}.png"
        image.save(target, format="PNG", optimize=False)
        print(f"{target.relative_to(ROOT)}  {image.width}x{image.height}  {target.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
