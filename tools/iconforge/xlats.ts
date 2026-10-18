// The palette and some palette translations (xlats) as JSON, from meridian-browser's own tables
// (packages/render/src/xlat.ts), so Python previews tint sprites exactly as the client does.
//   node xlats.ts <meridian-browser dir> <out.json> <xlat id>...
// Output: {"rgb": [768 bytes], "<id>": [256 indices], ...}

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , browser, outPath, ...ids] = process.argv;
const mod = (p: string) => import(pathToFileURL(`${browser}/${p}`).href);
const { parsePaletteBin } = await mod("packages/formats/src/index.ts");
const { XlatTable } = await mod("packages/render/src/xlat.ts");

const assets = `${browser}/dist/assets`;
const pal = parsePaletteBin(new Uint8Array(readFileSync(`${assets}/palette.bin`)));
const light = existsSync(`${assets}/lightpal.bin`) ? new Uint8Array(readFileSync(`${assets}/lightpal.bin`)) : null;
const xl = new XlatTable(pal.rgb, light);
const out: Record<string, number[]> = { rgb: Array.from(pal.rgb as Uint8Array) };
for (const id of ids) out[String(Number(id))] = Array.from(xl.get(Number(id)) as Uint8Array);
writeFileSync(outPath, JSON.stringify(out));
