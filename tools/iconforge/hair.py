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
RAMP = list(range(0xD0, 0xE0))  # the grey hair ramp, light to dark
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
}


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


def layout(views, cols: int):
    """One scale for every view (sheet px per image px) and each view's cell and integer offset in it."""
    rows = -(-len(views) // cols)
    cw, ch = SHEET // cols, SHEET // rows
    s = min(min(cw * 0.62 / v["w"], ch * 0.62 / v["h"]) for v in views)
    places = []
    for i, v in enumerate(views):
        cx, cy = (i % cols) * cw, (i // cols) * ch
        # the head's top-left sits at the same spot in every cell, on whole image pixels
        ox = int(round((cw / s - v["w"]) / 2)); oy = int(round((ch / s - v["h"]) * 0.45))
        places.append((cx, cy, ox, oy))
    return s, cw, ch, places


def compose(views, s, cw, ch, places) -> Image.Image:
    sheet = Image.new("RGB", (SHEET, SHEET), GREEN)
    for v, (cx, cy, ox, oy) in zip(views, places):
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


def build(kind: str, name: str, describe: str, ref: str | None, force: bool) -> Path:
    spec = KINDS[kind]
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    pal = load_palette()
    if kind == "hair":
        shown = render("male", spec["slots"], [{"bgf": (ref or "ptbb") + ".bgf", "hotspot": 13, "translation": 0}])
        face = render("male", spec["slots"], [], masks=[11, 12, 14])
    else:
        # the hair coloured (dark orange), so the matte never takes it for the new grey
        hair = [{"bgf": (ref or "ptad") + ".bgf", "hotspot": 13, "translation": 0x22}]
        shown = render("male", spec["slots"], hair)
        # clear of the eyes and nose only: the mouth sprite covers the whole jaw (the lips are red,
        # which the grey test leaves out anyway)
        face = render("male", spec["slots"], hair, masks=[11, 14])
    s, cw, ch, places = layout(shown, spec["cols"])
    sheet = compose(shown, s, cw, ch, places)
    sheet.save(d / "input.png")
    raw = d / "raw.png"
    if force or not raw.exists():
        text = spec["prompt"].format(n=len(shown), describe=describe)
        img, info = providers.run("openai", [sheet], text)
        img.save(raw)
        (d / "request.json").write_text(json.dumps({"describe": describe, "ref": ref, "prompt": text, "usage": info.get("usage")}, indent=1))
    gen = Image.open(raw)
    bitmaps = []
    for v, f, place in zip(shown, face, places):
        grid = back_to_grid(gen, s, cw, ch, place)
        keep = matte(kind, grid, v, f, place)
        idx = to_ramp(grid, keep, pal)
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
        k = kind or ("beard" if BGF.read(src).groups[0][3] == -1 else "hair")
        spec = KINDS[k]
        tiles = []
        for gender in ("male", "female"):
            for colour in PREVIEW_COLOURS:
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
    ap.add_argument("kind", choices=["hair", "beard", "preview"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--describe", default="")
    ap.add_argument("--ref")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.kind == "preview":
        preview(a.names)
    else:
        build(a.kind, a.names[0], a.describe, a.ref, a.force)


if __name__ == "__main__":
    main()
