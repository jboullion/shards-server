// The guild cloak's art (docs/adr/0022-guilds.md decision 22), drawn by script rather than by a
// model, so its views agree by construction.
//
//   node tools/iconforge/cloak.ts [--browser <meridian-browser>] [--install]
//
// A cape is modelled as a sheet hanging from the shoulders and wrapped half round the back,
// sized from the union of every torso of that gender (aligned on the neck, the head's hotspot),
// and seen from each of a torso's six views. The cloth is the art's red ramp and the trim its
// dark-blue ramp, the two ramps a guild's colour translation changes (XlatTable GUILDCOLOR), so a
// cloak takes its guild's colours as the shield does. Writes, into build/iconforge/cloak/:
//
// - cloakm.bgf, cloakf.bgf: the capes, one bitmap per torso view, hanging from the head's
//   hotspot (HS_HEAD) with the torso's groups, so they follow whatever pose the body shows. Each
//   frame carries our depth marker (meridian-browser packages/render/src/sprites.ts): over the
//   body and the near arm from the back and the sides (HS_OVER_BODY), behind the figure from the
//   front and the front quarters (HS_BEHIND_FIGURE). The side views sit a little further back
//   and, for men, higher on the back (SIDE_BACK, sideLift), as the user placed them.
// - preview.png: both genders in a shirt, a robe and plate, from every view, in a few guild
//   colours, drawn by meridian-browser's own compositor.
//
// --install copies the BGFs to resource/graphics/shards/, which the asset build serves first.

import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { deflateSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const { values: opt } = parseArgs({
  options: { browser: { type: "string", default: "E:/2026_Experiments/meridian-browser" }, install: { type: "boolean", default: false } },
});
const BROWSER = opt.browser!;
const ASSETS = join(BROWSER, "dist", "assets");
const OUT = join(REPO, "build", "iconforge", "cloak");
const INSTALL = join(REPO, "resource", "graphics", "shards");
mkdirSync(OUT, { recursive: true });

const mod = (p: string) => import(pathToFileURL(join(BROWSER, p)).href);
const { parseBgf, encodeBgf, parsePaletteBin, paletteRgba } = await mod("packages/formats/src/index.ts");
const { XlatTable } = await mod("packages/render/src/xlat.ts");
const { compositeSprite, frameFor, HS_OVER_BODY, HS_BEHIND_FIGURE } = await mod("packages/render/src/sprites.ts");

const pal = parsePaletteBin(new Uint8Array(readFileSync(join(ASSETS, "palette.bin"))));
const rgba: Uint8Array = paletteRgba(pal);
const xl = new XlatTable(pal.rgb, existsSync(join(ASSETS, "lightpal.bin")) ? new Uint8Array(readFileSync(join(ASSETS, "lightpal.bin"))) : null);
const cache = new Map<string, any>();
async function bgf(name: string): Promise<any> {
  if (!cache.has(name)) cache.set(name, await parseBgf(new Uint8Array(readFileSync(join(ASSETS, name)))));
  return cache.get(name);
}

const T = 254; // TRANSPARENT_INDEX
const HS_HEAD = 1;
const HS_NOSE = 14;
const HS_LEGS = 41;
const CLOTH = 0x10; // the art's red ramp: the guild's first colour
const TRIM = 0x90; // its dark-blue ramp: the second
const SHRINK = 4; // as the torsos

const GENDERS = {
  male: {
    torsos: ["bta.bgf", "btae.bgf", "btc.bgf", "bte.bgf", "btg.bgf", "bti.bgf", "btk.bgf", "btm.bgf"],
    head: "phax.bgf", legs: "bfa.bgf", right: "bra.bgf", left: "bla.bgf", out: "cloakm.bgf", sideLift: 5,
    preview: [["bta.bgf", "bfa.bgf", "bla.bgf"], ["btc.bgf", "bfc.bgf", "blc.bgf"], ["bte.bgf", "bfa.bgf", "bla.bgf"]],
  },
  female: {
    torsos: ["btb.bgf", "btbe.bgf", "btd.bgf", "btf.bgf", "bth.bgf", "btj.bgf", "btl.bgf", "btn.bgf"],
    head: "phkx.bgf", legs: "bfb.bgf", right: "brb.bgf", left: "blb.bgf", out: "cloakf.bgf", sideLift: 0,
    preview: [["btb.bgf", "bfb.bgf", "blb.bgf"], ["btd.bgf", "bfd.bgf", "bld.bgf"], ["btf.bgf", "bfb.bgf", "blb.bgf"]],
  },
} as const;

/** A torso's six views, as its stand group lays them out over the eight angle slots: 0 front, 3 back */
const BACK_VIEW = 3;
/** The two side views (angle slots 2 and 6) */
const SIDE_VIEWS = [2, 4];
/** How many base pixels further back the side views sit */
const SIDE_BACK = 1;

const hotspot = (b: any, n: number) => b.hotspots.find((h: any) => Math.abs(h.number) === n) ?? null;

// ---- measuring the body ----

interface Mask {
  set: Set<string>;
  add(x: number, y: number): void;
  rowExtent(y: number): [number, number] | null;
}
function mask(): Mask {
  const set = new Set<string>();
  return {
    set,
    add: (x, y) => set.add(`${x},${y}`),
    rowExtent(y) {
      let lo = Infinity, hi = -Infinity;
      for (const k of set) {
        const [x, yy] = k.split(",").map(Number);
        if (yy === y) { lo = Math.min(lo, x); hi = Math.max(hi, x); }
      }
      return lo === Infinity ? null : [lo, hi];
    },
  };
}

/** Every torso's opaque pixels in one view, every pose (or just standing), relative to the neck */
async function unionMask(torsos: readonly string[], view: number, standOnly = false): Promise<Mask> {
  const m = mask();
  for (const name of torsos) {
    const t = await bgf(name);
    for (const g of standOnly ? [t.groups[0]] : t.groups) {
      const idx = g[[0, 1, 2, 3, 6, 7][view]];
      if (idx === undefined) continue;
      const b = t.bitmaps[idx];
      const h = hotspot(b, HS_HEAD);
      if (!h) continue;
      for (let y = 0; y < b.height; y++)
        for (let x = 0; x < b.width; x++) if (b.pixels[y * b.width + x] !== T) m.add(x - h.x, y - h.y);
    }
  }
  return m;
}

/** Which way a figure faces on screen in the angle slot 2 (a side view): +1 right, -1 left, by the nose */
async function sideFacing(head: string): Promise<number> {
  const h = await bgf(head);
  const b = frameFor(h, 2 * 512, 0);
  const nose = hotspot(b, HS_NOSE);
  return nose && nose.x > b.width / 2 ? 1 : -1;
}

/** From the neck down to the soles, in base pixels: the whole standing figure, drawn by the compositor */
async function feetBelowNeck(g: (typeof GENDERS)[keyof typeof GENDERS]): Promise<number> {
  const torso = await bgf(g.torsos[0]);
  const c = compositeSprite(
    {
      base: { bgf: torso, group: 0, translation: 0, hotspot: 0 },
      overlays: [
        { bgf: await bgf(g.legs), group: 0, translation: 0, hotspot: HS_LEGS },
        { bgf: await bgf(g.head), group: 0, translation: 0, hotspot: HS_HEAD },
      ],
      viewAngle: 0,
      faceApart: true,
    },
    xl,
  );
  const neck = hotspot(frameFor(torso, 0, 0), HS_HEAD);
  return c.height / c.resolution - c.originY / c.resolution - neck.y;
}

// ---- the cape ----

interface Shape {
  /** Half the shoulders' width, the body's half depth, the neck's offset from the body's centre line */
  shoulder: number;
  depth: number;
  /** Neck to hem */
  length: number;
}

interface Frame {
  width: number;
  height: number;
  xOffset: number;
  yOffset: number;
  pixels: Uint8Array;
  hotspots: { number: number; x: number; y: number }[];
}

/** A view's angle round the figure (radians): slots 0..7 at 45° steps; our six views use slots 0, 1, 2, 4, 6, 7 */
const VIEW_ANGLE = [0, 1, 2, 4, 6, 7].map((s) => (s * Math.PI) / 4);

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * The cape seen from one view, in base pixels relative to the neck. The sheet: θ across (0 the
 * middle of the back, ±π/2 the edges, at the shoulders' sides), v down (0 the shoulders, 1 the
 * hem). It widens and stands further off the back towards the hem, with folds that deepen as it
 * falls. Body space: x to the figure's right, z forward, y down.
 */
function drawCape(shape: Shape, view: number, facing: number, sideLift: number): Frame {
  const phi = VIEW_ANGLE[view] * facing;
  const side = SIDE_VIEWS.includes(view);
  const marker = view === BACK_VIEW || side ? HS_OVER_BODY : HS_BEHIND_FIGURE;
  const lift = side ? sideLift : 0;
  const back = side ? SIDE_BACK : 0;
  const zbuf = new Map<string, { z: number; idx: number }>();
  const FOLDS = 7;
  const smooth = (e0: number, e1: number, x: number) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
  /** The hem rides up and down with the folds */
  const hemAt = (th: number) => 1 + 0.025 * Math.sin(FOLDS * th + 0.4);
  const point = (th: number, v: number) => {
    // round over the shoulders, then falling wider than the arms
    const w = shape.shoulder * (0.78 + 0.32 * smooth(0, 0.18, v)) + shape.shoulder * 0.38 * Math.pow(v, 0.9) + 1;
    const d = lerp(shape.depth + 1, shape.depth + 4, v);
    const fold = (0.5 + 2.3 * v) * Math.sin(FOLDS * th + 0.4);
    const x = w * Math.sin(th) + 0.6 * v * Math.sin(FOLDS * th + 0.4);
    const z = -(d + fold) * Math.cos(th) - back;
    // over the shoulders the top edge falls a little at the sides
    const y = 2 - lift + 6 * Math.sin(th) ** 2 * (1 - smooth(0, 0.2, v)) + v * shape.length;
    return { x, y, z };
  };
  // light from the viewer's upper left
  const L = (() => { const l = [-0.8, -0.45, 0.4]; const n = Math.hypot(...l); return l.map((c) => c / n); })();
  const N_TH = 260, N_V = 320;
  for (let i = 0; i <= N_TH; i++) {
    const th = -Math.PI / 2 + (Math.PI * i) / N_TH;
    const hem = hemAt(th);
    for (let j = 0; j <= N_V * 1.04; j++) {
      const v = j / N_V;
      if (v > hem) break;
      const p = point(th, v);
      // into screen space: x right, y down, z towards the viewer
      const sx = p.x * Math.cos(phi) + p.z * Math.sin(phi);
      const sz = -p.x * Math.sin(phi) + p.z * Math.cos(phi);
      // the surface's normal, from its neighbours, also on screen
      const a = point(th + 0.01, v), b = point(th, Math.min(1, v + 0.01));
      const ta = [a.x - p.x, a.y - p.y, a.z - p.z], tb = [b.x - p.x, b.y - p.y, b.z - p.z];
      let n = [ta[1] * tb[2] - ta[2] * tb[1], ta[2] * tb[0] - ta[0] * tb[2], ta[0] * tb[1] - ta[1] * tb[0]];
      // outward: away from the body's axis
      if (n[0] * Math.sin(th) - n[2] * Math.cos(th) < 0) n = n.map((c) => -c);
      const ns = [n[0] * Math.cos(phi) + n[2] * Math.sin(phi), n[1], -n[0] * Math.sin(phi) + n[2] * Math.cos(phi)];
      const len = Math.hypot(...ns) || 1;
      const outside = ns[2] > 0;
      const nn = (outside ? ns : ns.map((c) => -c)).map((c) => c / len);
      // and darker in the folds' valleys
      const valley = 0.16 * (0.4 + v) * Math.cos(FOLDS * th + 0.4);
      const lit = clamp(0.15 + 0.85 * Math.max(0, nn[0] * L[0] + nn[1] * L[1] + nn[2] * L[2]) + valley, 0, 1);
      const edge = Math.abs(th) > Math.PI / 2 - 0.09;
      const trim = v > hem - 0.06 || v < 0.03 || edge;
      let shade = Math.round((1 - lit) * 12) + (outside ? 0 : 2);
      const ramp = trim ? TRIM : CLOTH;
      shade = clamp(shade, 1, 13);
      const key = `${Math.floor(sx)},${Math.floor(p.y)}`;
      const was = zbuf.get(key);
      if (!was || sz > was.z) zbuf.set(key, { z: sz, idx: ramp + shade });
    }
  }
  // bounds
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const k of zbuf.keys()) {
    const [x, y] = k.split(",").map(Number);
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  const width = x1 - x0 + 1, height = y1 - y0 + 1;
  const pixels = new Uint8Array(width * height).fill(T);
  for (const [k, { idx }] of zbuf) {
    const [x, y] = k.split(",").map(Number);
    pixels[(y - y0) * width + (x - x0)] = idx;
  }
  // darken the silhouette's rim a little, so the cape reads against the body
  const rim = pixels.slice();
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const i = y * width + x;
      if (pixels[i] === T) continue;
      const open = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => {
        const xx = x + dx, yy = y + dy;
        return xx < 0 || yy < 0 || xx >= width || yy >= height || pixels[yy * width + xx] === T;
      });
      if (open) rim[i] = (pixels[i] & 0xf0) | Math.min(14, (pixels[i] & 0x0f) + 2);
    }
  const hotspots = [{ number: marker, x: 0, y: 0 }];
  return { width, height, xOffset: x0, yOffset: y0, pixels: rim, hotspots };
}

// ---- writing ----

function toBgf(name: string, frames: Frame[], groups: number[][], shrink = SHRINK) {
  return {
    name: name.replace(/\.bgf$/, ""),
    version: 10,
    shrink,
    bitmaps: frames.map((f) => ({ width: f.width, height: f.height, xOffset: f.xOffset, yOffset: f.yOffset, hotspots: f.hotspots, pixels: f.pixels })),
    groups,
  };
}
async function write(name: string, b: any) {
  writeFileSync(join(OUT, name), await encodeBgf(b));
  return parseBgf(new Uint8Array(readFileSync(join(OUT, name))));
}

const written: Record<string, any> = {};

const capes: Record<string, Frame[]> = {};
for (const [gender, g] of Object.entries(GENDERS)) {
  const back = await unionMask(g.torsos, BACK_VIEW);
  // the body's depth standing: a swinging arm in another pose would count
  const side = await unionMask(g.torsos, 2, true);
  const rows = [5, 6, 7, 8];
  const halfW = Math.max(...rows.map((y) => { const e = back.rowExtent(y); return e ? (e[1] - e[0] + 1) / 2 : 0; }));
  const halfD = Math.max(...rows.map((y) => { const e = side.rowExtent(y); return e ? (e[1] - e[0] + 1) / 2 : 0; }));
  const feet = await feetBelowNeck(g);
  const shape: Shape = { shoulder: halfW, depth: halfD, length: Math.round(0.7 * feet) };
  const facing = await sideFacing(g.head);
  console.log(`${gender}: shoulders ${halfW * 2}, depth ${halfD * 2}, neck to soles ${feet}, cape ${shape.length}, side view faces ${facing > 0 ? "right" : "left"}`);
  const frames = [0, 1, 2, 3, 4, 5].map((v) => drawCape(shape, v, facing, g.sideLift));
  capes[gender] = frames;
  // the torso's groups, each angle slot showing the cape of its view
  const torso = await bgf(g.torsos[0]);
  const groups = torso.groups.map((grp: number[]) => grp.map((i: number) => i % 6));
  written[g.out] = await write(g.out, toBgf(g.out, frames, groups));
}

// ---- the preview ----

/** XlatTable GUILDCOLOR: red to ramp i, blue to ramp j (RAMPS order: red, skin1, skin2, skin4, orange, bgreen, dblue, purple, gold, gray, bblue) */
const guildXlat = (i: number, j: number) => 0x87 + i * 11 + j;
const COLOURS: [number, number][] = [[0, 8], [6, 8], [5, 9], [7, 0]]; // colour 1, colour 2
// a tunic in grey, on the second skin (XlatTable: the art's red ramp to grey, its dark blue to skin)
const { XLAT, encodeDye, dyeField, DYE_SHADE, DYE_BASE } = await mod("packages/render/src/xlat.ts");
const SKIN = XLAT.DBLUETOSKIN2;
const BODY = DYE_BASE + encodeDye(dyeField(0xd, DYE_SHADE.DARK), dyeField(0xd, DYE_SHADE.LIGHT), 3);
const tiles: { c: any; label: string }[] = [];
for (const [gender, g] of Object.entries(GENDERS)) {
  for (const [ti, [torsoName, legsName, leftName]] of g.preview.entries()) {
    const [c1, c2] = COLOURS[ti % COLOURS.length];
    const xlat = guildXlat(c1, c2);
    for (let slot = 0; slot < 8; slot++) {
      const c = compositeSprite(
        {
          base: { bgf: await bgf(torsoName), group: 0, translation: BODY, hotspot: 0 },
          overlays: [
            { bgf: await bgf(legsName), group: 0, translation: BODY, hotspot: HS_LEGS },
            { bgf: await bgf(g.head), group: 0, translation: SKIN, hotspot: HS_HEAD },
            { bgf: await bgf(g.right), group: 0, translation: BODY, hotspot: 21 },
            { bgf: await bgf(leftName), group: 0, translation: BODY, hotspot: 31 },
            { bgf: written[g.out], group: 0, translation: xlat, hotspot: HS_HEAD },
          ],
          viewAngle: slot * 512,
          faceApart: true,
        },
        xl,
      );
      tiles.push({ c, label: `${gender} ${torsoName} ${slot}` });
    }
  }
}
const SCALE = 3, PAD = 6, COLS = 8;
const cellW = Math.max(...tiles.map((t) => t.c.width / t.c.resolution)) * SCALE + PAD;
const cellH = Math.max(...tiles.map((t) => t.c.height / t.c.resolution)) * SCALE + PAD;
const rows = Math.ceil(tiles.length / COLS);
const W = Math.ceil(cellW * COLS + PAD), H = Math.ceil(cellH * rows + PAD);
const img = new Uint8Array(W * H * 4);
for (let i = 0; i < W * H; i++) img.set([96, 96, 104, 255], i * 4);
const plot = (x: number, y: number, idx: number, table: Uint8Array) => {
  if (x < 0 || y < 0 || x >= W || y >= H) return;
  const c = table[idx];
  img.set([rgba[c * 4], rgba[c * 4 + 1], rgba[c * 4 + 2], 255], (y * W + x) * 4);
};
const identity = new Uint8Array(256).map((_, i) => i);
tiles.forEach((t, n) => {
  const ox = PAD + (n % COLS) * cellW, oy = PAD + Math.floor(n / COLS) * cellH;
  const res = t.c.resolution;
  for (let y = 0; y < t.c.height; y++)
    for (let x = 0; x < t.c.width; x++) {
      const p = t.c.pixels[y * t.c.width + x];
      if (p === T) continue;
      for (let dy = 0; dy < SCALE; dy++) for (let dx = 0; dx < SCALE; dx++) plot(ox + Math.floor((x * SCALE) / res) + dx, oy + Math.floor((y * SCALE) / res) + dy, p, identity);
    }
});
writeFileSync(join(OUT, "preview.png"), png(W, H, img));
console.log(`wrote ${Object.keys(written).join(", ")} and preview.png to ${OUT}`);

if (opt.install) {
  mkdirSync(INSTALL, { recursive: true });
  for (const n of Object.keys(written)) copyFileSync(join(OUT, n), join(INSTALL, n));
  console.log(`installed to ${INSTALL}`);
}

function png(w: number, h: number, px: Uint8Array): Buffer {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) Buffer.from(px.buffer, px.byteOffset + y * w * 4, w * 4).copy(raw, y * (w * 4 + 1) + 1);
  const table = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (b: Buffer) => { let c = 0xffffffff; for (const v of b) c = table[(c ^ v) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (t: string, d: Buffer) => {
    const l = Buffer.alloc(4); l.writeUInt32BE(d.length);
    const td = Buffer.concat([Buffer.from(t), d]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td));
    return Buffer.concat([l, td, c]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
