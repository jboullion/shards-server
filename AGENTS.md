# AGENTS.md: shards-server

Read [README.md](README.md) first for what this repo is and how to build and run it. This file
is for agents and developers changing it.

## What this is

The server half of Meridian Shards: `blakserv` (C), the game code in Blakod (`kod/`), quests
(`quests/`), and tools. The browser client and all the design records live in the companion repo
**meridian-browser** (`E:\2026_Experiments\meridian-browser`), whose `AGENTS.md` is the main
developer guide. That repo's `server\src` is a junction to this working tree.

- **ADRs are over there**, in `docs/adr/` (0001 to 0026). Kod comments here cite them
  (`ADR 0009` quests, `0011` fast travel, `0021` factions, `0022` guilds, `0023` spells,
  `0025` achievements, `0026` the Justicar). Read the ADR before reworking a system.
- The user makes the commits. Do not commit or push unless asked.
- The wire protocol is shared with the client: `kod/include/protocol.khd` here, and
  `packages/protocol` there (documented in meridian-browser's `docs/research/protocol.md`).
  A server change that alters a message needs the client changed in step.

## Direction

- This is a fork being made into the user's ideal Meridian 59. Prefer outright deletion of
  removed content over migration code. A fresh world is acceptable when content is removed.
- Removed on purpose: the crafting school (slot 7 stays reserved), curses, random PvP. Do not
  reintroduce references to them.
- Players fight players only in the faction war, guild wars, arenas and Frenzies.

## Build and run

See README. Short version: VS 18 `VsDevCmd` (x86), `set _CL_=/WX-`,
`nmake -nologo debug=1 Bzlib Bjansson Bserver Bkod` from the repo root, or meridian-browser's
`server\build.cmd` (which also generates the quest Kod). `npm start|stop|restart|status|logs|maint`
run the local server (`tools/server.mjs`).

Traps that cost time:

- **The build exits 0 when a `.kod` fails to compile.** Grep the log for `.kod(` errors.
- **Changing a constant in a `.khd` does not recompile the `.kod` files that use it.** Touch
  every user of the constant and rebuild, or their `.bof` keeps the old value.
- **A class name cannot be a parameter or local name** (`chest`, `guild`): use `oChest`.
- **A failed `bc` run can corrupt `kod/kodbase.txt`** (untracked; the build copies it into
  `run/server` even on failure). Back it up before risky builds. Symptom: `bc` segfaults on
  every file.
- **A failed build may not copy new `.bof` files into `run/server/loadkod`.** Touch the source
  and rebuild.
- **Deleting a class's `.kod` does not remove its `.bof` from `run/server/memmap`** (nor
  `rsc/<name>.rsc`). Delete them by hand. A forgotten `.rsc` keeps its old resource numbers,
  so once `kod/kodbase.txt` is renumbered it overwrites current strings in `rsc0000.rsb`
  (guild rank names showed sound files and spell text). Any `run/server/rsc/*.rsc` with no
  `.rsc` under `kod/` is stale.
- **Deleting a Kod class that a saved world still holds an object of makes blakserv stop
  loading at that object**, losing every later object, characters included. It then falls back
  to a fresh world with the old accounts. Either keep a stub class while the objects are
  deleted, or make a fresh world on purpose. The procedure for the live server is in
  meridian-browser's `deploy/README.md`.
- **`reload system` compacts object IDs.** Look objects up again afterwards; they also change
  at every save. Stale IDs have sent test items to the wrong object.
- **`GetTime()` is not Unix time.** Base time arithmetic on a value the game set.
- **New spells on a running world** need `send object 0 CreateAllSpellsIfNew`
  (`reload system` does not make them).
- **Maintenance port:** commands end in CR, not LF. Strings are passed as `quote`, which must be
  the last parameter. Replies are best read from `run/server/channel/admin.txt`.
- **Resources:** new Kod strings show blank in the browser client until meridian-browser's asset
  build (`node tools/assets/build-assets.ts`) copies `run/server/rsc/rsc0000.rsb`.
- **Files are CRLF** (the repo has `core.autocrlf` on Windows). Edits made with some tools lose
  trailing whitespace; match source lines whitespace-tolerantly.
- `kod/util/questdefs.kod` is **generated** by meridian-browser's `npm run quests` from
  `quests/*.json`. Never edit it by hand.

## Verifying Kod changes

A clean compile only proves the Kod parses. Blakod logs runtime errors and keeps running, so
read `run/server/channel/error.txt` after exercising the change. The harness is
`claude-testbench/`:

- [CLAUDE.md](claude-testbench/CLAUDE.md): the operating guide (sandbox on port 9999, never
  `save game`, hard kill on exit). Its VS 2015 `vsvars32.bat` compile line fails on machines
  without VS 2015: use the build above.
- [RECIPES.md](claude-testbench/RECIPES.md): spawn, kill and give items on the live server.
- `.mcp.json` loads the **blakserv-dm** MCP server (`claude-testbench/mcp_blakserv.py`) with
  first-class tools: `spawn`, `kill`, `give`, `cast`, `whisper`, `say_to_room`, `look`,
  `whereis`, `raw`. Prefer these to one-off scripts.

Headless logic only: it cannot judge rendering, feel or balance. For the whole game through the
browser client there is an AI playtester in meridian-browser (`npm run agent`, ADR 0020).

## Layout

See the table in README. Places agents most often work:

- `kod/` by subject: `kod/object/...` (items, monsters, passive/active, user), `kod/util/`
  (systems: `questbook`, `courtassizes`, `achievements`, `factionwar`, `fallenhalls`, ...),
  `kod/include/` (`blakston.khd`, `protocol.khd`).
- `quests/*.json`: one quest per file; `givers.json` places givers, `retired.json` lists
  retired ids. Court bounties are `court-bounty-*.json`.
- `blakserv/`: the C server. Changes here need a full rebuild and are rare: the load fallback
  (`loadall.c`), garbage collection (`garbage.c`), the protocol handlers.

## Documentation map

| Where | What |
|---|---|
| `README.md` | Overview, layout, build, run |
| meridian-browser `AGENTS.md`, `docs/adr/`, `docs/roadmap.md` | Developer guide, decisions, plan and progress log |
| meridian-browser `deploy/README.md` | Release, Docker, live-VM run book, fresh-world procedure |
| `claude-testbench/*.md` | Runtime test harness and live-server recipes |
| `tools/iconforge/README.md` | Item icon restyle pipeline |
| `roomedit/roogen/README.md` | Room generator |
| `doc/`, `design/`, `manual/`, `README.old` | Upstream Meridian 59 docs: Blakod language, protocol, old design. Language and protocol references still hold; design pages describing karma, factions' old bonuses or random PvP do not. |
