"""
Step 1: find every item icon and flag the ones that look like fan art.

    python tools/iconforge/forge.py inventory

Reads the item, spell and skill classes (kod/object/item, kod/object/passive/spell and /skill) for
the .bgf each one shows as its icon (vrIcon, vrIcon_male, vrIcon_female), finds the file the game serves for each, and compares it
with the classic Steam client's copy:

    fanart    not in the classic client (or changed since) AND high-res: shrink >= 15 or a side >= 300 px.
              The 104 server's contributors drew big and let the client shrink it; the originals are ~64 px.
    check     not in the classic client (or changed) but low-res: may be fan art drawn small. Judge by eye.
    original  byte-identical art to the classic client.
    missing   no file anywhere.

Writes data/iconforge/inventory.json and build/iconforge/inventory/<kind>-<status>.png contact sheets.
Fan-art items start selected; spell and skill icons only when chosen with `forge.py select`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bgf import BGF, load_palette, same_content
from review import contact_sheet

REPO = Path(__file__).resolve().parents[2]
# Where icon-bearing classes live, by kind. Items are inventory icons; spell and skill icons show in
# the spell/skill lists, the cast bar and (for enchantments) the status bar.
KINDS = {"item": REPO / "kod" / "object" / "item",
         "spell": REPO / "kod" / "object" / "passive" / "spell",
         "skill": REPO / "kod" / "object" / "passive" / "skill",
         # monsters and NPCs: the live sprite (vrIcon) and the corpse (vrDead_icon)
         "monster": REPO / "kod" / "object" / "active" / "holder" / "nomoveon" / "battler" / "monster"}
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
ICON_RE = re.compile(r"^\s*(vrIcon\w*|vrDead_icon\w*)\s*=\s*(\w+)", re.M)
CLASS_RE = re.compile(r"^(\w+)\s+is\s+\w+", re.M)


def _index(root: Path, recursive: bool) -> dict:
    if not root.is_dir():
        return {}
    it = root.rglob("*") if recursive else root.iterdir()
    return {p.name.lower(): p for p in it if p.suffix.lower() == ".bgf"}


def class_icons(root: Path) -> dict:
    """bgf name (lower case) -> sorted list of classes under `root` that show it as an icon."""
    out: dict[str, set] = {}
    for kod in root.rglob("*.kod"):
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
    icons, kinds = {}, {}
    for kind, root in KINDS.items():
        for name, classes in class_icons(root).items():
            icons.setdefault(name, []).extend(classes)
            kinds.setdefault(name, [])
            if kind not in kinds[name]:
                kinds[name].append(kind)
    shards = _index(SHARDS_DIR, False)
    served_idx = {}
    for root in reversed(SERVED):  # earlier roots win
        served_idx.update(_index(root, recursive=root == REPO / "resource"))
    classic_idx = _index(CLASSIC, False)
    for k, v in shards.items():  # icons that only exist as our own art (e.g. iiceshrd.bgf)
        served_idx.setdefault(k, v)

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
        e = {"name": name, "kind": "/".join(kinds[name]), "status": status, "classes": sorted(set(classes)),
             "source": str(src) if src else None,
             "in_classic": name in classic_idx,
             "restyled": name in shards}
        if served:
            e.update(shrink=served.shrink, bitmaps=len(served.bitmaps), groups=len(served.groups),
                     size=[max(b.w for b in served.bitmaps), max(b.h for b in served.bitmaps)])
        # Fan-art items start selected; spell and skill icons wait until chosen (`forge.py select`).
        e["selected"] = previous.get(name, {}).get("selected", status == "fanart" and kinds[name] == ["item"])
        entries.append(e)

    DATA.mkdir(parents=True, exist_ok=True)
    counts = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    inv = {"counts": counts, "selected": sum(e["selected"] for e in entries), "icons": entries}
    inv_path.write_text(json.dumps(inv, indent=1))

    pal = load_palette()
    out = BUILD / "inventory"
    for kind in KINDS:
        for status in ("fanart", "check", "original"):
            group = [e for e in entries if e["status"] == status and e.get("source") and kind in e["kind"].split("/")]
            if not group:
                continue
            tiles = []
            for e in group:
                b = BGF.read(Path(e["source"]))
                tiles.append(("%s (s%d)" % (e["name"].removesuffix(".bgf"), b.shrink), [inventory_bitmap(b).rgba(pal)]))
            contact_sheet(tiles, out / ("%s-%s.png" % (kind, status)), cell=96)
    for kind in KINDS:
        k = [e for e in entries if kind in e["kind"].split("/")]
        c = {}
        for e in k:
            c[e["status"]] = c.get(e["status"], 0) + 1
        print("%-5s icons: %3d  %s" % (kind, len(k), c))
    print("selected: %d" % inv["selected"])
    print("wrote %s and contact sheets in %s" % (inv_path, out))
    return inv


def inventory_bitmap(b: BGF):
    """The bitmap the inventory shows: group 3 (viInventory_group) when there is one, else the first."""
    if len(b.groups) >= 3 and b.groups[2]:
        return b.bitmaps[b.groups[2][0]]
    if b.groups and b.groups[0]:
        return b.bitmaps[b.groups[0][0]]
    return b.bitmaps[0]
