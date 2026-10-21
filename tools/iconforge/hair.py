"""
New hairstyles and facial hair for players, in the game's own look (after meridian-unreal's
tools/sprites/new_hair.py).

    python hair.py hair  <name> --describe "a long single braid down the back" [--ref ptdc] [--force]
    python hair.py beard <name> --describe "a short, neatly trimmed full beard" [--force]
    python hair.py preview <name>...

1. Render the head and face, as the client draws them (headrender.ts), at every view the part has:
   hair at the head's 6 views on hotspot 13 (HS_TOUPEE), facial hair at the 5 front views on
   hotspot 12 (the mouth's). Hair shows a reference style in its raw grey; facial hair shows the
   face with no beard (and the hair coloured, so it can't be mistaken for the new grey).
2. One request redraws the sheet of views with the new part in neutral grey.
3. Each cell is mapped back onto the client's pixel grid (1 image px = 1 part px at shrink 14), the
   part matted (grey, not background, not the skin under it, not over the eyes, nose or mouth),
   and its greys snapped to the hair ramp (D0, 16 greys) plus black, the only colours hair uses: the
   hair colour (a palette translation) then recolours it like the original styles.
4. A BGF at shrink 14, each view placed by its offset from the head's hotspot, so it sits on any
   head; previews on the male and female heads in several hair colours.

Outputs go to build/iconforge/hair/<name>/ (raw model sheet cached as raw.png; delete it, or
--force, to ask again); the part is <name>.bgf there. `forge.py`-style install: copy it to
resource/graphics/shards/ and list it in kod/util/system.kod.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers  # noqa: E402
from bgf import BGF, Bitmap, load_palette  # noqa: E402
from review import contact_sheet  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BROWSER = Path(__import__("os").environ.get("MERIDIAN_BROWSER", r"E:\2026_Experiments\meridian-browser"))
ASSETS = BROWSER / "dist" / "assets"
OUT = REPO / "build" / "iconforge" / "hair"
OVERRIDE = OUT / "_override"
GREEN = (0, 177, 64)
SHEET = 1024
SKIN = 2  # XLAT DBLUETOSKIN2, a mid skin tone
BLACK = 0
OVER_FIGURE = 127  # a hotspot marking facial hair, which the browser draws over the body
RAMP =list(range(0xD0, 0xE0))  # the grey hair ramp, light to dark
PREVIEW_COLOURS = [0x2D, 0x2F, 0x2C, 0x11]  # brown, blond, black, blue

KINDS = {
    "hair": {"hotspot": 13, "slots": [0, 1, 2, 3, 6, 7], "groups": [[0, 1, 2, 3, 3, 3, 4, 5]], "cols": 3,
             "prompt": (
                 "The image is a sheet of {n} views of one character's head from a 1990s pre-rendered fantasy "
                 "role-playing game, one view per cell on flat green (front, three-quarter, side, back and the other "
                 "side), each with a placeholder hairstyle drawn in neutral grey. Redraw every view with this "
                 "hairstyle instead: {describe}. Keep each head, face, skin, ears, viewing angle, size and position "
                 "exactly as they are; change only the hair, and draw the same hairstyle consistently in every view. "
                 "Draw the hair in neutral grey shades only, from light grey highlights to dark grey shadows, with no "
                 "colour at all (the game recolours it). Match the original's painted, slightly grainy pre-rendered "
                 "look and its soft light from the upper left. Keep every view inside its own cell. The background "
                 "must stay flat pure green (#00B140) everywhere outside the heads and hair. No text, no borders.")},
    "beard": {"hotspot": 12, "slots": [0, 1, 2, 6, 7], "groups": [[0, 1, 2, -1, -1, -1, 3, 4]], "cols": 3,
              "prompt": (
                  "The image is a sheet of {n} views of one character's head from a 1990s pre-rendered fantasy "
                  "role-playing game, one view per cell on flat green (front, three-quarter, side and the other side). "
                  "Give him this facial hair: {describe}. Keep each head, face, skin, hair, ears, viewing angle, size "
                  "and position exactly as they are, and keep the lips visible; add only the facial hair, the same in "
                  "every view. Draw the facial hair in neutral grey shades only, from light grey highlights to dark "
                  "grey shadows, with no colour at all (the game recolours it). Match the original's painted, "
                  "slightly grainy pre-rendered look and its soft light from the upper left. Keep every view inside "
                  "its own cell. The background must stay flat pure green (#00B140) everywhere outside the heads. "
                  "No text, no borders.")},
    # Headwear (helmets hang on HS_TOUPEE and take the hair off): the head is shown bald, with room above
    # it for a tall hat. A hat's item frames (inventory, ground, broken) are made from its worn views.
    "hat": {"hotspot": 13, "slots": [0, 1, 2, 3, 6, 7], "groups": [[0, 1, 2, 3, 3, 3, 4, 5], [6], [7], [8]], "cols": 3,
            "fit": 0.42, "head_y": 0.85,
            "prompt": (
                "The image is a sheet of {n} views of one bald character's head from a 1990s pre-rendered fantasy "
                "role-playing game, one view per cell on flat green (front, three-quarter, side, back and the other "
                "side). Put this hat on every view: {describe}. It sits snugly on the head, and every view shows the "
                "same hat from that view's angle. Keep each head, face, skin, ears, viewing angle, size and position "
                "exactly as they are; add only the hat. Draw the whole hat in neutral grey shades only, light grey "
                "highlights through mid greys to dark grey shadows, mostly light and mid greys, with no colour at all "
                "(the game recolours it). Match the original's painted, slightly grainy pre-rendered look and its "
                "soft light from the upper left. Keep every view, the whole hat included, inside its own cell. The "
                "background must stay flat pure green (#00B140) everywhere outside the heads and hats. No text, no "
                "borders.")},
}

# Hat previews: the school colours a wizard's hat takes (wizardhat.kod ResetColors): none, Qor's black,
# Shal'ille's platinum, Faren's red, Kraanan's blue, Riija's purple, Jala's green
SCHOOL_COLOURS = [0, 0x2C, 0x30, 0x21, 0x20, 0x18, 0x15]


def render(gender: str, slots, parts, masks=(), override: Path | None = None) -> list[dict]:
    """headrender.ts: the composite per slot as numpy RGBA, with its origin, hotspots and mask."""
    work = OUT / "_render" / str(__import__("os").getpid())  # one per process: runs can go in parallel
    work.mkdir(parents=True, exist_ok=True)
    req = {"browser": str(BROWSER).replace("\\", "/"), "assets": str(ASSETS).replace("\\", "/"),
           "override": str(override).replace("\\", "/") if override else None,
           "gender": gender, "slots": list(slots), "skin": SKIN, "parts": parts, "masks": list(masks)}
    (work / "req.json").write_text(json.dumps(req))
    subprocess.run(["node", str(HERE / "headrender.ts"), str(work / "req.json"), str(work / "out.json")],
                   check=True, cwd=str(BROWSER), capture_output=True)
    views = json.loads((work / "out.json").read_text())
    for v in views:
        v["img"] = np.frombuffer(base64.b64decode(v["rgba"]), np.uint8).reshape(v["h"], v["w"], 4)
        v["maskarr"] = (np.frombuffer(base64.b64decode(v["mask"]), np.uint8).reshape(v["h"], v["w"]) > 0) if v["mask"] else np.zeros((v["h"], v["w"]), bool)
        v["hotspots"] = {int(k): val for k, val in v["hotspots"].items()}
    return views


def layout(views, cols: int, fit: float = 0.62, head_y: float = 0.45):
    """Each view's scale (sheet px per image px), cell and integer offset in it. Every head is drawn the
    same size: a view's image can be finer than another's (a back view with no face parts on it is drawn
    at the head's own resolution, the others finer), so the scale goes by head pixels.
    fit: how much of the cell the head fills; head_y: how far down the cell it sits (room above for a hat)."""
    rows = -(-len(views) // cols)
    cw, ch = SHEET // cols, SHEET // rows
    per_head = min(min(cw * fit * v["res"] / v["w"], ch * fit * v["res"] / v["h"]) for v in views)
    scales, places = [], []
    for i, v in enumerate(views):
        s = per_head / v["res"]
        cx, cy = (i % cols) * cw, (i // cols) * ch
        # the head's top-left sits at the same spot in every cell, on whole image pixels
        ox = int(round((cw / s - v["w"]) / 2)); oy = int(round((ch / s - v["h"]) * head_y))
        scales.append(s)
        places.append((cx, cy, ox, oy))
    return scales, cw, ch, places


def compose(views, scales, cw, ch, places) -> Image.Image:
    sheet = Image.new("RGB", (SHEET, SHEET), GREEN)
    for v, s, (cx, cy, ox, oy) in zip(views, scales, places):
        im = Image.fromarray(v["img"], "RGBA")
        im = im.resize((round(v["w"] * s), round(v["h"] * s)), Image.LANCZOS)
        im.putalpha(im.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
        sheet.paste(im, (cx + round(ox * s), cy + round(oy * s)), im)
    return sheet


def back_to_grid(gen: Image.Image, s, cw, ch, place) -> np.ndarray:
    """A generated cell resampled onto the view's own pixel grid (image px), extended to the cell."""
    cx, cy, _, _ = place
    gw, gh = int(cw / s), int(ch / s)
    cell = gen.convert("RGB").resize((SHEET, SHEET), Image.LANCZOS).crop((cx, cy, cx + round(gw * s), cy + round(gh * s)))
    return np.asarray(cell.resize((gw, gh), Image.BOX)).astype(np.float32)


def matte(kind: str, grid: np.ndarray, view: dict, face: dict, place) -> np.ndarray:
    """The new part's pixels on the grid: grey, not green, not the skin under them, clear of the features."""
    _, _, ox, oy = place
    # the face render has its own bounds: line its head up with the view's
    ox, oy = ox + view["ox"] - face["ox"], oy + view["oy"] - face["oy"]
    H, W = grid.shape[:2]
    r, g, b = grid[..., 0], grid[..., 1], grid[..., 2]
    mx, mn = grid.max(-1), grid.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1)
    green = (g > r + 35) & (g > b + 35)
    # model greys run slightly warm (beards up to ~0.34); skin never drops below ~0.38
    grey = (sat < 0.32) & ~green
    # the face render placed on the grid: skin it shows that the new image still shows is skin
    fimg = np.zeros((H, W, 4), np.float32); fmask = np.zeros((H, W), bool)
    fh, fw = face["h"], face["w"]
    y0, x0 = oy, ox
    ys, xs = slice(max(0, y0), min(H, y0 + fh)), slice(max(0, x0), min(W, x0 + fw))
    sy, sx = slice(ys.start - y0, ys.stop - y0), slice(xs.start - x0, xs.stop - x0)
    fimg[ys, xs] = face["img"][sy, sx]; fmask[ys, xs] = face["maskarr"][sy, sx]
    # skin the new image still shows: close to the render's colour there, and as saturated as skin
    skin = (fimg[..., 3] > 127) & (np.abs(grid - fimg[..., :3]).sum(-1) < 70) & (sat > 0.30)
    feat = np.asarray(Image.fromarray((fmask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    keep = grey & ~skin & ~feat
    if kind == "beard":
        # facial hair hangs below the eyes
        eye_y = face["hotspots"].get(11, [0, 0])[1] + y0
        keep[: int(eye_y) + 3] = False
    # open (drop specks), then close (fill pinholes where the hair matched the skin under it)
    m = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    m = m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return (np.asarray(m) > 0) & ~green & ~feat


def to_ramp(grid: np.ndarray, keep: np.ndarray, pal) -> np.ndarray:
    """Palette indices: the kept pixels' brightness, stretched to the original ramp's range, snapped to
    the hair greys (D0..DF) or black; 254 elsewhere."""
    lum = grid @ np.array([0.299, 0.587, 0.114], np.float32)
    out = np.full(keep.shape, 254, np.uint8)
    if not keep.any():
        return out
    lo, hi = np.percentile(lum[keep], [2, 98])
    v = np.clip((lum - lo) / max(1.0, hi - lo), 0, 1) * (225 - 30) + 30
    greys = np.array([pal[i][0] for i in RAMP], np.float32)
    idx = np.array(RAMP, np.uint8)[np.abs(v[..., None] - greys[None, None, :]).argmin(-1)]
    idx = np.where(v < 26, BLACK, idx)
    out[keep] = idx[keep]
    return out


# Palette whites outside the hair ramp: no hair colour or school colour (a GRAY_TO_* translation) changes them
FIXED_WHITE, FIXED_LIGHT = 0xFF, 0x07


def white_details(grid: np.ndarray, keep: np.ndarray, size: int = 9, lift: float = 30.0) -> np.ndarray:
    """Small bright shapes on the part (embroidered stars, buttons): pixels well above the brightness
    around them (a white top-hat over `size` px). Broad highlights rise slowly, so they stay in the ramp."""
    lum = grid @ np.array([0.299, 0.587, 0.114], np.float32)
    img = Image.fromarray(np.clip(lum, 0, 255).astype(np.uint8), "L")
    opened = np.asarray(img.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))).astype(np.float32)
    m = keep & (lum - opened > lift) & (lum > 130)
    # whole shapes: grow back onto their soft edges that are still light, then drop single specks
    grown = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
    m = m | (grown & keep & (lum > 150))
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    # the part's rim (its outline, a few px deep): edge highlights live there, embroidery doesn't
    inner = np.asarray(Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(7))) > 0
    return compact_shapes(m & keep, rim=keep & ~inner)


def compact_shapes(m: np.ndarray, rim: np.ndarray | None = None, min_area: int = 70, max_area: int = 400,
                   max_aspect: float = 1.75, min_fill: float = 0.48, max_on_rim: float = 0.3) -> np.ndarray:
    """Only the compact pieces of a mask away from the part's rim (stars, a moon, a button): the strokes
    and specks a highlight leaves along an edge or a curl are dropped."""
    out = np.zeros_like(m)
    seen = np.zeros_like(m)
    H, W = m.shape
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        stack, piece = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            piece.append((y, x))
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < H and 0 <= xx < W and m[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        ys, xs = np.array(piece).T
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        on_rim = rim[ys, xs].mean() if rim is not None else 0.0
        if __import__("os").environ.get("HAIR_DEBUG"):
            print(f"  shape at {int(xs.mean())},{int(ys.mean())}: area {len(piece)} {w}x{h} "
                  f"fill {len(piece) / (h * w):.2f} rim {on_rim:.2f}")
        if (min_area <= len(piece) <= max_area and max(h, w) / min(h, w) <= max_aspect
                and len(piece) / (h * w) >= min_fill and on_rim <= max_on_rim):
            out[ys, xs] = True
    return out


def item_frames(views: list) -> list:
    """A hat's frames off the head, from its worn views (the classic hats have them in groups 2-4):
    the inventory picture (the three-quarter view), lying on the ground (the front view), and broken
    (the front view crushed flat)."""
    def copy(b, squash=1.0):
        idx = np.frombuffer(b.pixels, np.uint8).reshape(b.h, b.w)
        h = max(1, round(b.h * squash))
        if h != b.h:
            idx = np.asarray(Image.fromarray(idx, "L").resize((b.w, h), Image.NEAREST))
        return Bitmap(b.w, h, 0, 0, [], idx.tobytes())
    return [copy(views[1]), copy(views[0]), copy(views[0], 0.45)]


def build(kind: str, name: str, describe: str, ref: str | None, force: bool, white: bool = False) -> Path:
    spec = KINDS[kind]
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    pal = load_palette()
    if kind == "hair":
        shown = render("male", spec["slots"], [{"bgf": (ref or "ptbb") + ".bgf", "hotspot": 13, "translation": 0}])
        face = render("male", spec["slots"], [], masks=[11, 12, 14])
    elif kind == "hat":
        # bald, as a worn helmet leaves the head (Helmet.NewUsed: RemoveHair). An empty shrink-14 part
        # on the toupee keeps every view as fine as the hat will be: a back view has no face parts, and
        # without one it would come out at the head's own resolution, half that of the others.
        OVERRIDE.mkdir(parents=True, exist_ok=True)
        BGF(name="_fine", shrink=14, groups=[[0]], bitmaps=[Bitmap(1, 1, 0, 0, [], bytes([254]))]).write(OVERRIDE / "_fine.bgf")
        fine = [{"bgf": "_fine.bgf", "hotspot": 13, "translation": 0}]
        shown = render("male", spec["slots"], fine, override=OVERRIDE)
        face = render("male", spec["slots"], fine, masks=[11, 12, 14], override=OVERRIDE)
    else:
        # the hair coloured (dark orange), so the matte never takes it for the new grey
        hair = [{"bgf": (ref or "ptad") + ".bgf", "hotspot": 13, "translation": 0x22}]
        shown = render("male", spec["slots"], hair)
        # clear of the eyes and nose only: the mouth sprite covers the whole jaw (the lips are red,
        # which the grey test leaves out anyway)
        face = render("male", spec["slots"], hair, masks=[11, 14])
    scales, cw, ch, places = layout(shown, spec["cols"], spec.get("fit", 0.62), spec.get("head_y", 0.45))
    sheet = compose(shown, scales, cw, ch, places)
    sheet.save(d / "input.png")
    raw = d / "raw.png"
    if force or not raw.exists():
        text = spec["prompt"].format(n=len(shown), describe=describe)
        img, info = providers.run("openai", [sheet], text)
        img.save(raw)
        (d / "request.json").write_text(json.dumps({"describe": describe, "ref": ref, "prompt": text, "usage": info.get("usage")}, indent=1))
    gen = Image.open(raw)
    bitmaps = []
    for v, f, s, place in zip(shown, face, scales, places):
        grid = back_to_grid(gen, s, cw, ch, place)
        keep = matte(kind, grid, v, f, place)
        idx = to_ramp(grid, keep, pal)
        if white:
            # stars and the like stay white whatever colour the rest takes
            if __import__("os").environ.get("HAIR_DEBUG"):
                print("view", v["slot"])
            wm = white_details(grid, keep)
            lum = grid @ np.array([0.299, 0.587, 0.114], np.float32)
            idx[wm] = np.where(lum[wm] >= 205, FIXED_WHITE, FIXED_LIGHT)
        ys, xs = np.nonzero(idx != 254)
        _, _, ox, oy = place
        hx, hy = v["hotspots"][spec["hotspot"]]
        if not len(xs):
            bitmaps.append(Bitmap(1, 1, 0, 0, [], bytes([254])))
            continue
        # crop on whole head pixels, so the offset from the hotspot (in head px) is exact
        res = v["res"]
        x0 = xs.min() - ((xs.min() - ox - v["ox"]) % res); y0 = ys.min() - ((ys.min() - oy - v["oy"]) % res)
        x1, y1 = xs.max() + 1, ys.max() + 1
        crop = idx[y0:y1, x0:x1]
        xoff = int(round((x0 - ox - v["ox"]) / res - (hx - v["ox"]) / res))
        yoff = int(round((y0 - oy - v["oy"]) / res - (hy - v["oy"]) / res))
        bitmaps.append(Bitmap(crop.shape[1], crop.shape[0], xoff, yoff, [], crop.tobytes()))
    if kind == "beard":
        # meridian-browser sprites.ts HS_OVER_FIGURE: drawn over the body, whose collar would hide it
        for b in bitmaps:
            b.hotspots.append({"num": OVER_FIGURE, "x": 0, "y": 0})
    if kind == "hat":
        bitmaps += item_frames(bitmaps)
    part = BGF(name=name, shrink=14, groups=spec["groups"], bitmaps=bitmaps)
    out = d / f"{name}.bgf"
    part.write(out)
    print(f"{kind} {name}: {len(bitmaps)} views -> {out}")
    preview([name], kind)
    return out


def preview(names, kind: str | None = None):
    """Each part on the male and female heads, every view, in several hair colours: preview.png."""
    pal = load_palette()
    OVERRIDE.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = OUT / name / f"{name}.bgf"
        (OVERRIDE / f"{name}.bgf").write_bytes(src.read_bytes())
        groups = BGF.read(src).groups
        k = kind or ("hat" if len(groups) == 4 else "beard" if groups[0][3] == -1 else "hair")
        spec = KINDS[k]
        tiles = []
        for gender in ("male", "female"):
            for colour in SCHOOL_COLOURS if k == "hat" else PREVIEW_COLOURS:
                parts = [{"bgf": f"{name}.bgf", "hotspot": spec["hotspot"], "translation": colour}]
                if k == "beard":
                    parts.insert(0, {"bgf": "ptad.bgf", "hotspot": 13, "translation": colour})
                for v in render(gender, [0, 1, 2, 3, 6, 7], parts, override=OVERRIDE):
                    im = Image.fromarray(v["img"], "RGBA").resize((v["w"] * 2 // v["res"], v["h"] * 2 // v["res"]), Image.BILINEAR)
                    tiles.append((f"{gender[0]} {colour:#x} s{v['slot']}", [im]))
        contact_sheet(tiles, OUT / name / "preview.png", cell=150)
        print("preview:", OUT / name / "preview.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["hair", "beard", "hat", "preview"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--describe", default="")
    ap.add_argument("--ref")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--white-details", action="store_true",
                    help="small bright shapes (stars, buttons) stay white instead of taking the colour")
    a = ap.parse_args()
    if a.kind == "preview":
        preview(a.names)
    else:
        build(a.kind, a.names[0], a.describe, a.ref, a.force, a.white_details)


if __name__ == "__main__":
    main()
