"""
Step 1: find every item icon and flag the ones that look like fan art.

    python tools/iconforge/forge.py inventory

Reads the item classes (kod/object/item/**/*.kod) for the .bgf each one shows as its icon
(vrIcon, vrIcon_male, vrIcon_female), finds the file the game serves for each, and compares it
with the classic Steam client's copy:

    fanart    not in the classic client (or changed since) AND high-res: shrink >= 15 or a side >= 300 px.
              The 104 server's contributors drew big and let the client shrink it; the originals are ~64 px.
    check     not in the classic client (or changed) but low-res: may be fan art drawn small. Judge by eye.
    original  byte-identical art to the classic client.
    missing   no file anywhere.

Writes data/iconforge/inventory.json and build/iconforge/inventory/<status>.png contact sheets.
Icons get "selected": true when fanart; edit the JSON (or use `forge.py select`) to change the set.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bgf import BGF, load_palette, same_content
from review import contact_sheet

REPO = Path(__file__).resolve().parents[2]
ITEM_DIR = REPO / "kod" / "object" / "item"
DATA = REPO / "data" / "iconforge"
BUILD = REPO / "build" / "iconforge"

# Where the game's BGFs come from, in the order meridian-browser's asset build picks them
# (tools/assets/build-assets.ts: the installed 104 client wins over the server resource tree),
# with our own restyled icons (resource/graphics/shards) ahead of both.
SHARDS_DIR = REPO / "resource" / "graphics" / "shards"
SERVED = [Path.home() / "AppData" / "Local" / "Meridian-104" / "resource", REPO / "resource"]
CLASSIC = Path(r"H:\Steam\steamapps\common\Meridian 59\resource")

HIGHRES_SHRINK = 15
HIGHRES_SIDE = 300

RSC_RE = re.compile(r"^\s*(\w+)\s*=\s*([\w\-]+\.bgf)\b", re.M | re.I)
ICON_RE = re.compile(r"^\s*(vrIcon\w*)\s*=\s*(\w+)", re.M)
CLASS_RE = re.compile(r"^(\w+)\s+is\s+\w+", re.M)


def _index(root: Path, recursive: bool) -> dict:
    if not root.is_dir():
        return {}
    it = root.rglob("*") if recursive else root.iterdir()
    return {p.name.lower(): p for p in it if p.suffix.lower() == ".bgf"}


def item_icons() -> dict:
    """bgf name (lower case) -> sorted list of item classes that show it as an icon."""
    out: dict[str, set] = {}
    for kod in ITEM_DIR.rglob("*.kod"):
        text = kod.read_text(encoding="latin-1")
        m = CLASS_RE.search(text)
        cls = m.group(1) if m else kod.stem
        rscs = {k.lower(): v.lower() for k, v in RSC_RE.findall(text)}
        for _var, rsc in ICON_RE.findall(text):
            bgf = rscs.get(rsc.lower())
            if bgf:
                out.setdefault(bgf, set()).add(cls)
    return {k: sorted(v) for k, v in sorted(out.items())}


def classify(served: BGF | None, classic: BGF | None) -> str:
    if served is None:
        return "missing"
    if classic is not None and same_content(served, classic):
        return "original"
    side = max((max(b.w, b.h) for b in served.bitmaps), default=0)
    if served.shrink >= HIGHRES_SHRINK or side >= HIGHRES_SIDE:
        return "fanart"
    return "check"


def run() -> dict:
    icons = item_icons()
    shards = _index(SHARDS_DIR, False)
    served_idx = {}
    for root in reversed(SERVED):  # earlier roots win
        served_idx.update(_index(root, recursive=root == REPO / "resource"))
    classic_idx = _index(CLASSIC, False)

    previous = {}
    inv_path = DATA / "inventory.json"
    if inv_path.exists():
        previous = {e["name"]: e for e in json.loads(inv_path.read_text())["icons"]}

    entries = []
    for name, classes in icons.items():
        src = served_idx.get(name)
        served = classic = None
        try:
            served = BGF.read(src) if src else None
            classic = BGF.read(classic_idx[name]) if name in classic_idx else None
        except ValueError as e:
            print("!! %s: %s" % (name, e))
        status = classify(served, classic)
        e = {"name": name, "status": status, "classes": classes,
             "source": str(src) if src else None,
             "in_classic": name in classic_idx,
             "restyled": name in shards}
        if served:
            e.update(shrink=served.shrink, bitmaps=len(served.bitmaps), groups=len(served.groups),
                     size=[max(b.w for b in served.bitmaps), max(b.h for b in served.bitmaps)])
        e["selected"] = previous.get(name, {}).get("selected", status == "fanart")
        entries.append(e)

    DATA.mkdir(parents=True, exist_ok=True)
    counts = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    inv = {"counts": counts, "selected": sum(e["selected"] for e in entries), "icons": entries}
    inv_path.write_text(json.dumps(inv, indent=1))

    pal = load_palette()
    out = BUILD / "inventory"
    for status in ("fanart", "check", "original"):
        group = [e for e in entries if e["status"] == status and e.get("source")]
        if not group:
            continue
        tiles = []
        for e in group:
            b = BGF.read(Path(e["source"]))
            tiles.append(("%s (s%d)" % (e["name"].removesuffix(".bgf"), b.shrink), [inventory_bitmap(b).rgba(pal)]))
        contact_sheet(tiles, out / ("%s.png" % status), cell=96)
    print("icons: %d  %s  selected: %d" % (len(entries), counts, inv["selected"]))
    print("wrote %s and contact sheets in %s" % (inv_path, out))
    return inv


def inventory_bitmap(b: BGF):
    """The bitmap the inventory shows: group 3 (viInventory_group) when there is one, else the first."""
    if len(b.groups) >= 3 and b.groups[2]:
        return b.bitmaps[b.groups[2][0]]
    if b.groups and b.groups[0]:
        return b.bitmaps[b.groups[0][0]]
    return b.bitmaps[0]
