// Head renders for new hair and facial hair (hair.py), drawn by meridian-browser's own compositor
// (packages/render/src/sprites.ts) so placement matches the game exactly.
//
//   node headrender.ts <request.json> <out.json>
//
// request: { browser, assets, override?, gender, slots, skin, parts: [{ bgf, hotspot, translation }],
//            masks?: [hotspot,...] }
// For each slot: the composite (head + parts) as RGBA, its resolution (image px per head px), where
// the head's top-left sits, the head's hotspots in image px, and an alpha mask of the overlays on
// the hotspots listed in `masks` (the face features hair must not cover).
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , reqPath, outPath] = process.argv;
const req = JSON.parse(readFileSync(reqPath, "utf8"));
const mod = (p: string) => import(pathToFileURL(`${req.browser}/${p}`).href);
const { parseBgf, parsePaletteBin } = await mod("packages/formats/src/index.ts");
const { XlatTable } = await mod("packages/render/src/xlat.ts");
const { compositeSprite, frameFor } = await mod("packages/render/src/sprites.ts");
const { compositeToRgba } = await mod("packages/render/src/colorTexture.ts");

const file = (name: string) => {
  for (const dir of [req.override, req.assets].filter(Boolean)) {
    const p = `${dir}/${name}`;
    if (existsSync(p)) return p;
  }
  throw new Error(`no ${name}`);
};
const pal = parsePaletteBin(new Uint8Array(readFileSync(file("palette.bin"))));
const xl = new XlatTable(pal.rgb, existsSync(`${req.assets}/lightpal.bin`) ? new Uint8Array(readFileSync(`${req.assets}/lightpal.bin`)) : null);
const cache: Record<string, unknown> = {};
const bgf = async (name: string) => (cache[name] ??= await parseBgf(new Uint8Array(readFileSync(file(name)))));

const HEADS: Record<string, { head: string; eyes: string; nose: string; mouth: string }> = {
  male: { head: "phax.bgf", eyes: "peax.bgf", nose: "pnax.bgf", mouth: "pmax.bgf" },
  female: { head: "phkx.bgf", eyes: "pekx.bgf", nose: "pnkx.bgf", mouth: "pmkx.bgf" },
};
const h = HEADS[req.gender];
const face = [
  { bgf: h.mouth, hotspot: 12, translation: req.skin },
  { bgf: h.eyes, hotspot: 11, translation: req.skin },
  { bgf: h.nose, hotspot: 14, translation: req.skin },
];
const out = [];
for (const slot of req.slots as number[]) {
  const angle = slot * 512;
  const head = await bgf(h.head);
  const make = async (parts: { bgf: string; hotspot: number; translation: number }[], drawBase = true) => {
    const overlays = [];
    for (const p of parts) overlays.push({ bgf: await bgf(p.bgf), group: 0, translation: p.translation, hotspot: p.hotspot });
    return compositeSprite({ base: { bgf: head, group: 0, translation: req.skin, hotspot: 0 }, overlays, viewAngle: angle, drawBase }, xl);
  };
  const all = [...face, ...(req.parts ?? [])];
  const c = await make(all);
  // the face features alone, placed on the same canvas (same bounds: the parts are a subset)
  const masked = all.filter((p) => (req.masks ?? []).includes(p.hotspot));
  const m = masked.length ? await make(masked, false) : null;
  const bmp = frameFor(head, angle, 0);
  const hotspots: Record<number, [number, number]> = {};
  for (const hs of bmp.hotspots) hotspots[Math.abs(hs.number)] = [c.originX + hs.x * c.resolution, c.originY + hs.y * c.resolution];
  let mask = null;
  if (m) {
    // the mask image's own origin may differ (no base): shift it onto the composite
    const dx = c.originX - m.originX * (c.resolution / m.resolution);
    const dy = c.originY - m.originY * (c.resolution / m.resolution);
    const k = c.resolution / m.resolution;
    const a = new Uint8Array(c.width * c.height);
    for (let y = 0; y < c.height; y++)
      for (let x = 0; x < c.width; x++) {
        const sx = Math.floor((x - dx) / k), sy = Math.floor((y - dy) / k);
        if (sx >= 0 && sy >= 0 && sx < m.width && sy < m.height && m.pixels[sy * m.width + sx] !== 254) a[y * c.width + x] = 255;
      }
    mask = Buffer.from(a).toString("base64");
  }
  out.push({
    slot, w: c.width, h: c.height, res: c.resolution, ox: c.originX, oy: c.originY, hotspots,
    rgba: Buffer.from(compositeToRgba(c, pal.rgb, 3)).toString("base64"), mask,
    headW: bmp.width, headH: bmp.height,
  });
}
writeFileSync(outPath, JSON.stringify(out));
console.log(`${out.length} views`);
