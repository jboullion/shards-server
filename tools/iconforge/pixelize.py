"""
Step 4: turn a model's output into a game bitmap. Deterministic, no model involved:

    matte    the flat background (magenta by default, or whatever colour fills the corners)
             becomes transparent; a real alpha channel is used as is
    crop     to the object
    fit      into the original bitmap's object box, scaled to the new shrink, keeping aspect
    shrink   area-average down to the target size; alpha cut at 50% (BGFs are 1-bit)
    palette  every pixel to its nearest blakston.pal colour, no dithering; index 254 is transparency
    outline  optional 1-px dark edge around the silhouette

Most of the "sprite look" comes from here: the downscale and the 256-colour palette.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from bgf import TRANSPARENT, Bitmap

KEY_COLOR = (255, 0, 255)


def _palette_lab(pal: np.ndarray) -> np.ndarray:
    return _rgb_to_lab(pal.astype(np.float32) / 255.0)


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0..1) -> CIELAB, for perceptual nearest-colour matching."""
    a = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = a @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def matte(img: Image.Image, key=None, tolerance: float = 60.0) -> Image.Image:
    """RGBA with the background removed. Uses the image's alpha if it has any transparency,
    otherwise keys out `key` (default: the median colour of the four corners)."""
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    if (rgba[..., 3] < 250).mean() > 0.01:
        return Image.fromarray(rgba.astype(np.uint8), "RGBA")
    rgb = rgba[..., :3]
    if key is None:
        h, w = rgb.shape[:2]
        k = max(4, min(h, w) // 32)
        corners = np.concatenate([rgb[:k, :k].reshape(-1, 3), rgb[:k, -k:].reshape(-1, 3),
                                  rgb[-k:, :k].reshape(-1, 3), rgb[-k:, -k:].reshape(-1, 3)])
        key = np.median(corners, axis=0)
    dist = np.sqrt(((rgb - np.asarray(key, np.float32)) ** 2).sum(-1))
    # soft edge between tolerance and 2x tolerance, then de-spill the key colour from the edge
    alpha = np.clip((dist - tolerance) / tolerance, 0, 1)
    rgba[..., 3] = alpha * 255
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def opaque_box(img: Image.Image, threshold: int = 128):
    a = np.asarray(img.convert("RGBA"))[..., 3] >= threshold
    ys, xs = np.nonzero(a)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def to_palette(rgba: np.ndarray, pal: np.ndarray, allowed=None) -> np.ndarray:
    """(h, w, 4) uint8 -> (h, w) palette indices; transparent pixels -> 254."""
    idx_pool = np.array([i for i in range(256) if i != TRANSPARENT and (allowed is None or i in allowed)])
    lab_pal = _palette_lab(pal)[idx_pool]
    lab = _rgb_to_lab(rgba[..., :3].astype(np.float32) / 255.0)
    d = ((lab[..., None, :] - lab_pal[None, None, :, :]) ** 2).sum(-1)
    out = idx_pool[d.argmin(-1)].astype(np.uint8)
    out[rgba[..., 3] < 128] = TRANSPARENT
    return out


def add_outline(idx: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Darkest palette colour on every transparent pixel that touches the silhouette (4-neighbour)."""
    solid = idx != TRANSPARENT
    edge = np.zeros_like(solid)
    edge[1:] |= solid[:-1]; edge[:-1] |= solid[1:]; edge[:, 1:] |= solid[:, :-1]; edge[:, :-1] |= solid[:, 1:]
    edge &= ~solid
    lum = pal.astype(np.int32) @ np.array([299, 587, 114])
    lum[TRANSPARENT] = 1 << 30
    dark = int(np.argmin(np.where(np.arange(256) == 0, 1 << 30, lum)))  # avoid 0 (pure black reads as a hole)
    out = idx.copy()
    out[edge] = dark
    return out


def pixelize(generated: Image.Image, original: Bitmap, orig_shrink: int, new_shrink: int, pal: np.ndarray,
             outline: bool = False, allowed=None, key=KEY_COLOR) -> Bitmap:
    """The generated image as a new Bitmap the same in-game size and placement as `original`."""
    k = new_shrink / orig_shrink
    W, H = max(1, round(original.w * k)), max(1, round(original.h * k))
    hs = [{"num": h["num"], "x": round(h["x"] * k), "y": round(h["y"] * k)} for h in original.hotspots]
    obox = opaque_box(original.rgba(pal))
    if obox is None:
        # an empty frame in the original (some worn views are blank): keep it empty
        return Bitmap(W, H, round(original.xoff * k), round(original.yoff * k), hs, bytes([TRANSPARENT]) * (W * H))
    bx0, by0 = round(obox[0] * k), round(obox[1] * k)
    bw, bh = max(1, round((obox[2] - obox[0]) * k)), max(1, round((obox[3] - obox[1]) * k))

    cut = matte(generated, key=key)
    gbox = opaque_box(cut)
    if gbox is None:
        raise ValueError("the generated image has no object after matting")
    obj = cut.crop(gbox)
    s = min(bw / obj.width, bh / obj.height)
    tw, th = max(1, round(obj.width * s)), max(1, round(obj.height * s))
    # premultiply so the background never bleeds into edge colours, area-average down, unpremultiply
    a = np.asarray(obj).astype(np.float32) / 255.0
    pre = np.dstack([a[..., :3] * a[..., 3:4], a[..., 3:4]])
    small = np.asarray(Image.fromarray((pre * 255).astype(np.uint8), "RGBA").resize((tw, th), Image.BOX)).astype(np.float32)
    al = small[..., 3:4] / 255.0
    rgb = np.where(al > 0, small[..., :3] / np.maximum(al, 1e-6), 0)
    small = np.dstack([np.clip(rgb, 0, 255), small[..., 3]]).astype(np.uint8)

    canvas = np.zeros((H, W, 4), np.uint8)
    x0 = min(max(0, bx0 + (bw - tw) // 2), max(0, W - tw))
    y0 = min(max(0, by0 + (bh - th)), max(0, H - th))  # bottom-aligned, like an object resting in its box
    canvas[y0:y0 + th, x0:x0 + tw] = small[:H - y0, :W - x0]
    idx = to_palette(canvas, pal, allowed)
    if outline:
        idx = add_outline(idx, pal)
    return Bitmap(W, H, round(original.xoff * k), round(original.yoff * k), hs, idx.tobytes())
