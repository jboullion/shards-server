"""
The red nose mask's sprites (RedNoseMask, kod/object/item/passitem/defmod/helmet/mask/rednosemsk.kod), drawn
by script: a shaded ball, placed on the face's nose in every view.

    python tools/iconforge/rednose.py [--install]

The 104 fan art (mmrednose.bgf, mfrednose.bgf) was a ball 200 px across at shrink 1, with no offsets, so it
drew three squares wide on the ground and over the whole head when worn. A ball needs no model: this draws it
lit from the upper left, in the grey ramp (D0-DF) that the mask's PT_GRAY_TO_LRED turns red, with a white
glint (255, which no translation changes).

- The worn views hang from the head's HS_TOUPEE (helmet.kod GetOverlayHotspot), like the carrot nose's
  (mmcarnose.bgf), so their offsets are in the head's pixels from that hotspot. Each view's ball sits on the
  tip of the face's nose: the head's nose hotspot (14) plus the nose bitmaps (pnax, pnbx, pncx for men; pnkx,
  pnlx, pnmx for women), averaged, as meridian-browser's sprites.ts places them. The back views stay blank, as
  the face's are.
- Shrink 14, the face parts' own, so the ball is as fine as the nose under it.
- Groups as the carrot nose's: the worn views, then the ground frame; the inventory shows the ground frame
  too (FaceMask's viGround_group 2, viInventory_group 3), about the carrot nose's size on the ground.

Outputs go to build/iconforge/rednose/ with preview.png (each view on both heads, red). --install copies the
BGFs to resource/graphics/shards/ and over the kept icon, which forge.py install would otherwise copy back.
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bgf import BGF, Bitmap, TRANSPARENT  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BROWSER = Path(__import__("os").environ.get("MERIDIAN_BROWSER", r"E:\2026_Experiments\meridian-browser"))
ASSETS = BROWSER / "dist" / "assets"
OUT = REPO / "build" / "iconforge" / "rednose"
SHARDS_DIR = REPO / "resource" / "graphics" / "shards"
KEPT = REPO / "build" / "iconforge" / "kept"

SHRINK = 14
HEAD_SHRINK = 7  # phax, phkx
WORN_D = 18  # the worn ball across, in its pixels (9 head pixels: about the nose's width)
GROUND_D = 28  # on the ground: 32 fine units, about the carrot nose's length by half its width
RAMP = list(range(0xD0, 0xE0))  # light to dark
GLINT = 255
PT_GRAY_TO_LRED = 0x21
TOUPEE, NOSE = 13, 14
VIEW_SLOTS = [0, 1, 2, 6, 7]  # the views with a face; the head's group runs 0,1,2,3,3,3,4,5
HEADS = {
    "male": ("phax.bgf", ["pnax.bgf", "pnbx.bgf", "pncx.bgf"], "mmrednose.bgf"),
    "female": ("phkx.bgf", ["pnkx.bgf", "pnlx.bgf", "pnmx.bgf"], "mfrednose.bgf"),
}


def ball(d: int, glint: bool) -> np.ndarray:
    """A d x d ball of palette indices, lit from the upper left and the front, 4x supersampled."""
    k = 4
    n = d * k
    c = (np.arange(n) + 0.5) / n * 2 - 1
    x, y = np.meshgrid(c, c)
    r2 = x * x + y * y
    inside = r2 < 1
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    light = np.array([-0.45, -0.55, 0.7])
    light /= np.linalg.norm(light)
    lam = np.clip(x * light[0] + y * light[1] + z * light[2], 0, 1)
    shade = 0.12 + 0.88 * lam ** 1.2
    shade = shade * (1 - 0.25 * (1 - z) ** 3)  # a darker rim, so it reads against skin
    cover = inside.reshape(d, k, d, k).mean(axis=(1, 3))
    s = np.where(inside, shade, 0).reshape(d, k, d, k).sum(axis=(1, 3)) / np.maximum(inside.reshape(d, k, d, k).sum(axis=(1, 3)), 1)
    idx = np.array(RAMP)[np.clip(((1 - s) * len(RAMP)).astype(int), 0, len(RAMP) - 1)]
    out = np.where(cover >= 0.5, idx, TRANSPARENT).astype(np.uint8)
    if glint:
        # the highlight's peak, where the light reflects straight back
        h = light + np.array([0, 0, 1.0])
        h /= np.linalg.norm(h)
        gx, gy = (h[0] + 1) / 2 * d, (h[1] + 1) / 2 * d
        spots = [(int(gy), int(gx))] + ([(int(gy), int(gx) + 1), (int(gy) + 1, int(gx))] if d >= 24 else [])
        for r, cc in spots:
            if out[r, cc] != TRANSPARENT:
                out[r, cc] = GLINT
    return out


def nose_tips(head_name: str, noses: list[str]) -> dict[int, tuple[float, float, int]]:
    """Per view slot: the nose's tip in head pixels and which way it points (-1 left, 1 right, 0 at us)."""
    head = BGF.read(ASSETS / head_name)
    tips: dict[int, list] = {s: [] for s in VIEW_SLOTS}
    for name in noses:
        nose = BGF.read(ASSETS / name)
        for s in VIEW_SLOTS:
            hb = head.bitmaps[head.groups[0][s]]
            nb = nose.bitmaps[nose.groups[0][s]]
            hs = next(h for h in hb.hotspots if abs(h["num"]) == NOSE)
            k = HEAD_SHRINK / nose.shrink
            ys, xs = np.nonzero(nb.indices() != TRANSPARENT)
            hx = hs["x"] + nb.xoff + (xs + 0.5) * k
            hy = hs["y"] + nb.yoff + (ys + 0.5) * k
            if s == 0:
                # the bottom of the nose, at its middle: the tip, seen from in front
                tips[s].append((hx.mean(), np.percentile(hy, 88), 0))
            else:
                # the furthest point the nose reaches, toward where the face looks
                way = -1 if s in (1, 2) else 1
                edge = hx.min() if way < 0 else hx.max()
                near = np.abs(hx - edge) <= 1.5
                tips[s].append((edge, hy[near].mean(), way))
    return {s: (float(np.mean([t[0] for t in v])), float(np.mean([t[1] for t in v])), v[0][2]) for s, v in tips.items()}


def worn_frame(head: BGF, slot: int, tip: tuple[float, float, int], art: np.ndarray) -> Bitmap:
    hb = head.bitmaps[head.groups[0][slot]]
    hs = next(h for h in hb.hotspots if abs(h["num"]) == TOUPEE)
    px = HEAD_SHRINK / SHRINK  # head pixels per ball pixel
    r = WORN_D * px / 2
    tx, ty, way = tip
    # the ball's centre: on the tip, sunk into it a little, a touch further forward than the tip in side views
    cx = tx - way * r * 0.35
    cy = ty - (r * 0.55 if way == 0 else 0)
    left, top = cx - r - hs["x"], cy - r - hs["y"]
    # offsets are whole head pixels; a ball pixel is half of one, so pad a transparent column/row for the half
    ox, oy = int(np.floor(left)), int(np.floor(top))
    padx, pady = int(round((left - ox) / px)), int(round((top - oy) / px))
    a = np.full((art.shape[0] + pady, art.shape[1] + padx), TRANSPARENT, np.uint8)
    a[pady:, padx:] = art
    return Bitmap(a.shape[1], a.shape[0], ox, oy, [], a.tobytes())


def make(gender: str) -> BGF:
    head_name, noses, out_name = HEADS[gender]
    head = BGF.read(ASSETS / head_name)
    tips = nose_tips(head_name, noses)
    worn = ball(WORN_D, glint=True)
    frames = {s: worn_frame(head, s, tips[s], worn) for s in VIEW_SLOTS}
    blank = Bitmap(1, 1, 0, 0, [], bytes([TRANSPARENT]))
    g = ball(GROUND_D, glint=True)
    ground = Bitmap(g.shape[1], g.shape[0], 0, 0, [], g.tobytes())
    bitmaps = [frames[0], frames[1], frames[2], blank, frames[6], frames[7], ground]
    groups = [[0, 1, 2, 3, 3, 3, 4, 5], [6], [6]]
    return BGF(out_name.removesuffix(".bgf"), SHRINK, bitmaps, groups)


def preview(paths: dict[str, Path]):
    """Each gender's head in every view wearing its nose, red, through meridian-browser's compositor."""
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for gender, p in paths.items():
        req = {"browser": str(BROWSER), "assets": str(ASSETS), "override": str(OUT), "gender": gender,
               "slots": [0, 1, 2, 3, 6, 7], "skin": 2,
               "parts": [{"bgf": p.name, "hotspot": TOUPEE, "translation": PT_GRAY_TO_LRED}]}
        (OUT / "_req.json").write_text(json.dumps(req))
        subprocess.run(["node", str(HERE / "headrender.ts"), str(OUT / "_req.json"), str(OUT / "_res.json")], check=True,
                       stdout=subprocess.DEVNULL)
        ims = []
        for v in json.loads((OUT / "_res.json").read_text()):
            a = np.frombuffer(base64.b64decode(v["rgba"]), np.uint8).reshape(v["h"], v["w"], 4)
            im = Image.fromarray(a.copy(), "RGBA")
            if v["res"] == 1:
                im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
            ims.append(im)
        rows.append(ims)
    w = max(sum(i.width + 8 for i in r) for r in rows)
    h = sum(max(i.height for i in r) + 8 for r in rows)
    sheet = Image.new("RGBA", (w, h), (52, 80, 52, 255))
    y = 0
    for r in rows:
        x = 0
        for i in r:
            sheet.alpha_composite(i, (x, y))
            x += i.width + 8
        y += max(i.height for i in r) + 8
    sheet.resize((sheet.width * 3, sheet.height * 3), Image.NEAREST).save(OUT / "preview.png")
    for f in ("_req.json", "_res.json"):
        (OUT / f).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="copy the BGFs to resource/graphics/shards")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    paths = {}
    for gender in HEADS:
        b = make(gender)
        p = OUT / HEADS[gender][2]
        b.write(p)
        paths[gender] = p
        print("wrote", p)
    preview(paths)
    print("preview", OUT / "preview.png")
    if a.install:
        for p in paths.values():
            shutil.copyfile(p, SHARDS_DIR / p.name)
            print("  installed", SHARDS_DIR / p.name)
            if (KEPT / p.name).exists():
                shutil.copyfile(p, KEPT / p.name)
                print("  and as the kept icon", KEPT / p.name)


if __name__ == "__main__":
    main()
