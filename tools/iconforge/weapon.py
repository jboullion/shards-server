"""
One drawing of a weapon, posed into every frame of its sprites, so the icon, the weapon in the
player's hand and the first-person view all show the same object:

    python tools/iconforge/weapon.py masterwand            (cached drawing; --force asks again)
    python tools/iconforge/weapon.py masterwand --install  (copies the BGFs to resource/graphics/shards)

The weapons and their sprites are listed in data/iconforge/weapons.json.

1. Draw: the current icon, stood upright on magenta, goes to the model with the style block and the
   weapon's description; it returns one large upright drawing (raw.png, cached).
2. Measure: in every frame of the original sprites, the weapon's axis (the principal axis of its
   pixels), its two ends, which end is the head (the wider one) and how wide the head is.
3. Pose: the drawing is scaled, rotated and placed so its head and butt land on those two ends,
   drawn 4x finer and area-averaged down, then mapped to the palette:
   - `icon`: like `held` with the frame's own length (icons aren't foreshortened). A `broken` frame
     shows it in three pieces.
   - `held` (the overlay in the player's hand): the shaft keeps one width for every frame (`shaft`
     times the drawing's own proportion) and only shortens, and the head is drawn as wide as the
     original's, so a weapon pointing at the viewer is a short shaft behind a big head, not a
     squashed one. `head_max` caps the head's scale at that many times the shaft's (2.5).
   - `pov` (first person): a close-up, the head as wide as the original's and the shaft running off
     the edge. In `cut` frames the head is out of the picture: it sits just past the far end, at the
     last frame's scale. `sink` pushes the head that share of its length past its end in the others.
     `fit` lists frames that show the whole weapon: each is scaled so it reaches the frame's far end
     (from the sunk head), rather than drawing its head as wide as the original's, and the other
     frames take the `scale_from` frame's scale. `scale` multiplies them all. `grow` lets a frame
     grow up and left to hold it (first-person frames hang from the bottom right corner, so they
     stay put).
4. Colour: the head's neutral greys (`tint`) go to the grey ramp D0-DF, which the game recolours
   per school of magic (basewand.kod ResetColors, the same xlat on all three sprites); everything
   else to the nearest palette colour outside that ramp, so it never changes colour.

Overlay offsets are in the player body's pixels (shrink 4, bta.bgf): a `held` sprite can change shrink
(its box scales with it and the offsets stay) and gets `pad` pixels of room on every side, which moves
its offsets by pad * 4 / shrink, so pad is a multiple of shrink / gcd(shrink, 4).

Outputs go to build/iconforge/weapon/<name>/: ref.png, raw.png, request.json, the BGFs, and
preview.png (each sprite's old frames, its new ones, then the new ones in each school's colour).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers  # noqa: E402
from bgf import BGF, Bitmap, TRANSPARENT, load_palette  # noqa: E402
from forge import style_text  # noqa: E402
from inventory import SERVED, SHARDS_DIR  # noqa: E402
from pixelize import ALPHA_CUT, KEY_COLOR, clean_edges, matte, to_palette  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SPEC = REPO / "data" / "iconforge" / "weapons.json"
OUT = REPO / "build" / "iconforge" / "weapon"
KEPT = REPO / "build" / "iconforge" / "kept"
BROWSER = Path(os.environ.get("MERIDIAN_BROWSER", r"E:\2026_Experiments\meridian-browser"))

BODY_SHRINK = 4  # the player body (bta.bgf) that held weapons hang from
SS = 4  # supersampling
GROW = 256  # how far a pov frame may grow up and left (`grow`), in its pixels
GREY = list(range(0xD0, 0xE0))
# basewand.kod viPT_*_master: Faren, Kraanan, Shal'ille, Qor, Riija
SCHOOLS = [("Faren", 0x21), ("Kraanan", 0x20), ("Shal'ille", 0x30), ("Qor", 0x2C), ("Riija", 0x18)]

PROMPT = (
    "The image shows {what} from a 1996 fantasy RPG, stood upright. Redraw this same {what} as one "
    "large sprite, perfectly straight and vertical: {head} at the top, the end of the handle at the "
    "bottom, seen from the side with no perspective, centred and reaching from near the top of the "
    "image to near the bottom without touching the edges. {describe} Draw {head} in pure neutral "
    "greys only (white highlight, light, mid and dark greys, no tint of any colour at all): the game "
    "recolours it for each school of magic. Everything else keeps its own colours, with no grey. {style}"
)
# A weapon in fixed colours (no school tint) whose drawing is posed upside down (`flip`): a sword's
# wider end, which the frames' measures call the head, is its hilt, but the model draws it tip up.
PROMPT_FIXED = (
    "The image shows {what} from a 1996 fantasy RPG, stood upright. Redraw this {what} as one large "
    "sprite, perfectly straight and vertical: {upright}, seen from the side with no perspective, centred "
    "and reaching from near the top of the image to near the bottom without touching the edges. "
    "{describe} {style}"
)


# ------------------------------------------------------------------------------------- files

def served(name: str) -> Path:
    """The copy the game shows now (our restyle first)."""
    for d in [SHARDS_DIR, *SERVED]:
        if (d / name).exists():
            return d / name
    raise SystemExit("no %s" % name)


def original(name: str) -> Path:
    """The copy whose layout (frames, boxes, offsets) the new sprite keeps."""
    for d in SERVED:
        if (d / name).exists():
            return d / name
    raise SystemExit("no original %s" % name)


# ------------------------------------------------------------------------------------- measure

def axis_of(solid: np.ndarray) -> dict:
    """The principal axis of the solid pixels: head and butt ends (x, y), length, and the head's width.
    The head is the wider end (a grey-ramp test gets the master wand's trim wrong)."""
    ys, xs = np.nonzero(solid)
    p = np.stack([xs, ys], 1).astype(float) + 0.5
    c = p.mean(0)
    u = np.linalg.svd(p - c, full_matrices=False)[2][0]
    t = (p - c) @ u
    n = (p - c) @ np.array([-u[1], u[0]])
    lo, hi = t.min(), t.max()
    k = max(2.0, 0.2 * (hi - lo))

    def width(sel):
        return n[sel].max() - n[sel].min() + 1 if sel.any() else 0

    head_hi = width(t > hi - k) >= width(t < lo + k)
    a, b = c + lo * u, c + hi * u
    head, butt = (b, a) if head_hi else (a, b)
    head_w = width(t > hi - k) if head_hi else width(t < lo + k)
    return {"head": head, "butt": butt, "length": float(hi - lo), "head_width": float(head_w)}


def frame_pose(bm: Bitmap) -> dict:
    return axis_of(bm.indices() != TRANSPARENT)


# ------------------------------------------------------------------------------------- draw

def upright(rgba: np.ndarray, head_up_from: dict, resample=Image.BICUBIC) -> Image.Image:
    """The image rotated so its axis runs straight up from butt to head, cropped to the weapon."""
    v = head_up_from["head"] - head_up_from["butt"]
    theta = math.degrees(math.atan2(-v[1], v[0]))  # y is down
    im = Image.fromarray(rgba, "RGBA").rotate(90 - theta, resample=resample, expand=True)
    return im.crop(im.getbbox())


def reference(spec: dict, pal) -> Image.Image:
    name, frame = spec["ref"].split(":")
    bm = BGF.read(served(name)).bitmaps[int(frame)]
    im = upright(np.asarray(bm.rgba(pal)), frame_pose(bm), Image.NEAREST)
    if spec.get("flip"):
        im = im.rotate(180)
    s = 0.86 * 1024 / im.height
    im = im.resize((max(1, round(im.width * s)), round(im.height * s)), Image.NEAREST)
    canvas = Image.new("RGBA", (1024, 1024), KEY_COLOR + (255,))
    canvas.alpha_composite(im, ((1024 - im.width) // 2, (1024 - im.height) // 2))
    return canvas.convert("RGB")


class Drawing:
    """The model's drawing, upright and matted: premultiplied RGBA floats, the tint mask, the axis column."""

    def __init__(self, raw: Image.Image, spec: dict):
        if spec.get("flip"):  # drawn tip up: its head (a sword's hilt) goes to the top
            raw = raw.rotate(180)
        rgba = clean_edges(np.asarray(matte(raw, KEY_COLOR)), KEY_COLOR)
        solid = rgba[..., 3] >= ALPHA_CUT
        im = upright(rgba, self._up(solid))
        a = np.asarray(im).astype(np.float32) / 255.0
        self.h, self.w = a.shape[:2]
        alpha = a[..., 3:4]
        rgb = a[..., :3]
        mx, mn = rgb.max(-1), rgb.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1e-3)
        rows = np.arange(self.h)[:, None] < spec.get("tint_rows", 0.25) * self.h
        tint = (sat < spec.get("tint_sat", 0.16)) & rows & (alpha[..., 0] > 0.5)
        self.tint_frac = float(tint.sum() / max(1, (alpha[..., 0] > 0.5).sum()))
        lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        self.tint_range = tuple(np.percentile(lum[tint], [2, 98])) if tint.any() else (0.0, 1.0)
        # premultiplied, plus the tint mask as a fifth channel (also premultiplied)
        self.px = np.concatenate([rgb * alpha, alpha, tint[..., None] * alpha], -1)
        solid = alpha[..., 0] > 0.5
        xs = np.nonzero(solid)[1]
        self.cx = float(xs.mean()) if len(xs) else self.w / 2
        self.head_rows = int(spec.get("head_rows", 0.2) * self.h)
        cols = np.nonzero(solid[:self.head_rows].any(0))[0]
        self.head_w = float(cols.max() - cols.min() + 1) if len(cols) else self.w

    @staticmethod
    def _up(solid):
        pose = axis_of(solid)
        # the model drew it head up; the axis only straightens it
        if pose["head"][1] > pose["butt"][1]:
            pose["head"], pose["butt"] = pose["butt"], pose["head"]
        return pose

    def piece(self, y0: int, y1: int) -> np.ndarray:
        return self.px[y0:y1]


def _scaled(arr: np.ndarray, sx: float, sy: float) -> np.ndarray:
    """Each channel resized by (sx, sy) with area averaging (Pillow 'F' images keep the floats)."""
    h, w = arr.shape[:2]
    nw, nh = max(1, round(w * sx)), max(1, round(h * sy))
    return np.stack([np.asarray(Image.fromarray(arr[..., c], "F").resize((nw, nh), Image.BOX if sx < 1 else Image.BICUBIC))
                     for c in range(arr.shape[2])], -1)


def _place(canvas: np.ndarray, arr: np.ndarray, M_cols: tuple, c: np.ndarray):
    """Paint `arr` (premultiplied) over `canvas` with target = M @ (a, b) + c, M orthonormal."""
    n, mu = M_cols  # columns: where the drawing's x and y axes go
    H, W = canvas.shape[:2]
    data = (n[0], n[1], -float(n @ c), mu[0], mu[1], -float(mu @ c))
    layers = [np.asarray(Image.fromarray(arr[..., ch], "F").transform((W, H), Image.AFFINE, data, Image.BICUBIC))
              for ch in range(arr.shape[2])]
    src = np.stack(layers, -1)
    a = src[..., 3:4].clip(0, 1)  # bicubic overshoots: keep premultiplied colour within alpha
    src = np.concatenate([src[..., :3].clip(0, a), a, src[..., 4:].clip(0, a)], -1)
    canvas[:] = src + canvas * (1 - a)


def render(d: Drawing, size: tuple, pieces: list) -> np.ndarray:
    """pieces: [(y0, y1, sx, sy, target point of the piece's top on the axis (x, y), unit head direction u)]
    in target pixels. Returns premultiplied (h, w, 5) floats at the target size."""
    W, H = size
    canvas = np.zeros((H * SS, W * SS, 5), np.float32)
    for y0, y1, sx, sy, top, u in pieces:
        if y1 <= y0 or sy <= 0:
            continue
        arr = _scaled(d.piece(y0, y1), sx * SS, sy * SS)
        u = np.asarray(u, float)
        n = np.array([-u[1], u[0]])  # where the drawing's x goes; its y (down the shaft) goes to -u
        c = np.asarray(top, float) * SS - n * d.cx * arr.shape[1] / d.w
        _place(canvas, arr, (n, -u), c)
    return canvas.reshape(H, SS, W, SS, 5).mean((1, 3))


def to_indices(d: Drawing, px: np.ndarray, pal) -> np.ndarray:
    a = px[..., 3]
    solid = a >= ALPHA_CUT / 255.0
    rgb = np.where(a[..., None] > 0, px[..., :3] / np.maximum(a[..., None], 1e-6), 0)
    rgba = np.dstack([(rgb.clip(0, 1) * 255).astype(np.uint8), np.where(solid, 255, 0).astype(np.uint8)])
    out = to_palette(rgba, pal, allowed=[i for i in range(256) if i not in GREY])
    tint = solid & (px[..., 4] / np.maximum(a, 1e-6) > 0.5)
    if tint.any():
        lo, hi = d.tint_range
        lum = rgb @ np.array([0.299, 0.587, 0.114])
        v = np.clip((lum - lo) / max(1e-3, hi - lo), 0, 1) * (225 - 30) + 30
        greys = np.array([pal[i][0] for i in GREY], np.float32)
        g = np.array(GREY, np.uint8)[np.abs(v[..., None] - greys).argmin(-1)]
        out[tint] = g[tint]
    return out


# ------------------------------------------------------------------------------------- pose

def broken_pieces(d: Drawing, head, butt, wide: float = 1.0) -> list:
    """The drawing between `head` and `butt` in three pieces with gaps, the middle one knocked aside;
    `wide` thickens it like a shaft."""
    head, butt = np.asarray(head, float), np.asarray(butt, float)
    v = head - butt
    L = float(np.hypot(*v)) or 1.0
    u = v / L
    n = np.array([-u[1], u[0]])
    cut = [0, d.head_rows, int(d.head_rows + 0.45 * (d.h - d.head_rows)), d.h]
    gap = 0.07 * L
    s = (L - 2 * gap) / d.h  # the gaps fit between the same two ends
    shift = [n * 0, n * 0.05 * L - u * gap, -u * 2 * gap]
    return [(cut[i], cut[i + 1], s * wide, s, head - u * cut[i] * s + shift[i], u) for i in (2, 1, 0)]


def held_pieces(d: Drawing, head, butt, sw: float, sh: float) -> list:
    """The shaft at width scale sw, shortened to fit; the head whole at scale sh."""
    head, butt = np.asarray(head, float), np.asarray(butt, float)
    v = head - butt
    L = float(np.hypot(*v)) or 1.0
    u = v / L
    hl = d.head_rows * sh
    shaft = max(0.0, L - hl)
    sa = shaft / max(1, d.h - d.head_rows)
    return [(d.head_rows, d.h, sw, sa, head - u * hl, u), (0, d.head_rows, sh, sh, head, u)]


def build_sprite(d: Drawing, sp: dict, pal) -> BGF:
    orig = BGF.read(original(sp["file"]))
    kind = sp["kind"]
    shrink = sp.get("shrink", orig.shrink)
    f = shrink / orig.shrink
    pad = sp.get("pad", 0)
    shift = pad * BODY_SHRINK / shrink
    if kind == "held" and shift != int(shift):
        raise SystemExit("%s: pad %d moves the offsets by %.2f body pixels" % (sp["file"], pad, shift))
    poses = [frame_pose(bm) for bm in orig.bitmaps]
    for i in sp.get("cut", []):  # no head to find: it's off the top (or the bottom, `cut_head`)
        p = poses[i]
        if (p["head"][1] > p["butt"][1]) == (sp.get("cut_head", "top") == "top"):
            p["head"], p["butt"] = p["butt"], p["head"]
    for p in poses:
        p["head"] = p["head"] * f + pad
        p["butt"] = p["butt"] * f + pad
        p["length"] *= f
    s_full = max(p["length"] for p in poses) / d.h
    sink = sp.get("sink", 0)
    for p in poses:  # the scale that draws the head as wide as the original's
        p["s_head"] = p["head_width"] * f / d.head_w
    fit = sp.get("fit")
    for i, p in enumerate(poses):
        # `fit` (pov): frames that show the whole weapon take the scale that reaches the frame's far
        # end from the sunk head; the others that of the `scale_from` frame
        p["s_pov"] = p["length"] / (d.h - sink * d.head_rows) if fit is not None and i in fit else p["s_head"]
        p["s_pov"] *= sp.get("scale", 1.0)  # and then drawn this much bigger or smaller
    s_ref = poses[sp.get("scale_from", len(poses) - 1)]["s_pov" if kind == "pov" else "s_head"]
    if fit is not None:
        for i, p in enumerate(poses):
            if i not in fit:
                p["s_pov"] = s_ref
    sw = s_full * sp.get("shaft", 1.0)  # a shaft can be drawn thicker than the drawing's proportion
    bitmaps = []
    for i, (bm, p) in enumerate(zip(orig.bitmaps, poses)):
        w, h = round(bm.w * f) + 2 * pad, round(bm.h * f) + 2 * pad
        grow = GROW if kind == "pov" and sp.get("grow") else 0
        if kind == "icon" and i in sp.get("broken", []):
            pieces = broken_pieces(d, p["head"], p["butt"], sp.get("shaft", 1.0))
        elif kind in ("held", "icon"):
            # the head as big as the original's (closer when it points at the viewer), never smaller
            # than the shaft's scale. An icon is never foreshortened: its own length sets the scale
            s = s_full if kind == "held" else p["length"] / d.h
            thick = sw if kind == "held" else s * sp.get("shaft", 1.0)
            pieces = held_pieces(d, p["head"], p["butt"], thick, float(np.clip(p["s_head"], thick, sp.get("head_max", 2.5) * s)))
        elif kind == "pov":
            # a close-up: the head as big as the original's, the shaft running off the edge. In a cut
            # frame the head is just past the far end, at the held frame's scale; otherwise `sink` of it
            # runs off past its end (a sword's grip below the screen, its crossguard showing)
            v = p["head"] - p["butt"]
            u = v / (float(np.hypot(*v)) or 1.0)
            cut = i in sp.get("cut", [])
            s = s_ref if cut else p["s_pov"]
            tip = p["head"] + u * d.head_rows * s * (1.1 if cut else sink) + grow
            pieces = [(0, d.h, s, s, tip, u)]
        else:
            raise SystemExit("%s: unknown kind %r" % (sp["file"], kind))
        idx = to_indices(d, render(d, (w + grow, h + grow), pieces), pal)
        if grow:
            # drawn with room above and to the left; the frame grows only that way, as far as the
            # weapon reaches: it hangs from the screen's bottom right corner (HS_SE)
            ys, xs = np.nonzero(idx != TRANSPARENT)
            idx = idx[min(grow, ys.min()):, min(grow, xs.min()):] if len(xs) else idx[grow:, grow:]
            h, w = idx.shape
        xoff, yoff = (bm.xoff - int(shift), bm.yoff - int(shift)) if kind == "held" else (bm.xoff, bm.yoff)
        bitmaps.append(Bitmap(w, h, xoff, yoff, list(bm.hotspots), idx.tobytes()))
    return BGF(name=orig.name, shrink=shrink, groups=[list(g) for g in orig.groups], bitmaps=bitmaps)


# ------------------------------------------------------------------------------------- preview

def xlats() -> dict:
    path = OUT / "xlats.json"
    if not path.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        subprocess.run(["node", str(HERE / "xlats.ts"), str(BROWSER).replace("\\", "/"), str(path),
                        *[str(x) for _, x in SCHOOLS]], check=True)
    return json.loads(path.read_text())


def preview(name: str, sprites: list, path: Path, schools: bool = True):
    """Each sprite at its in-game size (scaled by 1/shrink alike, so old and new compare): the old
    frames, the new ones, then (for a tinted weapon) the new ones' first frames in each school's colour."""
    X = xlats()
    rgb = np.array(X["rgb"], np.uint8).reshape(256, 3)
    rows = []
    for file, old, new in sprites:
        # display pixels per in-game unit: the finer version's pixels shown 1-3x, as size allows
        fine = min((old, new), key=lambda b: b.shrink)
        tall = max(bm.h for bm in fine.bitmaps)
        zoom = fine.shrink * (3 if tall < 60 else 2 if tall < 130 else 1)

        def frames(b, table=None, limit=None):
            out = []
            for bm in b.bitmaps[:limit]:
                idx = bm.indices()
                sol = idx != TRANSPARENT
                if not sol.any():
                    continue
                ys, xs = np.nonzero(sol)
                crop = idx[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                c = table[crop] if table is not None else crop
                im = Image.fromarray(np.dstack([rgb[c], np.where(crop == TRANSPARENT, 0, 255).astype(np.uint8)]), "RGBA")
                f = zoom / b.shrink
                out.append(im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.NEAREST))
            return out

        rows.append(("%s old" % file, frames(old)))
        rows.append(("%s new" % file, frames(new)))
        for school, x in SCHOOLS if schools else []:
            rows.append(("  %s" % school, frames(new, np.array(X[str(x)], np.uint8), 8)))
    W = 150 + max(sum(c.width + 4 for c in cells) for _, cells in rows)
    hs = [max((c.height for c in cells), default=10) + 6 for _, cells in rows]
    sheet = Image.new("RGBA", (W, sum(hs) + 6), (58, 58, 68, 255))
    dr = ImageDraw.Draw(sheet)
    y = 4
    for (label, cells), h in zip(rows, hs):
        dr.text((4, y + h // 2 - 6), label, fill="white")
        x = 150
        for c in cells:
            sheet.alpha_composite(c, (x, y + (h - c.height) // 2))
            x += c.width + 4
        y += h
    sheet.save(path)


# ------------------------------------------------------------------------------------- main

def build(name: str, force: bool) -> list:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))["weapons"][name]
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    pal = load_palette()
    raw = d / "raw.png"
    if force or not raw.exists():
        ref = reference(spec, pal)
        ref.save(d / "ref.png")
        style, _ = style_text()
        text = (PROMPT_FIXED if "upright" in spec else PROMPT).format(
            what=spec["what"], head=spec["head"], upright=spec.get("upright"), describe=spec["describe"], style=style)
        img, info = providers.run("openai", [ref], text)
        img.save(raw)
        (d / "request.json").write_text(json.dumps({"prompt": text, "usage": info.get("usage")}, indent=1))
    drawing = Drawing(Image.open(raw).convert("RGB"), spec)
    print("%s: drawing %dx%d, %.0f%% of it in the school colour, head %d rows by %d" % (
        name, drawing.w, drawing.h, 100 * drawing.tint_frac, drawing.head_rows, drawing.head_w))
    out = []
    for sp in spec["sprites"]:
        new = build_sprite(drawing, sp, pal)
        new.write(d / sp["file"])
        out.append((sp["file"], BGF.read(served(sp["file"])), new))
        print("  %s: %d frames (%s)" % (sp["file"], len(new.bitmaps), sp["kind"]))
    preview(name, out, d / "preview.png", schools=spec.get("tint_rows", 0.25) > 0)
    print("  preview:", d / "preview.png")
    return [d / sp["file"] for sp in spec["sprites"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--force", action="store_true", help="ask the model again")
    ap.add_argument("--install", action="store_true", help="copy the BGFs to resource/graphics/shards")
    a = ap.parse_args()
    for name in a.names:
        files = build(name, a.force)
        if a.install:
            for f in files:
                shutil.copy2(f, SHARDS_DIR / f.name)
                print("  installed", SHARDS_DIR / f.name)
                # forge.py install copies its kept icons over this folder: keep ours there too
                if (KEPT / f.name).exists():
                    shutil.copy2(f, KEPT / f.name)
                    print("  and as the kept icon", KEPT / f.name)


if __name__ == "__main__":
    main()
