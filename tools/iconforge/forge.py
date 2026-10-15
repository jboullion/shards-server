"""
iconforge: restyle the fan-art item icons into sprites that match the original Meridian 59 art.

    python tools/iconforge/forge.py inventory                 # 1. find icons, flag fan art, contact sheets
    python tools/iconforge/forge.py select carrot onion       #    add icons to the work set (--off removes)
    python tools/iconforge/forge.py batch --provider openai   # 2-5. next 20 icons -> candidates + review page
    python tools/iconforge/forge.py apply decisions-003.json  # 6. record keeps/rejects from the review page
    python tools/iconforge/forge.py install                   # 7. kept icons -> resource/graphics/shards/
    python tools/iconforge/forge.py status

batch options:
    --size 20                    icons per batch (rejected ones come back first)
    --provider openai,fal,comfy  one or more provider specs; "provider@model" picks a model
    --variants 2                 candidates per provider per icon
    --names a b c                exactly these icons (any status), e.g. for a bake-off
    --shrink 5                   shrink the new BGFs are written at (originals are ~4-7)
    --outline                    add a 1-px dark outline around each sprite
    --frames inventory           only send the inventory picture to the model (cheap bake-offs);
                                 ground/worn frames are pixelized from the original
    --opt denoise=0.5 --opt lora=pixel.safetensors   provider options (comfy)
    --workers 4                  parallel model calls

State lives in data/iconforge/ (inventory.json, state.json; commit these). Generated images,
candidates and review pages are in build/iconforge/ (git-ignored). Model outputs are cached by
input + prompt + model + variant, so re-running a batch never pays twice for the same request.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory as inv_mod  # noqa: E402
import providers  # noqa: E402
from bgf import BGF, TRANSPARENT, load_palette  # noqa: E402
from pixelize import KEY_COLOR, pixelize  # noqa: E402
from review import contact_sheet, fit, review_page  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = REPO / "data" / "iconforge"
BUILD = REPO / "build" / "iconforge"
CACHE = BUILD / "cache"
KEPT = BUILD / "kept"
INSTALL = REPO / "resource" / "graphics" / "shards"
STYLE = HERE / "style" / "style.md"
STATE_FILE = DATA / "state.json"
INV_FILE = DATA / "inventory.json"

BASE_PROMPT = ("Redraw the item in the first image as a game inventory icon. Keep it recognisably the same "
               "item: same object, shape, silhouette, proportions, colours and orientation.")
# The local img2img model (providers.comfy): SDXL base + Pixel Art XL, the LoRA's recommended strength.
COMFY_DEFAULTS = {"lora": "pixel-art-xl.safetensors", "lora_strength": "1.2", "denoise": "0.6"}
REF_PROMPT = "The other images are original item sprites from the game: match their style exactly, not their subjects."


# ------------------------------------------------------------------------------------- state

def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def load_state() -> dict:
    return load_json(STATE_FILE, {"next_batch": 1, "icons": {}})


def icon_state(state: dict, name: str) -> dict:
    return state["icons"].setdefault(name, {"status": "pending", "attempts": 0, "notes": [], "history": []})


def inventory() -> dict:
    if not INV_FILE.exists():
        raise SystemExit("run `forge.py inventory` first")
    return {e["name"]: e for e in json.loads(INV_FILE.read_text())["icons"]}


def normalise(name: str) -> str:
    name = name.lower()
    return name if name.endswith(".bgf") else name + ".bgf"


# ------------------------------------------------------------------------------------- style

def style_text() -> tuple:
    text = STYLE.read_text(encoding="utf-8")
    block = re.search(r"<!-- style-block -->(.*?)<!-- /style-block -->", text, re.S)
    refs = re.search(r"<!-- refs -->(.*?)<!-- /refs -->", text, re.S)
    if not block:
        raise SystemExit("%s has no style-block markers" % STYLE)
    style = " ".join(line.strip() for line in block.group(1).strip().splitlines() if line.strip())
    names = [line.strip() for line in (refs.group(1) if refs else "").splitlines() if line.strip()]
    return style, names


def on_canvas(img: Image.Image, size: int = 1024, fill: float = 0.8) -> Image.Image:
    """The sprite centred on a flat magenta square, scaled to `fill` of it."""
    img = img.convert("RGBA")
    box = img.getbbox() or (0, 0, img.width, img.height)
    img = img.crop(box)
    s = fill * size / max(img.width, img.height)
    img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                     Image.NEAREST if s >= 2 else Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), KEY_COLOR + (255,))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    return canvas.convert("RGB")


def style_refs(pal) -> list:
    _, names = style_text()
    imgs = []
    for n in names:
        p = inv_mod.CLASSIC / n
        if not p.exists():
            print("!! style reference %s not found in %s" % (n, inv_mod.CLASSIC))
            continue
        imgs.append(on_canvas(inv_mod.inventory_bitmap(BGF.read(p)).rgba(pal), 512))
    return imgs


def prompt_for(notes: list, provider: str = "openai", item: str = "") -> str:
    """The request text: the full instruction prompt, or for the local img2img model (comfy) the
    short tag prompt from style.md's comfy-block."""
    if provider == "comfy":
        block = re.search(r"<!-- comfy-block -->(.*?)<!-- /comfy-block -->", STYLE.read_text(encoding="utf-8"), re.S)
        text = " ".join(block.group(1).split()).replace("{item}", item)
        return text + (", " + ", ".join(n.rstrip(".") for n in notes) if notes else "")
    style, _ = style_text()
    parts = [BASE_PROMPT, REF_PROMPT]
    if notes:
        parts.append("Corrections from the last review: %s." % "; ".join(n.rstrip(".") for n in notes))
    parts.append(style)
    return " ".join(parts)


def item_words(classes: list) -> str:
    """"WolfMask" -> "wolf mask": the item's class name as words, for tag prompts."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", classes[0]).lower() if classes else "item"


# ------------------------------------------------------------------------------------- batch

def digest(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, Image.Image):
            buf = io.BytesIO()
            p.save(buf, "PNG")
            p = buf.getvalue()
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:24]


def generate(spec, images, text, opts, variant) -> tuple:
    """A cached model call -> (image, info)."""
    key = digest(spec, variant, text, json.dumps(opts, sort_keys=True), *images)
    png, meta = CACHE / (key + ".png"), CACHE / (key + ".json")
    if png.exists():
        return Image.open(png).copy(), json.loads(meta.read_text()) if meta.exists() else {}
    img, info = providers.run(spec, images, text, opts)
    CACHE.mkdir(parents=True, exist_ok=True)
    img.save(png)
    meta.write_text(json.dumps(info))
    return img, info


def unique_frames(b: BGF) -> dict:
    """bitmap index -> index of the first bitmap with identical pixels (restyle each distinct one once)."""
    seen, out = {}, {}
    for i, bm in enumerate(b.bitmaps):
        k = (bm.w, bm.h, bytes(bm.pixels))
        out[i] = seen.setdefault(k, i)
    return out


def cmd_batch(a):
    inv = inventory()
    state = load_state()
    pal = load_palette()
    specs = [s.strip() for s in a.provider.split(",") if s.strip()]
    for s in specs:
        providers.split(s)
    opts = dict(o.split("=", 1) for o in a.opt)

    if a.names:
        names = [normalise(n) for n in a.names]
        missing = [n for n in names if n not in inv]
        if missing:
            raise SystemExit("not in the inventory: %s" % ", ".join(missing))
    else:
        todo = [n for n, e in inv.items() if e["selected"] and e.get("source")
                and icon_state(state, n)["status"] in ("pending", "rejected")]
        todo.sort(key=lambda n: (icon_state(state, n)["status"] != "rejected", n))
        names = todo[:a.size]
    if not names:
        print("nothing to do: every selected icon is kept or waiting for review (forge.py status)")
        return

    batch = "%03d" % state["next_batch"]
    out = BUILD / "batches" / batch
    refs = style_refs(pal)
    print("batch %s: %d icons x %s x %d variants, %d style refs" % (batch, len(names), ",".join(specs), a.variants, len(refs)))

    # one job per (icon, distinct frame, provider, variant)
    sources, jobs = {}, []
    for n in names:
        b = BGF.read(Path(inv[n]["source"]))
        frames = unique_frames(b)
        notes = icon_state(state, n)["notes"]
        texts = {s: prompt_for(notes, providers.split(s)[0], item_words(inv[n]["classes"])) for s in specs}
        sources[n] = (b, frames, texts)
        inv_fi = frames[b.bitmaps.index(inv_mod.inventory_bitmap(b))]
        for fi in sorted(set(frames.values())):
            if a.frames == "inventory" and fi != inv_fi:
                continue  # pixelized straight from the original below
            src = on_canvas(b.bitmaps[fi].rgba(pal))
            for s in specs:
                for v in range(a.variants):
                    o = dict(opts)
                    if providers.split(s)[0] == "comfy":
                        o = {**COMFY_DEFAULTS, **o, "seed": int(digest(n, fi, v)[:8], 16)}
                    jobs.append((n, fi, s, v, src, texts[s], o))

    results, failures = {}, []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(generate, s, [src] + refs, text, o, v): (n, fi, s, v)
                for n, fi, s, v, src, text, o in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            n, fi, s, v = futs[f]
            try:
                results[(n, fi, s, v)] = f.result()[0]
            except Exception as e:  # keep the rest of the batch going
                failures.append("%s frame %d %s v%d: %s" % (n, fi, s, v, e))
            print("  %d/%d" % (i, len(jobs)), end="\r")
    print()
    for msg in failures:
        print("!! " + msg)

    manifest, review_items, sheet = {"batch": batch, "providers": specs, "items": []}, [], []
    for n in names:
        b, frames, texts = sources[n]
        item = {"name": n, "prompts": texts, "variants": []}
        inv_frame = inv_mod.inventory_bitmap(b)
        variant_imgs = []
        for s in specs:
            for v in range(a.variants):
                try:
                    new = {}
                    for fi in sorted(set(frames.values())):
                        gen = results.get((n, fi, s, v))
                        if gen is None and a.frames == "inventory":
                            gen = on_canvas(b.bitmaps[fi].rgba(pal))
                        if gen is None:
                            raise KeyError("frame %d failed" % fi)
                        new[fi] = pixelize(gen, b.bitmaps[fi], b.shrink, a.shrink, pal, outline=a.outline)
                except (KeyError, ValueError) as e:
                    print("!! %s %s v%d: %s" % (n, s, v, e))
                    continue
                cand = BGF(name=b.name, shrink=a.shrink, groups=[list(g) for g in b.groups],
                           bitmaps=[new[frames[i]] for i in range(len(b.bitmaps))])
                k = len(item["variants"])
                path = out / n.removesuffix(".bgf") / ("v%d.bgf" % k)
                cand.write(path)
                raw = results[(n, frames[b.bitmaps.index(inv_frame)], s, v)]
                raw.convert("RGB").resize((256, 256)).save(path.with_suffix(".raw.png"))
                item["variants"].append({"spec": s, "variant": v, "bgf": str(path.relative_to(REPO))})
                variant_imgs.append([inv_mod.inventory_bitmap(cand).rgba(pal)])
        manifest["items"].append(item)
        if variant_imgs:
            ref_size = variant_imgs[0][0].size
            orig = inv_frame.rgba(pal)
            orig = orig.resize(ref_size, Image.LANCZOS) if orig.width > ref_size[0] * 2 else orig
            review_items.append({"name": n, "classes": inv[n]["classes"], "attempt": icon_state(state, n)["attempts"] + 1,
                                 "original": [orig], "variants": variant_imgs})
            sheet.append((n.removesuffix(".bgf"), [orig] + [vi[0] for vi in variant_imgs]))
            st = icon_state(state, n)
            st["status"] = "review"
            st["attempts"] += 1
            st["history"].append({"batch": batch, "providers": specs})

    save_json(out / "manifest.json", manifest)
    review_page(batch, review_items, out / "review.html")
    contact_sheet(sheet, out / "sheet.png", cell=96, cols=4)
    state["next_batch"] += 1
    save_json(STATE_FILE, state)
    print("review page: %s" % (out / "review.html"))
    print("then: forge.py apply <the decisions-%s.json it saves>" % batch)
    if a.open:
        webbrowser.open((out / "review.html").as_uri())


# ------------------------------------------------------------------------------------- apply / install

def cmd_apply(a):
    dec = json.loads(Path(a.decisions).read_text())
    batch = dec["batch"]
    manifest = load_json(BUILD / "batches" / batch / "manifest.json", None)
    if manifest is None:
        raise SystemExit("no manifest for batch %s" % batch)
    decided = {d["name"]: d for d in dec["decisions"]}
    state = load_state()
    counts = {"keep": 0, "reject": 0, "undecided": 0}
    # every icon of the batch: one missing from the file is undecided and goes back in the queue
    for item in manifest["items"]:
        n, st = item["name"], icon_state(state, item["name"])
        d = decided.get(n, {})
        decision = d.get("decision")
        if decision == "keep" and item["variants"]:
            v = item["variants"][min(int(d.get("variant") or 0), len(item["variants"]) - 1)]
            KEPT.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / v["bgf"], KEPT / n)
            st.update(status="kept", chosen={"batch": batch, **v})
        elif decision == "reject":
            st["status"] = "rejected"
            if (d.get("note") or "").strip():
                st["notes"].append(d["note"].strip())
        else:
            st["status"] = "pending"
            decision = "undecided"
        if st["history"]:
            st["history"][-1]["decision"] = decision
        counts[decision] += 1
    save_json(STATE_FILE, state)
    print("batch %s: %s" % (batch, counts))


def cmd_install(a):
    state = load_state()
    INSTALL.mkdir(parents=True, exist_ok=True)
    done = 0
    for n, st in sorted(state["icons"].items()):
        src = KEPT / n
        if st["status"] in ("kept", "installed") and src.exists():
            shutil.copyfile(src, INSTALL / n)
            st["status"] = "installed"
            done += 1
    save_json(STATE_FILE, state)
    print("installed %d icons in %s" % (done, INSTALL))
    print("browser client: its asset build must read this folder first (see tools/iconforge/README.md)")


def cmd_select(a):
    data = json.loads(INV_FILE.read_text())
    names = {normalise(n) for n in a.names}
    hits = 0
    for e in data["icons"]:
        if e["name"] in names or (a.status and e["status"] == a.status):
            e["selected"] = not a.off
            hits += 1
    unknown = names - {e["name"] for e in data["icons"]}
    data["selected"] = sum(e["selected"] for e in data["icons"])
    INV_FILE.write_text(json.dumps(data, indent=1))
    print("%s %d icons; %d selected in all%s" % ("deselected" if a.off else "selected", hits, data["selected"],
                                                  ("; unknown: " + ", ".join(sorted(unknown))) if unknown else ""))


def cmd_status(a):
    inv = inventory()
    state = load_state()
    counts = {}
    for n, e in inv.items():
        if e["selected"]:
            s = icon_state(state, n)["status"]
            counts[s] = counts.get(s, 0) + 1
    print("selected icons by status: %s" % counts)
    print("next batch: %03d" % state["next_batch"])
    if a.verbose:
        for n, e in inv.items():
            if e["selected"]:
                st = icon_state(state, n)
                print("  %-22s %-9s attempts %d %s" % (n, st["status"], st["attempts"], "; ".join(st["notes"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    p = sub.add_parser("select")
    p.add_argument("names", nargs="*")
    p.add_argument("--status", help="every icon with this inventory status (fanart, check, original)")
    p.add_argument("--off", action="store_true")
    p = sub.add_parser("batch")
    p.add_argument("--size", type=int, default=20)
    p.add_argument("--provider", default="openai")
    p.add_argument("--variants", type=int, default=2)
    p.add_argument("--names", nargs="*")
    p.add_argument("--shrink", type=int, default=5)
    p.add_argument("--outline", action="store_true")
    p.add_argument("--frames", choices=["all", "inventory"], default="all",
                   help="inventory: only the inventory picture goes to the model; other frames are pixelized as they are")
    p.add_argument("--opt", action="append", default=[])
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--open", action="store_true", help="open the review page when done")
    p = sub.add_parser("apply")
    p.add_argument("decisions")
    sub.add_parser("install")
    p = sub.add_parser("status")
    p.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    {"inventory": lambda a: inv_mod.run(), "select": cmd_select, "batch": cmd_batch, "apply": cmd_apply,
     "install": cmd_install, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
