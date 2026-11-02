"""
Creatures stand on the ground: each frame's feet on its bottom row, and checks that a sprite's frames agree.

The client (meridian-browser packages/render/src/sprites.ts) rests a bitmap's bottom row on the floor,
moved down by yoff * 4 fine units, and centres it on the object, moved left by xoff fine units. A bitmap
pixel is 16 / shrink fine units. Classic creatures (mummy, lich) have the feet on the bottom row and yoff 0.

    feet     the lowest row at least FOOT of the frame's widest row, so a thin sword tip or tail doesn't count
    seat     cut the empty rows under the lowest pixel and set yoff so the feet go SINK fine units into the
             floor; anything under the feet (a sword tip) goes into the floor with them
    check    feet off the ground, and frames drawn at a different size from the rest of the sprite
    sheet    every pose (row) by angle (column), placed as the client places it, on a red ground line
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from bgf import BGF, TRANSPARENT, Bitmap

FOOT = 0.12        # share of the widest row that a row needs to count as feet
FOOT_BAND = 0.08   # share of the creature's height, up from the feet, whose centre is where it stands
SINK = 4           # fine units the feet go into the floor
LIFT_MAX = 8       # check: feet further than this from the ground, in fine units
SIZE_SPREAD = 0.15  # check: a frame this much bigger or smaller than the sprite's median scale


def solid(bm: Bitmap) -> np.ndarray:
    return np.frombuffer(bytes(bm.pixels), np.uint8).reshape(bm.h, bm.w) != TRANSPARENT


def feet_row(a: np.ndarray) -> int | None:
    """The feet's row in a boolean mask (or alpha > 0), None when it's empty."""
    rows = a.sum(1)
    if not rows.any():
        return None
    return int(np.nonzero(rows >= FOOT * rows.max())[0].max())


def feet_x(a: np.ndarray) -> float | None:
    """Where the creature stands across the mask: the centre of the band just above the feet."""
    foot = feet_row(a)
    if foot is None:
        return None
    ys = np.nonzero(a.any(1))[0]
    band = max(1, round(FOOT_BAND * (foot - ys.min() + 1)))
    xs = np.nonzero(a[foot - band + 1:foot + 1].any(0))[0]
    return (xs.min() + xs.max() + 1) / 2


def lift(bm: Bitmap, shrink: int) -> float | None:
    """Fine units from the ground up to the feet, as the client draws the frame (negative: in the floor)."""
    foot = feet_row(solid(bm))
    return None if foot is None else (bm.h - 1 - foot) * 16 / shrink - bm.yoff * 4


def seat_bitmap(bm: Bitmap, shrink: int) -> Bitmap:
    a = solid(bm)
    foot = feet_row(a)
    if foot is None:
        return bm
    low = int(np.nonzero(a.any(1))[0].max())
    px = np.frombuffer(bytes(bm.pixels), np.uint8).reshape(bm.h, bm.w)[:low + 1]
    below = (low - foot) * 16 / shrink  # fine units of the frame under the feet
    yoff = round((below + SINK) / 4)
    # hotspots count from the top-left, so cutting rows off the bottom leaves them where they were
    return Bitmap(bm.w, low + 1, bm.xoff, yoff, [dict(h) for h in bm.hotspots], px.tobytes())


def seat(b: BGF) -> BGF:
    """The sprite with every frame's feet on the ground. Nothing is redrawn or scaled."""
    out = BGF(name=b.name, shrink=b.shrink, groups=[list(g) for g in b.groups],
              bitmaps=[seat_bitmap(bm, b.shrink) for bm in b.bitmaps])
    if hasattr(b, "missing"):
        out.missing = b.missing
    return out


def _area(bm: Bitmap, shrink: int) -> float:
    return float(solid(bm).sum()) * (16 / shrink) ** 2


def check(b: BGF, original: BGF | None = None) -> list:
    """Problems a reviewer should know about, as sentences. With the original, also each frame's size
    against it: the ratio of their areas should be about the same for every frame of the sprite."""
    out = []
    lifts = [(i, lift(bm, b.shrink)) for i, bm in enumerate(b.bitmaps)]
    off = [(i, v) for i, v in lifts if v is not None and abs(v + SINK) > LIFT_MAX]
    if off:
        worst = max(off, key=lambda t: abs(t[1]))
        out.append("%d of %d frames have their feet off the ground (worst: frame %d, %+.0f fine units)"
                   % (len(off), len(b.bitmaps), worst[0], worst[1]))
    if original is not None and len(original.bitmaps) == len(b.bitmaps):
        ratios = {}
        for i, (bm, ob) in enumerate(zip(b.bitmaps, original.bitmaps)):
            na, oa = _area(bm, b.shrink), _area(ob, original.shrink)
            if na and oa:
                ratios[i] = (na / oa) ** 0.5
        if ratios:
            med = float(np.median(list(ratios.values())))
            odd = {i: r / med for i, r in ratios.items() if abs(r / med - 1) > SIZE_SPREAD}
            if odd:
                worst = max(odd, key=lambda i: abs(odd[i] - 1))
                out.append("%d of %d frames are drawn at a different size from the rest (worst: frame %d, %+.0f%%)"
                           % (len(odd), len(ratios), worst, (odd[worst] - 1) * 100))
    return out


def sheet(b: BGF, pal, label: str = "", scale: float = 0.22, ground: tuple = (200, 60, 60)) -> Image.Image:
    """Every pose (row) by angle (column), each frame where the client puts it, on a ground line.
    scale: screen pixels per fine unit; 0.22 shows a man-sized creature about 180 px tall."""
    groups = b.groups or [[i] for i in range(len(b.bitmaps))]
    cols = max(len(g) for g in groups)
    fx = 16 / b.shrink
    # the cell fits the widest and tallest frame as drawn
    left = min(-8 * bm.w / b.shrink - bm.xoff for bm in b.bitmaps)
    right = max(8 * bm.w / b.shrink - bm.xoff for bm in b.bitmaps)
    top = max(16 * bm.h / b.shrink - bm.yoff * 4 for bm in b.bitmaps)
    bottom = max(bm.yoff * 4 for bm in b.bitmaps) + 8
    half = max(-left, right)
    cw, above, below, head = int(2 * half * scale) + 8, int(top * scale) + 14, int(bottom * scale) + 4, 16
    ch = above + below
    im = Image.new("RGB", (cols * cw, head + len(groups) * ch), (38, 40, 48))
    d = ImageDraw.Draw(im)
    d.text((4, 2), ("%s  " % label if label else "") + "shrink %d" % b.shrink, fill=(230, 230, 230))
    for r, g in enumerate(groups):
        gy = head + r * ch + above
        d.line([(0, gy), (im.width, gy)], fill=ground)
        for c, i in enumerate(g):
            bm = b.bitmaps[i]
            w, h = max(1, round(bm.w * fx * scale)), max(1, round(bm.h * fx * scale))
            spr = bm.rgba(pal).resize((w, h), Image.NEAREST if fx * scale >= 1 else Image.BOX)
            x = c * cw + cw // 2 + round((-8 * bm.w / b.shrink - bm.xoff) * scale)
            y = gy - round((16 * bm.h / b.shrink - bm.yoff * 4) * scale)
            im.paste(spr, (x, y), spr)
            d.text((c * cw + 3, head + r * ch + 2), "#%d" % i, fill=(140, 140, 150))
    return im
