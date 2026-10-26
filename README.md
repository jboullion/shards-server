# Meridian Shards: server

The game server, game code and tools for **Meridian Shards**, a fork of the Meridian 59
"Server 104" code base (Blakod, `blakserv`, the classic client) that is being reworked into
"the version of the game I have always wanted". It is played in a browser at
<https://m59-shards.com/play/>.

This repo is the **server half**. The browser client, the desktop and Android shells, the
deploy kit (Docker, gateway, website) and all the design records (ADRs) live in the companion
repo **meridian-browser** (`E:\2026_Experiments\meridian-browser` on the dev machine). That
repo reaches this one through a directory junction, `server\src` (made by `server\link-src.cmd`),
so its build, asset build and deploy read this working tree directly.

Developer and agent notes are in [AGENTS.md](AGENTS.md).

## What is different from Server 104

Decisions and designs are recorded as ADRs in meridian-browser's `docs/adr/` (0001 to 0026).
In short, as of release 0.13.0 (2026-10-24) and after:

- **No random PvP.** Players fight each other only in the faction war, guild wars, the arena
  (and its bouts) and Frenzies. Name colours mark enemies per viewer. (ADR 0021)
- **Factions and the faction war:** renown, campaigns and sieges between the lieges, NPC
  battles, Champion's Gifts, Roq's contracts; companions come only from faction leaders. (ADR 0021)
- **Guild revamp:** public halls, ranked chests, prestige, a recruitment board, eight Fallen
  Halls, daily guild quests, trophies and the Inner Sanctum, cloaks. (ADR 0022)
- **Spell revamp:** level grids per school, the Jala songs (taught by Priestess Themala),
  monster-targetable spells, spell effects and cast sounds. (ADR 0023)
- **Magic items:** four tiers, no curses, an elder reveal, smith repair, random jewellery magic.
- **Quests:** data-driven (`quests/*.json`, see below), with Marion's quest flow and
  companion quests. (ADR 0009)
- **Achievements** with titles (ADR 0025), **chat emoji and item links** (ADR 0024),
  **cosmetics** (barber, tailor, dyes; ADR 0012), pets, followers and companions, an arena.
- **The Office of the Justicar:** the Barloque Assizes (an NPC court always in session),
  Caramo's daily bounties, and name changes from Caramo. (ADR 0026)
- Skills improve at half speed. Shopkeepers have a Sell window.
- **Removed:** the crafting school (school slot 7 stays reserved so client wire formats do not
  change), curses, and the old random-PvP and karma systems. Parts of `design/` and `doc/`
  still describe them: they are the upstream documents and have not been rewritten.

## Layout

| Path | What it is |
|---|---|
| `blakserv/` | The server (C). Ports and settings come from `run/server/blakserv.cfg`. |
| `kod/` | The game code in Blakod (`.kod`), compiled to `.bof` by `blakcomp`. `kod/include` has `blakston.khd` and `protocol.khd`; `kod/util/questdefs.kod` is generated. |
| `blakcomp/`, `blakdiff/`, `blakdeco/`, `bbgun/`, `makebgf/`, `gelmaker/`, `keybind/`, `roomedit/` | The Blakod compiler and the other upstream tools. |
| `clientd3d/` | The classic Windows client (kept building; the browser client is the one in play). |
| `quests/` | Quest definitions, one JSON file per quest, plus `givers.json` and `retired.json`. |
| `run/server/` | The live server's working folder (built binary, `blakserv.cfg`, `loadkod`, `rsc`, `savegame`, `channel/*.txt` logs). `run/testserver` is the throwaway sandbox for the testbench; `run/localclient` is the classic client. |
| `claude-testbench/` | `kodbench`, a runtime harness for checking Kod changes, and the `blakserv-dm` MCP server. See its `README.md`, `CLAUDE.md` and `RECIPES.md`. |
| `tools/server.mjs` | Start, stop and inspect the local server (below). `tools/iconforge/` is the item-icon restyle pipeline (its own README). |
| `www/` | The PHP statistics and guild website from upstream. |
| `data/` | Inventories used by the icon tool. |
| `doc/`, `design/`, `manual/`, `README.old`, `CONTRIBUTING.md` | Upstream Meridian 59 documentation (Blakod syntax, protocol, design pages). Useful for the language and protocol; the game design pages are out of date. |
| `lib*/`, `libarchive/` | zlib, libarchive, libpng and jansson, built from source. |

## Build (Windows)

Needs Visual Studio with the C++ workload. The current recipe uses VS 18 and builds 32-bit;
run it from the repo root:

```bat
call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x86 -host_arch=x64
set _CL_=/WX-
nmake -nologo debug=1 Bzlib Bjansson Bserver Bkod
```

- `_CL_=/WX-` switches off warnings-as-errors, which newer MSVC trips.
- A fresh clone has no `run/server`; the build creates it.
- The build exits 0 even when a `.kod` file fails to compile. Search its log for `.kod(` errors.
- meridian-browser's `server\build.cmd` does the same and also runs the quest generator first;
  prefer it when you will test in the browser client. The older `vsvars32.bat` (VS 2015)
  instructions in `claude-testbench/CLAUDE.md` do not work on a machine without VS 2015.
- The Linux build (`makefile.linux`, `common.mak.linux`, `rules.mak.linux`) is what the Docker
  image on the live server uses. The Dockerfile is in meridian-browser's `deploy/blakserv/`.

## Run (local)

No `npm install` is needed:

```bat
npm start          :: start blakserv (about 35 s for a fresh game), waits for the ports
npm stop           :: "terminate save": saves, then shuts down
npm restart
npm run status     :: ports listening, who is logged on
npm run logs       :: the end of channel/error.txt and debug.txt
npm run maint -- "show status"   :: any maintenance command
```

- Game port **5959**, maintenance port **9998** (both from `blakserv.cfg`). The testbench
  sandbox uses 9999.
- After recompiling Kod, `reload system` (maintenance command, or the toolbar button) loads the
  new bytecode. New Kod strings reach the browser client only after its asset build copies
  `run/server/rsc/rsc0000.rsb` (`node tools/assets/build-assets.ts` in meridian-browser).
- Quests: edit `quests/*.json`, then run `npm run quests` in meridian-browser; it checks them
  against the Kod and writes `kod/util/questdefs.kod`.
- Blakod logs runtime errors and keeps running, so a clean compile proves little. Read
  `run/server/channel/error.txt`, or use the testbench ([claude-testbench/CLAUDE.md](claude-testbench/CLAUDE.md)).

## Release and deploy

Releases (`npm run release:patch` / `release:minor`, `npm run ship`) and the Docker deploy to
the live VM are driven from meridian-browser; see its `deploy/README.md`. Removing Kod classes
can stop an existing save from loading: that file also has the fresh-world procedure.

## Licence

Distributed under the licence in the [LICENSE](LICENSE) file. It does not cover the game
content (artwork, audio), which is not included. "Meridian" is a registered trademark and may
not be used without the owners' written permission. If you redistribute this code you must make
the source, including your changes, available.

## Origins

Meridian 59 is by Andrew Kirmse and Chris Kirmse (copyright 1994-2012). This fork builds on the
open-source "Server 104" code base (<https://meridian59.us/>); thanks to everyone who kept it
alive. Upstream's original README is kept as `README.old`. Its login-key instructions
(`clientd3d/login.c-example`, `SecretKey` in `blakserv.cfg`) still apply to the classic client.
