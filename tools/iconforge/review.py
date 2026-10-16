"""
Contact sheets (PNG) and the per-batch review page (HTML) where you keep or reject each icon.
"""
from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

BG = (40, 40, 48, 255)
FG = (220, 220, 220, 255)


def fit(img: Image.Image, cell: int) -> Image.Image:
    """Scale to fit a cell: nearest-neighbour when enlarging so pixels stay crisp."""
    scale = min(cell / img.width, cell / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.NEAREST if scale >= 1 else Image.LANCZOS)


def contact_sheet(tiles: list, path: Path, cell: int = 96, cols: int = 10):
    """tiles: [(label, [images])]; each tile shows its images side by side with the label under them."""
    if not tiles:
        return
    per = max(len(imgs) for _, imgs in tiles)
    tw, th = cell * per + 8, cell + 22
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGBA", (tw * min(cols, len(tiles)), th * rows), BG)
    draw = ImageDraw.Draw(sheet)
    for i, (label, imgs) in enumerate(tiles):
        x0, y0 = (i % cols) * tw + 4, (i // cols) * th + 2
        for j, im in enumerate(imgs):
            f = fit(im.convert("RGBA"), cell)
            sheet.alpha_composite(f, (x0 + j * cell + (cell - f.width) // 2, y0 + (cell - f.height) // 2))
        draw.text((x0, y0 + cell + 2), label[:24], fill=FG)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def _data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Icon review: batch %(batch)s</title>
<style>
:root { --bg:#f4f1ea; --fg:#1f1d1a; --muted:#6b665d; --card:#fffdf8; --line:#d8d2c4; --keep:#2f7d3b; --reject:#a33b2a; --sel:#c9a227; }
@media (prefers-color-scheme: dark) { :root { --bg:#1b1a18; --fg:#ece7dc; --muted:#a39d91; --card:#262420; --line:#3b3832; --keep:#5fbf6c; --reject:#e2725b; --sel:#e0bb47; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, sans-serif; }
header { position:sticky; top:0; z-index:2; background:var(--bg); border-bottom:1px solid var(--line); padding:12px 16px; display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
header h1 { font-size:16px; margin:0 12px 0 0; }
header .count { color:var(--muted); }
button { font:inherit; padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
button.primary { background:var(--fg); color:var(--bg); border-color:var(--fg); }
main { padding:16px; display:grid; gap:16px; grid-template-columns:repeat(auto-fill, minmax(min(100%%, 520px), 1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.card.keep { border-color:var(--keep); box-shadow:0 0 0 2px var(--keep) inset; }
.card.reject { border-color:var(--reject); box-shadow:0 0 0 2px var(--reject) inset; }
.card h2 { font-size:14px; margin:0 0 4px; display:flex; justify-content:space-between; gap:8px; }
.card .meta { color:var(--muted); font-size:12px; margin-bottom:8px; }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end; }
figure { margin:0; text-align:center; font-size:11px; color:var(--muted); }
figure .tile { background:repeating-conic-gradient(#8885 0 25%%, #0000 0 50%%) 50%% / 16px 16px; border:2px solid transparent; border-radius:6px; padding:4px; display:flex; gap:6px; align-items:flex-end; cursor:pointer; }
figure.variant.chosen .tile { border-color:var(--sel); }
img.px { image-rendering:pixelated; display:block; }
.actions { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.actions input { flex:1; min-width:160px; font:inherit; padding:6px 8px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); }
.keep-btn.on { background:var(--keep); color:#fff; border-color:var(--keep); }
.reject-btn.on { background:var(--reject); color:#fff; border-color:var(--reject); }
</style></head>
<body>
<header>
  <h1>Icon review: batch %(batch)s</h1>
  <span class="count" id="count"></span>
  <span style="flex:1"></span>
  <button id="allkeep">Keep all undecided</button>
  <button class="primary" id="export">Save decisions.json</button>
</header>
<main id="cards"></main>
<script>
const BATCH = %(batch_json)s;
const ITEMS = %(items_json)s;
const KEY = "iconforge-" + BATCH;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} render(); }
function el(tag, attrs, ...kids) { const n = document.createElement(tag); Object.assign(n, attrs || {}); kids.forEach(k => n.append(k)); return n; }
function render() {
  const main = document.getElementById("cards"); main.textContent = "";
  let kept = 0, rejected = 0;
  for (const it of ITEMS) {
    const s = state[it.name] || (state[it.name] = { decision: null, variant: 0, note: "" });
    if (s.decision === "keep") kept++; if (s.decision === "reject") rejected++;
    const card = el("section", { className: "card" + (s.decision ? " " + s.decision : "") });
    card.append(el("h2", {}, el("span", { textContent: it.name }), el("span", { textContent: "attempt " + it.attempt })));
    card.append(el("div", { className: "meta", textContent: it.classes.join(", ") }));
    const row = el("div", { className: "row" });
    row.append(fig("original", it.original, false));
    it.variants.forEach((v, i) => {
      const f = fig((it.labels && it.labels[i]) || "variant " + (i + 1), v, true);
      if (s.variant === i) f.classList.add("chosen");
      f.onclick = () => { s.variant = i; s.decision = "keep"; save(); };
      row.append(f);
    });
    card.append(row);
    const note = el("input", { placeholder: "Note for the next attempt (rejects only)", value: s.note });
    note.oninput = () => { s.note = note.value; try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
    const keep = el("button", { className: "keep-btn" + (s.decision === "keep" ? " on" : ""), textContent: "Keep" });
    keep.onclick = () => { s.decision = s.decision === "keep" ? null : "keep"; save(); };
    const rej = el("button", { className: "reject-btn" + (s.decision === "reject" ? " on" : ""), textContent: "Reject" });
    rej.onclick = () => { s.decision = s.decision === "reject" ? null : "reject"; save(); };
    card.append(el("div", { className: "actions" }, keep, rej, note));
    main.append(card);
  }
  document.getElementById("count").textContent = ITEMS.length + " icons, " + kept + " kept, " + rejected + " rejected";
}
function fig(label, imgs, variant) {
  const tile = el("div", { className: "tile" });
  for (const im of imgs) tile.append(el("img", { className: "px", src: im.src, width: im.w, height: im.h, alt: label }));
  return el("figure", { className: variant ? "variant" : "" }, tile, el("figcaption", { textContent: label }));
}
document.getElementById("allkeep").onclick = () => { for (const it of ITEMS) { const s = state[it.name]; if (!s.decision) s.decision = "keep"; } save(); };
document.getElementById("export").onclick = () => {
  const out = { batch: BATCH, decisions: ITEMS.map(it => ({ name: it.name, ...state[it.name] })) };
  const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: "application/json" })), download: "decisions-" + BATCH + ".json" });
  document.body.append(a); a.click(); a.remove();
};
render();
</script>
</body></html>
"""


def review_page(batch: str, items: list, path: Path):
    """items: [{"name", "classes", "attempt", "original": [PIL images], "variants": [[PIL images], ...]}].
    Each image list is shown at in-game size (1x) and enlarged (3x), pixelated."""
    def views(imgs):
        out = []
        for im in imgs:
            out.append({"src": _data_url(im), "w": im.width, "h": im.height})
            out.append({"src": _data_url(im), "w": im.width * 3, "h": im.height * 3})
        return out
    data = [{"name": it["name"], "classes": it["classes"], "attempt": it["attempt"],
             "original": views(it["original"]), "variants": [views(v) for v in it["variants"]],
             "labels": it.get("labels", [])} for it in items]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE % {"batch": html.escape(batch), "batch_json": json.dumps(batch),
                            "items_json": json.dumps(data)}, encoding="utf-8")
