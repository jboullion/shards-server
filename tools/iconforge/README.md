# iconforge

Restyles the high-resolution fan-art item icons (photographic food, 3D-rendered helmets, …) into
low-resolution sprites that match the original 1996 Meridian 59 item art, in batches you review.

```
inventory ─► select ─► batch (model + pixelize + BGF) ─► review.html ─► apply ─► install
                          ▲                                               │
                          └──────────── rejected icons (with your notes) ─┘
```

Needs Python 3.12+ with Pillow and numpy. Everything else is the standard library.

## Steps

1. **Inventory**: `python tools/iconforge/forge.py inventory`
   - Reads every item class for the `.bgf` it shows as an icon, finds the file the game serves
     (installed 104 client first, then `resource/`), and compares it with the classic Steam client.
   - Classifies each icon:
     - `fanart`: not classic, and high-res (shrink ≥ 15 or a side ≥ 300 px)
     - `check`: not classic, but low-res
     - `original`: identical to the classic client
   - Writes `data/iconforge/inventory.json` and contact sheets in `build/iconforge/inventory/`.
2. **Select**: `forge.py select <names…>` adds icons to the work set; add `--off` to remove them, or
   `--status check` to add a whole class. Fan art starts selected.
3. **Batch**: `forge.py batch --provider openai --size 20 --variants 2`
   - Takes the next 20 selected icons that are pending or rejected (rejected ones first).
   - Sends each distinct frame to the model, on a flat magenta background, with the classic style
     references and the prompt from `style/style.md`.
   - Post-processes each result (`pixelize.py`):
     - matte the background
     - fit the item to its original in-game size at `--shrink 5`
     - area-downscale
     - map to `blakston.pal` with no dithering
   - Writes candidate BGFs that keep the original's groups, offsets and hotspots.
   - Output: `build/iconforge/batches/NNN/` with `review.html`, `sheet.png` and `manifest.json`.
4. **Review**: open `review.html`.
   - Click a variant to keep it, or press Reject and type what was wrong ("too dark", "keep the
     red bow").
   - Choices persist in the browser.
   - **Save decisions.json** downloads `decisions-NNN.json`.
5. **Apply**: `forge.py apply <path to decisions-NNN.json>`.
   - Keeps go to `build/iconforge/kept/`.
   - Rejects go back in the queue with your note added to their next prompt.
   - Anything undecided goes back as pending.
6. Repeat 3–5 until `forge.py status` shows nothing pending.
7. **Install**: `forge.py install` copies kept icons to `resource/graphics/shards/` under their
   original names. Commit them.
   - Windows client: `resource/graphics/makefile` builds `shards` last, so its copies replace the
     contributors' versions in `run/localclient/resource`.
   - Browser client: `meridian-browser/tools/assets/build-assets.ts` reads
     `server/src/resource/graphics/shards` (or `ICONFORGE_DIR`) before every other source. Then
     run `npm run assets`.

### Per-icon settings (`data/iconforge/overrides.json`)

- `describe`: what the model draws instead of the class name.
- `mode: "generate"`: draws a new icon from the description alone.
- `output`: installs under another file name.
- `from`: starts a new variant sprite from another sprite's frames (our restyle of it if there is one).
  - Point the class's Kod at the new file first; `forge.py inventory` then lists it with that source.
  - Example: `minotaurbers.bgf`, the minotaur berserker drawn from the restyled minotaur.
  - Monsters work best with `--sheet-frames 8`: with three angles to a sheet, the model can drift off the grid.

## Providers

| spec | what | keys / setup |
|---|---|---|
| `echo` | no model: the original goes straight to pixelize. Free; use it to try the loop. | none |
| `comfy[@checkpoint]` | local SDXL img2img in ComfyUI at 127.0.0.1:8188. Options: `--opt denoise=0.55 --opt lora=<file> --opt lora_strength=0.9 --opt steps=28 --opt cfg=6`. No style references (img2img sees only the icon). | ComfyUI running; a pixel-art LoRA helps a lot |
| `qwen[@model]` | Qwen-Image-Edit (2511, 8-bit) in a ComfyUI with a big GPU, e.g. a RunPod 5090 pod. Gets the icon plus the style references as one sheet. Options: `--opt steps=30 --opt cfg=4 --opt lora=qwen-pixel-art.safetensors`. | `QWEN_COMFY_URL` (in `.env` or the environment), e.g. `https://<pod-id>-8188.proxy.runpod.net`; the pod needs the model, its text encoder and VAE in `models/` |
| `openai[@model]` | GPT Image edits with the style references. | `OPENAI_API_KEY` in the repo-root `.env` |
| `fal[@model]` | any fal.ai edit model (Flux Kontext, Qwen-Image-Edit). | `FAL_AI_API_KEY` in `.env` |

Several at once (`--provider comfy,openai`) puts every provider's candidates side by side on the
same review page, which is how to run a bake-off:

```
forge.py batch --names carrot dhelma cookie key1 gift1 onion rawsteak squidc pecanslice wolfmask --provider comfy,openai,fal --variants 2
```

Model results are cached in `build/iconforge/cache/`, keyed on input, prompt, model, options and
variant number. Re-running a batch, or re-pixelizing with another `--shrink` or `--outline`, never
pays twice. Changing `style/style.md` or a reject note does make a new request.

## Hairstyles and facial hair (`hair.py`)

New player hair and beards, drawn on the game's own heads and kept recolourable:

```
python tools/iconforge/hair.py hair  ptsb --ref ptad --describe "a short, tousled crop"
python tools/iconforge/hair.py beard pbdc --describe "a pointed goatee with a thin moustache"
```

- `headrender.ts` renders the head and face with meridian-browser's own compositor, so placement matches the game.
- One request redraws a sheet of every view in neutral grey. The part is matted (grey, not green, not the skin under it, clear of the eyes and nose), snapped to the hair ramp (`D0`–`DF`) plus black, and written as a shrink-14 BGF placed from the head's hotspot: 13 for hair (6 views), 12 for facial hair (5 views, no back).
- Facial hair frames also carry a hotspot numbered 127. meridian-browser (`sprites.ts` `HS_OVER_FIGURE`) then draws the beard over the body, under a helm. Without it, the collar covers the beard, because the head hangs under the body. Keep it when editing a beard by hand.
- Outputs go to `build/iconforge/hair/<name>/` (`raw.png` is cached, `preview.png` shows the part on both heads in four hair colours). Install by copying the BGF to `resource/graphics/shards/` and listing it in `kod/util/system.kod` (`GetAllowedHairIcons`, `GetAllowedBeardIcons`).
- `data/iconforge/hair.json` lists every style made so far, with its reference and description.

## Weapons in every view (`weapon.py`)

A weapon has up to three sprites: its icon, the overlay in the player's hand (24 frames) and the first-person view (5 frames). `weapon.py` gets one large upright drawing from the model and poses it into every frame of all three, so they show the same object:

```
python tools/iconforge/weapon.py masterwand --install
```

- The weapons and their sprites are listed in `data/iconforge/weapons.json` (first the apprentice and master wands).
- Each frame of the original sprite gives the pose: its axis, its two ends, the head (the wider end) and the head's size. The drawing is scaled, rotated and placed on those, 4x finer, then area-averaged down.
- The head's neutral greys go to the grey ramp (`D0`–`DF`), which the wands' school xlat recolours on all three sprites. Nothing else may use that ramp.
- In-hand overlays can change shrink (`wandov.bgf` went from 30 to 6): the offsets are in the player body's pixels, so they stay put.
- Outputs go to `build/iconforge/weapon/<name>/`. `preview.png` shows the old frames, the new ones, and the new ones in each school's colour. `raw.png` is cached; `--force` asks the model again.
- `--install` also replaces the copies `forge.py install` would otherwise copy back over these.

## Fixing sprites by hand

meridian-browser's admin dashboard (`npm run dev`, then `localhost:5173/admin.html`) has a **Sprites** tab: a pixel editor for any `.bgf`, with hair and facial hair previewed on both heads in every hair colour. It saves to `resource/graphics/shards/`, keeping the copy it replaces in `build/spriteforge/backups/`. It can also import a PNG and list a new style in `system.kod`.

Hand fixes live in the same folder iconforge installs to. Re-running `forge.py install` or `hair.py` for the same name overwrites them, so redo the fix (or skip that name) afterwards.

## Files

- `forge.py`: the CLI.
- `inventory.py`: finds icons and classifies them.
- `providers.py`: the model calls.
- `pixelize.py`: matte, fit, downscale and palette mapping.
- `hair.py`, `headrender.ts`: player hairstyles and facial hair.
- `weapon.py`, `xlats.ts`: one drawing posed into a weapon's icon, in-hand and first-person sprites (`xlats.ts` gives the previews the client's palette translations).
- `bgf.py`: BGF read/write (round-trip tested on the 104 client's files).
- `review.py`: contact sheets and the review page.
- `style/style.md`: the style prompt and the list of reference icons.
- `data/iconforge/`: `inventory.json` and `state.json`, committed.
- `build/iconforge/`: everything generated, git-ignored.
