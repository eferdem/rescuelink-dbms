#!/usr/bin/env python3
"""
Splits a very tall screenshot into two readable halves at a horizontal
whitespace band, so the report PDF does not have to shrink a 3100 px page
down to an unreadable strip.

Usage:  py -3.11 scripts/split_shots.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"

TARGETS = [
    ("10_transaction_commit.png", "10a_transaction_trace.png", "10b_transaction_proof.png"),
    ("11_transaction_rollback.png", "11a_rollback_trace.png", "11b_rollback_proof.png"),
]


def background_rows(img: Image.Image) -> list[int]:
    """Rows that contain a single flat colour across the content width."""
    px = img.convert("RGB").load()
    width, height = img.size
    xs = range(60, width - 60, 7)
    rows: list[int] = []
    for y in range(height):
        first = px[60, y]
        if all(px[x, y] == first for x in xs):
            rows.append(y)
    return rows


def last_content_row(img: Image.Image) -> int:
    px = img.convert("RGB").load()
    width, height = img.size
    xs = range(60, width - 60, 7)
    last = 0
    for y in range(height):
        first = px[60, y]
        if not all(px[x, y] == first for x in xs):
            last = y
    return last


def main() -> int:
    for source, top_name, bottom_name in TARGETS:
        path = SHOTS / source
        if not path.exists():
            print(f"  missing {source}")
            continue
        img = Image.open(path)
        width, height = img.size
        flat = set(background_rows(img))

        # longest run of flat rows whose midpoint sits between 45 % and 78 %
        best = None
        run_start = None
        for y in range(height + 1):
            if y < height and y in flat:
                if run_start is None:
                    run_start = y
                continue
            if run_start is not None:
                length = y - run_start
                mid = run_start + length // 2
                if length >= 8 and 0.45 * height <= mid <= 0.78 * height:
                    if best is None or length > best[0]:
                        best = (length, mid)
                run_start = None

        if best is None:
            cut = int(height * 0.6)
        else:
            cut = best[1]

        img.crop((0, 0, width, cut)).save(SHOTS / top_name, optimize=True)
        bottom = img.crop((0, cut, width, height))
        bottom = bottom.crop((0, 0, width, min(bottom.size[1], last_content_row(bottom) + 60)))
        bottom.save(SHOTS / bottom_name, optimize=True)
        print(f"  {source}: {height}px -> cut at {cut}px "
              f"({top_name}, {bottom_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
