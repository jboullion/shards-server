"""
Animated sprites (monsters, NPCs): restyle a whole angle at once so animations stay consistent.

A creature BGF is a list of poses (groups), each listing one bitmap per viewing angle (8 slots; some
angles share a bitmap). One "sheet" is every pose of one angle laid out in a grid on magenta: the model
redraws the sheet in one request, so a walk cycle is drawn together and doesn't flicker. The result
is sliced back into cells and each frame is fitted into its original frame's box (pixelize), which
keeps every frame where the animation expects it. A one-bitmap sprite (a corpse) is one plain image.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from bgf import BGF, Bitmap
from pixelize import KEY_COLOR, pixelize

SHEET = 1024
FILL = 0.84  # of a cell: the margin keeps neighbouring frames apart


def plan(b: BGF) -> list:
    """The sheets for a sprite: lists of bitmap indices, one list per distinct angle, poses in order."""
    if len(b.bitmaps) == 1 or not b.groups:
        return [[0]] if len(b.bitmaps) == 1 else [[i] for i in range(len(b.bitmaps))]
    slots = max(len(g) for g in b.groups)
    sheets, seen = [], set()
    for a in range(slots):
        frames = []
        for g in b.groups:
            if a < len(g) and g[a] not in frames:
                frames.append(g[a])
        key = tuple(frames)
        if frames and key not in seen:
            seen.add(key)
            sheets.append(frames)
    # bitmaps in no group (rare) get a sheet of their own
    covered = {i for s in sheets for i in s}
    rest = [i for i in range(len(b.bitmaps)) if i not in covered]
    if rest:
        sheets.append(rest)
    return sheets


def layout(n: int, aspect: float) -> tuple:
    """(cols, rows) for n cells of the given width/height aspect on a square sheet."""
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cw, ch = SHEET / cols, SHEET / rows
        scale = min(cw / aspect, ch)  # the frame height that fits a cell
        if best is None or scale > best[0]:
            best = (scale, cols, rows)
    return best[1], best[2]


def cells(n: int, aspect: float) -> list:
    cols, rows = layout(n, aspect)
    cw, ch = SHEET // cols, SHEET // rows
    return [((i % cols) * cw, (i // cols) * ch, (i % cols + 1) * cw, (i // cols + 1) * ch) for i in range(n)]


def compose(b: BGF, frames: list, pal) -> Image.Image:
    """The frames on one magenta sheet, all at one scale so the creature keeps its size between poses."""
    imgs = [b.bitmaps[i].rgba(pal) for i in frames]
    w = max(im.width for im in imgs)
    h = max(im.height for im in imgs)
    if len(frames) == 1:
        rects = [(0, 0, SHEET, SHEET)]
    else:
        rects = cells(len(frames), w / h)
    cw, ch = rects[0][2] - rects[0][0], rects[0][3] - rects[0][1]
    s = FILL * min(cw / w, ch / h)
    sheet = Image.new("RGBA", (SHEET, SHEET), KEY_COLOR + (255,))
    for im, (x0, y0, x1, y1) in zip(imgs, rects):
        # every frame scaled by the same factor and placed by its own box, so relative sizes hold
        t = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        sheet.alpha_composite(t, (x0 + (cw - t.width) // 2, y0 + (ch - t.height) // 2))
    return sheet.convert("RGB")


def slice_back(gen: Image.Image, b: BGF, frames: list) -> list:
    """The generated sheet cut into the same cells it was composed with."""
    gen = gen.convert("RGB").resize((SHEET, SHEET), Image.LANCZOS)
    if len(frames) == 1:
        return [gen]
    w = max(b.bitmaps[i].w for i in frames)
    h = max(b.bitmaps[i].h for i in frames)
    return [gen.crop(r) for r in cells(len(frames), w / h)]


def _many_rects(items: list) -> list:
    aspect = sum(b.bitmaps[f[0]].w / max(1, b.bitmaps[f[0]].h) for b, f in items) / len(items)
    return [(0, 0, SHEET, SHEET)] if len(items) == 1 else cells(len(items), aspect)


def compose_many(items: list, pal) -> Image.Image:
    """Several different single-image creatures (e.g. corpses) on one sheet, each fitted to its own cell.
    items: [(bgf, [bitmap index])]."""
    sheet = Image.new("RGBA", (SHEET, SHEET), KEY_COLOR + (255,))
    for (b, frames), (x0, y0, x1, y1) in zip(items, _many_rects(items)):
        im = b.bitmaps[frames[0]].rgba(pal)
        s = FILL * min((x1 - x0) / im.width, (y1 - y0) / im.height)
        t = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        sheet.alpha_composite(t, (x0 + (x1 - x0 - t.width) // 2, y0 + (y1 - y0 - t.height) // 2))
    return sheet.convert("RGB")


def slice_many(gen: Image.Image, items: list) -> list:
    gen = gen.convert("RGB").resize((SHEET, SHEET), Image.LANCZOS)
    return [gen.crop(r) for r in _many_rects(items)]


def rebuild(b: BGF, sheets: list, results: list, pal) -> BGF:
    """A new BGF: each frame pixelized from its cell into its original box (same size, offsets, hotspots)."""
    new = {}
    for frames, gen in zip(sheets, results):
        for i, cell in zip(frames, slice_back(gen, b, frames)):
            new[i] = pixelize(cell, b.bitmaps[i], b.shrink, b.shrink, pal)
    bitmaps = [new.get(i) or b.bitmaps[i] for i in range(len(b.bitmaps))]
    return BGF(name=b.name, shrink=b.shrink, groups=[list(g) for g in b.groups], bitmaps=bitmaps)


def animation_gif(b: BGF, pal, path: Path, slot: int = 0, ms: int = 140, scale: int = 2):
    """One angle's poses in order as an animated GIF (frames placed by their offsets)."""
    if not b.groups:
        return
    seq = [g[slot] for g in b.groups if slot < len(g)]
    w = max(b.bitmaps[i].w + abs(b.bitmaps[i].xoff) for i in seq)
    h = max(b.bitmaps[i].h + abs(b.bitmaps[i].yoff) for i in seq)
    frames = []
    for i in seq:
        bm = b.bitmaps[i]
        canvas = Image.new("RGBA", (w, h), (40, 40, 48, 255))
        canvas.alpha_composite(bm.rgba(pal), ((w - bm.w) // 2, h - bm.h))
        frames.append(canvas.resize((w * scale // 2, h * scale // 2), Image.NEAREST).convert("RGB"))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0)
