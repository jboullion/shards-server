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

### Hats (`hair.py hat`)

```
python tools/iconforge/hair.py hat wizhatb --white-details --describe "a whimsical wizard's hat with a long crooked tip ..."
```

- Hats hang where hair does (hotspot 13), so the same steps make them. The head is drawn bald, as a worn helmet leaves it, with room above for a tall hat.
- The hat's item frames come from its worn views: the inventory picture (the three-quarter view), on the ground (the front view) and broken (the front view crushed flat). The groups are `[[views], [6], [7], [8]]`, like the classic hats.
- The hat is in the grey ramp, so the item's palette translation colours it: the wizard's hat takes its school's colour (`wizardhat.kod` `ResetColors`). `preview.png` shows the six school colours.
- `--white-details` keeps small bright shapes white (embroidered stars, a moon, buttons): palette 255 and 7, which no translation changes. The shapes are told from highlights by being compact and off the hat's rim; `HAIR_DEBUG=1` prints what each one measures. Check the result and fix a stray speck in the sprite editor.
- The wizard's hat (`wizardhat.bgf`, and `wizardhatf.bgf`, the same file for women) came from `wizhatb`. Its copy in `build/iconforge/kept/` is the new hat, so `forge.py install` keeps it.

## Weapons in every view (`weapon.py`)

A weapon has up to three sprites: its icon, the overlay in the player's hand (24 frames) and the first-person view (5 frames). `weapon.py` gets one large upright drawing from the model and poses it into every frame of all three, so they show the same object:

```
python tools/iconforge/weapon.py masterwand --install
```

- The weapons and their sprites are listed in `data/iconforge/weapons.json` (first the apprentice and master wands).
- Each frame of the original sprite gives the pose: its axis, its two ends, the head (the wider end) and the head's size. The drawing is scaled, rotated and placed on those, 4x finer, then area-averaged down.
- The head's neutral greys go to the grey ramp (`D0`–`DF`), which the wands' school xlat recolours on all three sprites. Nothing else may use that ramp.
- A weapon in fixed colours sets `tint_rows` to 0 and gives `upright`, how the model should stand it, with `flip` when that puts its head (the wider end, which the frames are measured by) at the bottom. The sword of light (`lightsword`) is drawn tip up and turned over, so its hilt is the head. Its in-hand sprite sets `head_max` 1, so the hilt isn't drawn bigger than the blade's scale. Its first-person sprite sets `sink`, so the grip runs off the bottom of the screen.
- In-hand overlays can change shrink (`wandov.bgf` went from 30 to 6): the offsets are in the player body's pixels, so they stay put.
- Outputs go to `build/iconforge/weapon/<name>/`. `preview.png` shows the old frames, the new ones, and the new ones in each school's colour. `raw.png` is cached; `--force` asks the model again.
- `--install` also replaces the copies `forge.py install` would otherwise copy back over these.

## The red nose (`rednose.py`)

```
python tools/iconforge/rednose.py --install
```

The red nose mask's 104 fan art (`mmrednose.bgf`, `mfrednose.bgf`) was a ball 200 px across at shrink 1, as big worn as on the ground. The script draws the ball itself, so no model is needed: shaded in the grey ramp, which the mask's `PT_GRAY_TO_LRED` turns red, with a white glint.

- The worn views hang from `HS_TOUPEE`, like the carrot nose's. Each view sits on the tip of the face's nose: the head's hotspot 14 plus that gender's nose bitmaps, averaged. The back views are blank, as the face's are.
- It's at shrink 14, the face parts' own. The ground frame is 32 fine units across, about the carrot nose's size; the inventory shows it too.
- Outputs go to `build/iconforge/rednose/`, with `preview.png` (both heads, every view). `--install` also replaces the kept icon, which `forge.py install` would otherwise copy back.

## Guild cloaks (`cloak.ts`)

```
node tools/iconforge/cloak.ts [--browser <meridian-browser>] [--install]
```

The guild cloak (meridian-browser `docs/adr/0022-guilds.md` decision 22) is drawn by script, not by a model. A cape is a sheet hanging from the shoulders and wrapped half round the back, sized from every torso of a gender aligned on the neck, and seen from each of a torso's six views, so the views agree by construction.

- It writes `cloakm.bgf` and `cloakf.bgf` (the capes, with the torso's groups, hanging from `HS_HEAD`), `cloakic.bgf` (the icon) and `cloakrk.bgf` (the hall's rack) to `build/iconforge/cloak/`, with `preview.png`: both genders in a shirt, a robe and plate, from every view, in four guild colour pairs, drawn by meridian-browser's own compositor.
- The cloth is the red ramp and the trim the dark-blue ramp, the two a guild's colour translation changes.
- Each frame carries meridian-browser's depth marker: `HS_OVER_BODY` (125) from behind, `HS_BEHIND_FIGURE` (126) otherwise.
- `--install` copies the BGFs to `resource/graphics/shards/`. Re-running overwrites hand fixes made in the sprite editor.

## Fixing sprites by hand

meridian-browser's admin dashboard (`npm run dev`, then `localhost:5173/admin.html`) has a **Sprites** tab: a pixel editor for any `.bgf`, with hair and facial hair previewed on both heads in every hair colour. It saves to `resource/graphics/shards/`, keeping the copy it replaces in `build/spriteforge/backups/`. It can also import a PNG and list a new style in `system.kod`.

Hand fixes live in the same folder iconforge installs to. Re-running `forge.py install` or `hair.py` for the same name overwrites them, so redo the fix (or skip that name) afterwards.

## Files

- `forge.py`: the CLI.
- `inventory.py`: finds icons and classifies them.
- `providers.py`: the model calls.
- `pixelize.py`: matte, fit, downscale and palette mapping.
- `hair.py`, `headrender.ts`: player hairstyles and facial hair.
- `cloak.ts`: the guild cloak, its icon and its rack.
- `weapon.py`, `xlats.ts`: one drawing posed into a weapon's icon, in-hand and first-person sprites (`xlats.ts` gives the previews the client's palette translations).
- `bgf.py`: BGF read/write (round-trip tested on the 104 client's files).
- `review.py`: contact sheets and the review page.
- `style/style.md`: the style prompt and the list of reference icons.
- `data/iconforge/`: `inventory.json` and `state.json`, committed.
- `build/iconforge/`: everything generated, git-ignored.
