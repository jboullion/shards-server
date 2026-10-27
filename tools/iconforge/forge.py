"""
iconforge: restyle the fan-art item icons into sprites that match the original Meridian 59 art.

    python tools/iconforge/forge.py inventory                 # 1. find icons, flag fan art, contact sheets
    python tools/iconforge/forge.py select carrot onion       #    add icons to the work set (--off removes)
    python tools/iconforge/forge.py batch --provider openai   # 2-5. next 20 icons -> candidates + review page
    python tools/iconforge/forge.py apply decisions-003.json  # 6. record keeps/rejects from the review page
    python tools/iconforge/forge.py accept 003                #    or keep the whole batch as it is
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
    --opt qwen:lora=a,b --opt qwen:prefix="Pixel Art, "   provider options; "provider:" scopes one
                                 to that provider (unscoped ones go to all and change their cache keys)
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
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory as inv_mod  # noqa: E402
import sheets  # noqa: E402
import providers  # noqa: E402
from bgf import BGF, TRANSPARENT, Bitmap, load_palette  # noqa: E402
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
# Qwen-Image-Edit (providers.qwen) with the 8-step Lightning LoRA: 8 steps at cfg 1 instead of 30 at cfg 4,
# about 10x faster. --opt lora=... replaces the whole list, so keep Lightning in it when adding another.
QWEN_DEFAULTS = {"lora": "qwen-edit-2511-lightning-8step.safetensors", "lora_strength": "1", "steps": "8", "cfg": "1"}
# providers.qwen sends the references as one sheet, the second image. Qwen tends to keep the input's
# photographic rendering, so its prompt asks for a repaint in the sheet's style rather than a redraw.
QWEN_PROMPT = ("Repaint the item in the first image as a small hand-painted pixel-art inventory sprite, in exactly "
               "the style of the item sprites on the sheet in the second image: chunky visible pixels, a limited "
               "palette with few shades per colour, flat cel shading lit from the top left, a dark outline. Keep "
               "the same item, shape, silhouette, colours and orientation, but none of its photographic texture "
               "or smooth 3D shading. Do not copy the subjects of the second image. Output only the one item.")
REF_PROMPT = ("The last image is a sheet of original item sprites from the game: match their style exactly, "
              "not their subjects.")
# overrides.json mode "generate" on an item: a new icon from its description, the current art not sent
ITEM_GENERATE = ("Draw a new game inventory icon of %s. The image is a sheet of original item sprites from the game: "
                 "match their style exactly, but draw this item's own subject.")


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


def prompt_for(notes: list, provider: str = "openai", item: str = "", describe: str | None = None) -> str:
    """The request text: the full instruction prompt, or for the local img2img model (comfy) the
    short tag prompt from style.md's comfy-block. With `describe`, a new icon drawn from it (mode generate)."""
    if provider == "comfy":
        block = re.search(r"<!-- comfy-block -->(.*?)<!-- /comfy-block -->", STYLE.read_text(encoding="utf-8"), re.S)
        text = " ".join(block.group(1).split()).replace("{item}", item)
        return text + (", " + ", ".join(n.rstrip(".") for n in notes) if notes else "")
    style, _ = style_text()
    if describe:
        parts = [ITEM_GENERATE % describe.rstrip(".")]
    else:
        parts = [QWEN_PROMPT] if provider == "qwen" else [BASE_PROMPT, REF_PROMPT]
    if notes:
        parts.append("Corrections from the last review: %s." % "; ".join(n.rstrip(".") for n in notes))
    parts.append(style)
    return " ".join(parts)


# ------------------------------------------------------------------------------------- spells

OVERRIDES_FILE = DATA / "overrides.json"
SPELL_SIZES = (16, 64)  # classic spell icons: a 16x16 frame (group 1) and a 64x64 frame (group 2), shrink 1
SPELL_RESTYLE = ("Redraw the spell icon in the first image as a classic game spell icon. Keep its subject, "
                 "composition and colours. The last image is a sheet of original spell icons from the game: match "
                 "their style exactly, not their subjects.")
SPELL_GENERATE = ("Draw a new spell icon for a fantasy RPG spell, showing %s. The image is a sheet of original spell "
                  "icons from the game: match their style exactly, but draw this spell's own subject.")


def overrides() -> dict:
    return {k: v for k, v in load_json(OVERRIDES_FILE, {}).items() if not k.startswith("_")}


def is_spell(entry: dict) -> bool:
    return "spell" in entry.get("kind", "item").split("/")


def spell_style() -> tuple:
    text = STYLE.read_text(encoding="utf-8")
    block = re.search(r"<!-- spell-block -->(.*?)<!-- /spell-block -->", text, re.S)
    refs = re.search(r"<!-- spell-refs -->(.*?)<!-- /spell-refs -->", text, re.S)
    style = " ".join(line.strip() for line in block.group(1).strip().splitlines() if line.strip())
    return style, [line.strip() for line in refs.group(1).splitlines() if line.strip()]


def spell_refs(pal) -> list:
    imgs = []
    for n in spell_style()[1]:
        p = inv_mod.CLASSIC / n
        if not p.exists():
            print("!! spell reference %s not found in %s" % (n, inv_mod.CLASSIC))
            continue
        b = BGF.read(p)
        big = b.bitmaps[b.groups[1][0]] if len(b.groups) > 1 else max(b.bitmaps, key=lambda x: x.w * x.h)
        imgs.append(on_canvas(big.rgba(pal), 512))
    return imgs


def spell_prompt(override: dict, notes: list) -> str:
    parts = [SPELL_GENERATE % override["describe"].rstrip(".") if override.get("mode") == "generate" else SPELL_RESTYLE]
    if notes:
        parts.append("Corrections from the last review: %s." % "; ".join(n.rstrip(".") for n in notes))
    parts.append(spell_style()[0])
    return " ".join(parts)


def spell_bitmaps(gen: Image.Image, pal) -> list:
    """The 16x16 and 64x64 frames of a classic spell icon, both from one generated image."""
    out = []
    for size in SPELL_SIZES:
        box = Bitmap(size, size, 0, 0, [], bytes(size * size))  # an all-opaque stand-in: fit to the whole square
        out.append(pixelize(gen, box, 1, 1, pal))
    return out


# ------------------------------------------------------------------------------------- creatures

MONSTER_SHEET = ("The first image is a sheet of %d animation frames of one creature (%s), one frame per cell on a "
                 "magenta background. Redraw every frame in place as %s. Keep each frame in its own cell with the "
                 "same pose, position, size and facing as the original, so the frames still animate together, and "
                 "make the creature look identical from frame to frame. The last image is a sheet of original "
                 "creature sprites from the game: match their rendering style, not their subjects.")
MONSTER_SINGLE = ("Redraw the creature in the first image as %s, in the same pose, position, size and facing. The "
                  "last image is a sheet of original creature sprites from the game: match their rendering style, "
                  "not their subjects.")
MONSTER_PACK = ("The first image is a sheet of %d different creatures, one per cell on a magenta background, in "
                "reading order (left to right, top to bottom): %s. Redraw each one in its own cell, in the same pose, "
                "position, size and facing; keep every cell separate. The last image is a sheet of original creature "
                "sprites from the game: match their rendering style, not their subjects.")


def is_monster(entry: dict) -> bool:
    return "monster" in entry.get("kind", "item").split("/")


def monster_style() -> tuple:
    text = STYLE.read_text(encoding="utf-8")
    block = re.search(r"<!-- monster-block -->(.*?)<!-- /monster-block -->", text, re.S)
    refs = re.search(r"<!-- monster-refs -->(.*?)<!-- /monster-refs -->", text, re.S)
    style = " ".join(line.strip() for line in block.group(1).strip().splitlines() if line.strip())
    return style, [line.strip() for line in refs.group(1).splitlines() if line.strip()]


def monster_refs(pal) -> list:
    imgs = []
    for n in monster_style()[1]:
        p = inv_mod.CLASSIC / n
        if not p.exists():
            print("!! creature reference %s not found in %s" % (n, inv_mod.CLASSIC))
            continue
        b = BGF.read(p)
        first = b.bitmaps[b.groups[0][0]] if b.groups and b.groups[0] else b.bitmaps[0]
        imgs.append(on_canvas(first.rgba(pal), 512))
    return imgs


def monster_what(override: dict, classes: list) -> str:
    return override.get("describe") or "the same creature (%s)" % item_words(classes)


def monster_prompt(override: dict, classes: list, n_frames: int, notes: list, n_angles: int = 1) -> str:
    what = monster_what(override, classes)
    seen = ("all seen from the same angle" if n_angles == 1 else
            "seen from %d angles, the frames grouped by angle in reading order" % n_angles)
    parts = [MONSTER_SHEET % (n_frames, seen, what) if n_frames > 1 else MONSTER_SINGLE % what]
    if notes:
        parts.append("Corrections from the last review: %s." % "; ".join(n.rstrip(".") for n in notes))
    parts.append(monster_style()[0])
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


# Set when a provider says the account is out of credits: queued requests stop instead of all failing.
STOP = threading.Event()
OUT_OF_CREDITS = ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit")


def cache_key(spec, images, text, opts, variant) -> str:
    return digest(spec, variant, text, json.dumps(opts, sort_keys=True), *images)


def store(key: str, img: Image.Image, info: dict):
    CACHE.mkdir(parents=True, exist_ok=True)
    img.save(CACHE / (key + ".png"))
    (CACHE / (key + ".json")).write_text(json.dumps(info))


def generate(spec, images, text, opts, variant, cached_only=False) -> tuple:
    """A cached model call -> (image, info). cached_only: never call the model."""
    key = cache_key(spec, images, text, opts, variant)
    png, meta = CACHE / (key + ".png"), CACHE / (key + ".json")
    if png.exists():
        return Image.open(png).copy(), json.loads(meta.read_text()) if meta.exists() else {}
    if cached_only:
        raise LookupError("not cached")
    if STOP.is_set():
        raise LookupError("stopped: out of credits")
    img, info = providers.run(spec, images, text, opts)
    store(key, img, info)
    return img, info


BATCHES = BUILD / "openai_batches"


def run_openai_batch(jobs: list, wait_minutes: float) -> bool:
    """Send every uncached OpenAI job as one Batch API request (half price), wait for it and store the
    results in the cache, so the normal pass then finds them. A batch already submitted for the same
    requests is resumed rather than paid for again. False when it hasn't finished within the wait."""
    todo = {}
    for n, fi, s, v, images, text, o in jobs:
        prov, model = providers.split(s)
        if prov != "openai":
            continue
        key = cache_key(s, images, text, o, v)
        if not (CACHE / (key + ".png")).exists():
            todo[key] = (images, text, model, o)
    if not todo:
        return True
    BATCHES.mkdir(parents=True, exist_ok=True)
    pending = None
    for p in sorted(BATCHES.glob("*.json")):
        rec = json.loads(p.read_text())
        if not rec.get("collected") and set(todo) <= set(rec["keys"]):
            pending = (p, rec)
            break
    if pending is None:
        lines = [providers.openai_batch_line(k, im, t, m, o) for k, (im, t, m, o) in todo.items()]
        b = providers.openai_batch_create(lines)
        p = BATCHES / ("%s.json" % b["id"])
        rec = {"id": b["id"], "keys": sorted(todo), "submitted": time.strftime("%Y-%m-%d %H:%M"), "collected": False}
        save_json(p, rec)
        pending = (p, rec)
        print("submitted OpenAI batch %s: %d requests (half price; results within 24 h)" % (b["id"], len(lines)))
    else:
        print("resuming OpenAI batch %s (%d requests)" % (pending[1]["id"], len(pending[1]["keys"])))
    p, rec = pending
    deadline = time.time() + wait_minutes * 60
    while True:
        b = providers.openai_batch_get(rec["id"])
        counts = b.get("request_counts") or {}
        print("  batch %s: %s (%s/%s done, %s failed)" % (rec["id"], b["status"], counts.get("completed", 0),
                                                         counts.get("total", "?"), counts.get("failed", 0)))
        if b["status"] in ("completed", "failed", "expired", "cancelled"):
            break
        if time.time() > deadline:
            print("still running; run the same command again later to collect it (no new charge)")
            return False
        time.sleep(30)
    got = providers.openai_batch_results(b)
    errors = 0
    for k, r in got.items():
        if "image" in r:
            store(k, r["image"], {"provider": "openai", "model": todo.get(k, (None, None, "?"))[2],
                                  "usage": r.get("usage"), "batch": rec["id"]})
        else:
            errors += 1
            if errors <= 5:
                print("!! batch request %s: %s" % (k, str(r["error"])[:300]))
    rec.update(collected=True, status=b["status"], errors=errors)
    save_json(p, rec)
    print("collected %d images from batch %s (%d errors)" % (len(got) - errors, rec["id"], errors))
    return True


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
    # --opt key=value applies to every provider; --opt qwen:key=value only to that one (so a LoRA
    # setting never changes another provider's requests or their cache keys)
    opts = {}
    for o in a.opt:
        k, v = o.split("=", 1)
        prov, _, key = k.rpartition(":")
        opts.setdefault(prov, {})[key] = v

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
    # the classic references go as ONE tiled image: six separate images were most of a request's input tokens
    refs = [providers.reference_sheet(style_refs(pal))]
    srefs = [providers.reference_sheet(spell_refs(pal))] if any(is_spell(inv[n]) for n in names) else []
    mrefs = [providers.reference_sheet(monster_refs(pal))] if any(is_monster(inv[n]) for n in names) else []
    plans = {}
    ovr = overrides()
    print("batch %s: %d icons x %s x %d variants, %d style refs" % (batch, len(names), ",".join(specs), a.variants, len(refs)))

    # one job per (icon, distinct frame, provider, variant); a spell icon is one job (frame -1)
    sources, jobs, inv_fi_of = {}, [], {}
    singles = []  # one-bitmap creatures (corpses): packed several to a request below
    for n in names:
        b = BGF.read(Path(inv[n]["source"]))
        if is_monster(inv[n]):
            plan = plans[n] = sheets.plan(b)
            sources[n] = (b, "monster", {})
            inv_fi_of[n] = 1000
            if len(plan) == 1 and len(plan[0]) == 1 and a.pack_singles > 1:
                singles.append(n)
                continue
            # pack whole angles into a sheet until it holds about --sheet-frames frames (at least one angle)
            packed, angles, cur, cur_angles = [], [], [], 0
            for frames in plan:
                if cur and len(cur) + len(frames) > a.sheet_frames:
                    packed.append(cur)
                    angles.append(cur_angles)
                    cur, cur_angles = [], 0
                cur = cur + frames
                cur_angles += 1
            packed.append(cur)
            angles.append(cur_angles)
            plan = plans[n] = packed
            notes = icon_state(state, n)["notes"]
            texts = {}
            for si, frames in enumerate(plan):
                text = monster_prompt(ovr.get(n, {}), inv[n]["classes"], len(frames), notes, angles[si])
                texts[si] = text
                images = [sheets.compose(b, frames, pal)] + mrefs
                for s in specs:
                    for v in range(a.variants):
                        jobs.append((n, 1000 + si, s, v, images, text,
                                     {**opts.get("", {}), **opts.get(providers.split(s)[0], {})}))
            sources[n] = (b, "monster", {s: texts.get(0, "") for s in specs})
            continue
        if is_spell(inv[n]):
            o_n = ovr.get(n, {})
            text = spell_prompt(o_n, icon_state(state, n)["notes"])
            sources[n] = (b, None, {s: text for s in specs})
            inv_fi_of[n] = -1
            if o_n.get("mode") == "generate":
                images = list(srefs)  # a fresh icon: the current art is not sent
            else:
                images = [on_canvas(max(b.bitmaps, key=lambda x: x.w * x.h).rgba(pal))] + srefs
            for s in specs:
                for v in range(a.variants):
                    jobs.append((n, -1, s, v, images, text, {**opts.get("", {}), **opts.get(providers.split(s)[0], {})}))
            continue
        frames = unique_frames(b)
        notes = icon_state(state, n)["notes"]
        # mode generate: one new drawing from the description, pixelized into every frame (inventory and
        # ground show the same object); the current art is not sent, only the style references
        fresh = ovr.get(n, {}).get("mode") == "generate" and ovr.get(n, {}).get("describe")
        texts = {s: prompt_for(notes, providers.split(s)[0], item_words(inv[n]["classes"]), fresh or None) for s in specs}
        sources[n] = (b, frames, texts)
        inv_fi = inv_fi_of[n] = frames[b.bitmaps.index(inv_mod.inventory_bitmap(b))]
        for fi in sorted(set(frames.values())):
            if (a.frames == "inventory" or fresh) and fi != inv_fi:
                continue  # pixelized straight from the original (or the one new drawing) below
            src = on_canvas(b.bitmaps[fi].rgba(pal))
            for s in specs:
                for v in range(a.variants):
                    prov = providers.split(s)[0]
                    o = {**opts.get("", {}), **opts.get(prov, {})}
                    if prov == "comfy":
                        o = {**COMFY_DEFAULTS, **o}
                    if prov == "qwen":
                        o = {**QWEN_DEFAULTS, **o}
                    if prov in ("comfy", "qwen"):
                        o = {**o, "seed": int(digest(n, fi, v)[:8], 16)}
                    jobs.append((n, fi, s, v, list(refs) if fresh else [src] + refs, texts[s], o))

    packs = {}
    for k in range(0, len(singles), max(1, a.pack_singles)):
        group = singles[k:k + a.pack_singles]
        pid = "pack%d" % (k // a.pack_singles)
        packs[pid] = group
        cells = [(sources[n][0], [0]) for n in group]
        listing = "; ".join("%d) %s" % (i + 1, monster_what(ovr.get(n, {}), inv[n]["classes"])) for i, n in enumerate(group))
        text = " ".join([MONSTER_PACK % (len(group), listing) if len(group) > 1 else
                         MONSTER_SINGLE % monster_what(ovr.get(group[0], {}), inv[group[0]]["classes"]),
                         monster_style()[0]])
        for n in group:
            sources[n] = (sources[n][0], "monster", {s: text for s in specs})
        images = [sheets.compose_many(cells, pal)] + mrefs
        for s in specs:
            for v in range(a.variants):
                jobs.append((pid, 1000, s, v, images, text, {**opts.get("", {}), **opts.get(providers.split(s)[0], {})}))

    if a.batch_api and not a.cached_only:
        if not run_openai_batch(jobs, a.batch_wait):
            return

    results, failures = {}, []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(generate, s, images, text, o, v, a.cached_only): (n, fi, s, v)
                for n, fi, s, v, images, text, o in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            n, fi, s, v = futs[f]
            try:
                results[(n, fi, s, v)] = f.result()[0]
            except LookupError as e:
                failures.append("%s %s v%d: not generated (%s)" % (n, s, v, e))
            except Exception as e:  # keep the rest of the batch going
                if any(k in str(e) for k in OUT_OF_CREDITS) and not STOP.is_set():
                    STOP.set()
                    print("\n!! the %s account is out of credits: stopping this batch" % s)
                failures.append("%s frame %d %s v%d: %s" % (n, fi, s, v, e))
            print("  %d/%d" % (i, len(jobs)), end="\r")
    print()
    # a packed sheet of several creatures: give each its own cell
    for pid, group in packs.items():
        for s in specs:
            for v in range(a.variants):
                gen = results.pop((pid, 1000, s, v), None)
                if gen is None:
                    continue
                for n, cell in zip(group, sheets.slice_many(gen, [(sources[n][0], [0]) for n in group])):
                    results[(n, 1000, s, v)] = cell
    for msg in failures if len(failures) <= 20 else failures[:20] + ["... and %d more" % (len(failures) - 20)]:
        print("!! " + msg)

    manifest, review_items, sheet = {"batch": batch, "providers": specs, "items": []}, [], []
    for n in names:
        b, frames, texts = sources[n]
        output = ovr.get(n, {}).get("output", n)
        item = {"name": n, "output": output, "prompts": texts, "variants": []}
        if frames == "monster":
            inv_frame = b.bitmaps[plans[n][0][0]]
        elif frames is None:
            inv_frame = max(b.bitmaps, key=lambda x: x.w * x.h)
        else:
            inv_frame = inv_mod.inventory_bitmap(b)
        variant_imgs, variant_labels = [], []
        for s in specs:
            for v in range(a.variants):
                if (n, inv_fi_of[n], s, v) not in results:
                    continue  # the model call failed or wasn't made: no candidate
                if frames == "monster":  # creature: every angle sheet, rebuilt frame by frame
                    plan = plans[n]
                    got = [results.get((n, 1000 + si, s, v)) for si in range(len(plan))]
                    if any(g is None for g in got):
                        print("!! %s %s v%d: %d of %d sheets missing" % (n, s, v, sum(g is None for g in got), len(plan)))
                        continue
                    cand = sheets.rebuild(b, plan, got, pal)
                    if cand.missing:
                        print("!! %s %s v%d: %d of %d frames came back empty and keep the original art: %s"
                              % (n, s, v, len(cand.missing), len(b.bitmaps), cand.missing))
                    path = out / output.removesuffix(".bgf") / ("v%d.bgf" % len(item["variants"]))
                    cand.write(path)
                    got[0].convert("RGB").resize((512, 512)).save(path.with_suffix(".raw.png"))
                    if cand.groups:
                        for slot in sorted({0, min(2, max(len(g) for g in cand.groups) - 1)}):
                            sheets.animation_gif(cand, pal, path.with_name("%s.angle%d.gif" % (path.stem, slot)), slot)
                            sheets.animation_gif(b, pal, path.with_name("original.angle%d.gif" % slot), slot)
                    item["variants"].append({"spec": s, "variant": v, "bgf": str(path.relative_to(REPO))})
                    variant_imgs.append([cand.bitmaps[i].rgba(pal) for i in plan[0][:12]])
                    variant_labels.append("%s %d" % (s, v + 1))
                    continue
                if frames is None:  # spell icon: 16x16 + 64x64 from one image
                    gen = results[(n, -1, s, v)]
                    small, big = spell_bitmaps(gen, pal)
                    cand = BGF(name=output.removesuffix(".bgf"), shrink=1, groups=[[0], [1]], bitmaps=[small, big])
                    path = out / output.removesuffix(".bgf") / ("v%d.bgf" % len(item["variants"]))
                    cand.write(path)
                    gen.convert("RGB").resize((256, 256)).save(path.with_suffix(".raw.png"))
                    item["variants"].append({"spec": s, "variant": v, "bgf": str(path.relative_to(REPO))})
                    variant_imgs.append([big.rgba(pal), small.rgba(pal)])
                    variant_labels.append("%s %d" % (s, v + 1))
                    continue
                try:
                    new = {}
                    for fi in sorted(set(frames.values())):
                        gen = results.get((n, fi, s, v))
                        if gen is None and ovr.get(n, {}).get("mode") == "generate":
                            gen = results.get((n, inv_fi_of[n], s, v))  # the one new drawing, every frame
                        if gen is None and a.frames == "inventory":
                            gen = on_canvas(b.bitmaps[fi].rgba(pal))
                        if gen is None:
                            raise KeyError("frame %d failed" % fi)
                        o_n = ovr.get(n, {})
                        # `orig_shrink`: the size to fit it to, where the original's own shrink is wrong
                        new[fi] = pixelize(gen, b.bitmaps[fi], o_n.get("orig_shrink", b.shrink), a.shrink, pal, outline=a.outline,
                                           fit_object=o_n.get("mode") == "generate",
                                           greys=o_n.get("recolour") == "greys")
                except (KeyError, ValueError) as e:
                    print("!! %s %s v%d: %s" % (n, s, v, e))
                    continue
                cand = BGF(name=b.name, shrink=a.shrink, groups=[list(g) for g in b.groups],
                           bitmaps=[new[frames[i]] for i in range(len(b.bitmaps))])
                k = len(item["variants"])
                path = out / n.removesuffix(".bgf") / ("v%d.bgf" % k)
                cand.write(path)
                raw = results[(n, inv_fi_of[n], s, v)]
                raw.convert("RGB").resize((256, 256)).save(path.with_suffix(".raw.png"))
                item["variants"].append({"spec": s, "variant": v, "bgf": str(path.relative_to(REPO))})
                variant_imgs.append([inv_mod.inventory_bitmap(cand).rgba(pal)])
                variant_labels.append("%s %d" % (s, v + 1))
        manifest["items"].append(item)
        if variant_imgs:
            ref_size = variant_imgs[0][0].size
            if frames == "monster":
                orig = [b.bitmaps[i].rgba(pal) for i in plans[n][0][:12]]
            else:
                orig = inv_frame.rgba(pal)
                orig = [orig.resize(ref_size, Image.LANCZOS) if orig.width > ref_size[0] * 2 else orig]
            review_items.append({"name": n, "classes": inv[n]["classes"], "attempt": icon_state(state, n)["attempts"] + 1,
                                 "original": orig, "variants": variant_imgs, "labels": variant_labels})
            sheet.append((n.removesuffix(".bgf"), [orig[0]] + [vi[0] for vi in variant_imgs]))
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
            output = item.get("output", n)
            shutil.copyfile(REPO / v["bgf"], KEPT / output)
            st.update(status="kept", output=output, chosen={"batch": batch, **v})
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


def cmd_accept(a):
    """Keep every icon of a batch with its first candidate, as if each were kept on the review page."""
    manifest = load_json(BUILD / "batches" / a.batch / "manifest.json", None)
    if manifest is None:
        raise SystemExit("no manifest for batch %s" % a.batch)
    path = BUILD / "batches" / a.batch / ("decisions-%s-accepted.json" % a.batch)
    save_json(path, {"batch": a.batch, "decisions": [{"name": it["name"], "decision": "keep", "variant": 0, "note": ""}
                                                     for it in manifest["items"] if it["variants"]]})
    a.decisions = str(path)
    cmd_apply(a)


def cmd_install(a):
    state = load_state()
    INSTALL.mkdir(parents=True, exist_ok=True)
    done = 0
    for n, st in sorted(state["icons"].items()):
        output = st.get("output", n)
        src = KEPT / output
        if st["status"] in ("kept", "installed") and src.exists():
            shutil.copyfile(src, INSTALL / output)
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
    p.add_argument("--sheet-frames", type=int, default=24,
                   help="creatures: whole angles go into one request until it holds about this many frames")
    p.add_argument("--pack-singles", type=int, default=4,
                   help="creatures with one image (corpses): this many share one request")
    p.add_argument("--batch-api", action="store_true",
                   help="OpenAI: send the requests as one Batch API job (half price, may take hours)")
    p.add_argument("--batch-wait", type=float, default=180, help="minutes to wait for a Batch API job (default 180)")
    p.add_argument("--cached-only", action="store_true",
                   help="no model calls: build the page from results already generated (e.g. after stopping a batch)")
    p = sub.add_parser("apply")
    p.add_argument("decisions")
    p = sub.add_parser("accept", help="keep every icon of a batch (first candidate) without reviewing")
    p.add_argument("batch", help="batch number, e.g. 010")
    sub.add_parser("install")
    p = sub.add_parser("status")
    p.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    {"inventory": lambda a: inv_mod.run(), "select": cmd_select, "batch": cmd_batch, "apply": cmd_apply, "accept": cmd_accept,
     "install": cmd_install, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
