"""
Read and write Meridian 59 .bgf sprite files (BGF version 10), and load the game palette.

Format (makebgf/writebgf.c, clientd3d/dibutil.c):
    "BGF\\x11", int version (10), char name[32], int num_bitmaps, int num_groups,
    int max_indices, int shrink,
    per bitmap: int w, int h, int xoff, int yoff, u8 n_hotspots, n * (i8 num, int x, int y),
                u8 compressed, int clen, bytes (zlib if compressed, else w*h raw)
    per group:  int n, n * int index
Pixels are palette indices into blakston.pal; index 254 is transparent.

The reader follows meridian-unreal/tools/bgf2png/bgf2png.py; the writer is its inverse.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
PALETTE_FILE = REPO / "blakston.pal"
TRANSPARENT = 254
MAGIC = b"BGF\x11"
VERSION = 10


def load_palette(path: Path = PALETTE_FILE) -> np.ndarray:
    """The 256-colour game palette as a (256, 3) uint8 array."""
    rows = [tuple(int(v) for v in line.split()[:3]) for line in path.read_text().splitlines() if len(line.split()) >= 3]
    if len(rows) != 256:
        raise ValueError("%s has %d entries, not 256" % (path, len(rows)))
    return np.array(rows, dtype=np.uint8)


@dataclass
class Bitmap:
    w: int
    h: int
    xoff: int = 0
    yoff: int = 0
    hotspots: list = field(default_factory=list)  # [{"num", "x", "y"}]
    pixels: bytes = b""  # w*h palette indices, row-major

    def indices(self) -> np.ndarray:
        return np.frombuffer(self.pixels, dtype=np.uint8).reshape(self.h, self.w)

    def rgba(self, pal: np.ndarray) -> Image.Image:
        idx = self.indices()
        rgb = pal[idx]
        alpha = np.where(idx == TRANSPARENT, 0, 255).astype(np.uint8)
        return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


@dataclass
class BGF:
    name: str = ""
    shrink: int = 1
    bitmaps: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    version: int = VERSION

    @classmethod
    def read(cls, path: Path) -> "BGF":
        d = Path(path).read_bytes()
        if d[:4] != MAGIC:
            raise ValueError("%s: not a BGF file" % path)
        p = 4
        version, = struct.unpack_from("<i", d, p); p += 4
        if version < 10:
            raise ValueError("%s: BGF version %d unsupported" % (path, version))
        name = d[p:p + 32].split(b"\0")[0].decode("latin-1"); p += 32
        n_bmp, n_grp, _max_indices, shrink = struct.unpack_from("<4i", d, p); p += 16
        bitmaps = []
        for _ in range(n_bmp):
            w, h, xoff, yoff = struct.unpack_from("<4i", d, p); p += 16
            nh = d[p]; p += 1
            hotspots = []
            for _ in range(nh):
                num, = struct.unpack_from("<b", d, p); p += 1
                x, y = struct.unpack_from("<2i", d, p); p += 8
                hotspots.append({"num": num, "x": x, "y": y})
            comp = d[p]; p += 1
            clen, = struct.unpack_from("<i", d, p); p += 4
            if comp:
                pixels = zlib.decompress(d[p:p + clen]); p += clen
            else:
                pixels = d[p:p + w * h]; p += w * h
            bitmaps.append(Bitmap(w, h, xoff, yoff, hotspots, pixels))
        groups = []
        for _ in range(n_grp):
            n, = struct.unpack_from("<i", d, p); p += 4
            groups.append(list(struct.unpack_from("<%di" % n, d, p))); p += 4 * n
        return cls(name, shrink, bitmaps, groups, version)

    def to_bytes(self) -> bytes:
        out = bytearray(MAGIC)
        out += struct.pack("<i", self.version)
        out += self.name.encode("latin-1")[:31].ljust(32, b"\0")
        max_indices = max((len(g) for g in self.groups), default=0)
        out += struct.pack("<4i", len(self.bitmaps), len(self.groups), max_indices, self.shrink)
        for b in self.bitmaps:
            if len(b.pixels) != b.w * b.h:
                raise ValueError("bitmap is %dx%d but has %d pixels" % (b.w, b.h, len(b.pixels)))
            out += struct.pack("<4i", b.w, b.h, b.xoff, b.yoff)
            out += struct.pack("<B", len(b.hotspots))
            for hs in b.hotspots:
                out += struct.pack("<b2i", hs["num"], hs["x"], hs["y"])
            packed = zlib.compress(bytes(b.pixels), 9)
            out += struct.pack("<Bi", 1, len(packed)) + packed
        for g in self.groups:
            out += struct.pack("<i", len(g)) + struct.pack("<%di" % len(g), *g)
        return bytes(out)

    def write(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(self.to_bytes())

    def meta(self) -> dict:
        return {"name": self.name, "shrink": self.shrink, "groups": self.groups,
                "bitmaps": [{"w": b.w, "h": b.h, "xoff": b.xoff, "yoff": b.yoff, "hotspots": b.hotspots}
                            for b in self.bitmaps]}


def same_content(a: BGF, b: BGF) -> bool:
    """True when two BGFs hold the same art, groups and placement (ignores compression)."""
    if (a.shrink, a.groups, len(a.bitmaps)) != (b.shrink, b.groups, len(b.bitmaps)):
        return False
    return all((x.w, x.h, x.xoff, x.yoff, x.hotspots, bytes(x.pixels)) ==
               (y.w, y.h, y.xoff, y.yoff, y.hotspots, bytes(y.pixels)) for x, y in zip(a.bitmaps, b.bitmaps))
