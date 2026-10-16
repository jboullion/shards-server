// Runs the local server from run/server: start, stop, restart, status, logs, and one maintenance
// command. Plain Node, no dependencies (npm run <script> in this folder, see package.json).
//
//   node tools/server.mjs start | stop | restart | status | logs [lines] | maint "<command>"
//
// start   blakserv.exe in its own window, then waits until the game (5959) and maintenance (9998)
//         ports listen: a fresh game takes about 35 s. Already running: says so.
// stop    "terminate save" on the maintenance port: saves the game, then shuts down.
// status  whether the ports listen, and who's logged on.
// logs    the end of channel/error.txt and channel/debug.txt.
// maint   any maintenance command, e.g. node tools/server.mjs maint "show status".
//
// The ports come from run/server/blakserv.cfg. Don't send maintenance commands while blakserv is
// starting (status waits for the ports first): a command during startup can hang its main loop.

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { connect } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const RUN = join(ROOT, "run", "server");
const EXE = join(RUN, "blakserv.exe");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function ports() {
  let cfg = "";
  try {
    cfg = readFileSync(join(RUN, "blakserv.cfg"), "latin1");
  } catch {
    // The defaults
  }
  const port = (name, dflt) => Number(new RegExp(`^${name}\\s+(\\d+)`, "m").exec(cfg)?.[1] ?? dflt);
  return { game: port("Port", 5959), maint: port("MaintenancePort", 9998) };
}

function listening(port) {
  return new Promise((done) => {
    const s = connect({ host: "127.0.0.1", port });
    s.once("connect", () => {
      s.destroy();
      done(true);
    });
    s.once("error", () => done(false));
  });
}

/** A maintenance command's output: what arrives until it stops growing (blakserv/maintenance.c ends lines with CR) */
function maint(command) {
  return new Promise((done, fail) => {
    const s = connect({ host: "127.0.0.1", port: ports().maint });
    let out = "";
    let sent = false;
    let closed = false;
    s.on("data", (d) => (out += d.toString("latin1")));
    // Once the command is sent, the server going away is its answer ("terminate save")
    s.on("error", (e) => (sent ? (closed = true) : fail(new Error(`maintenance port: ${e.message}. Is blakserv running?`))));
    s.on("close", () => (closed = true));
    s.on("connect", async () => {
      await sleep(150);
      out = "";
      s.write(command + "\r");
      sent = true;
      let last = -1;
      for (let waited = 0; waited < 120_000 && !closed && (out.length === 0 || out.length !== last); waited += 200) {
        last = out.length;
        await sleep(200);
      }
      s.end();
      done(out.replace(/\r/g, ""));
    });
  });
}

async function start() {
  const p = ports();
  if (await listening(p.game)) return console.log(`blakserv is already running (port ${p.game})`);
  if (!existsSync(EXE)) {
    console.error(`no ${EXE}: build the server first (meridian-browser's server\\build.cmd)`);
    process.exit(1);
  }
  console.log("starting blakserv in its own window...");
  spawn(EXE, [], { cwd: RUN, detached: true, stdio: "ignore" }).unref();
  const began = Date.now();
  while (Date.now() - began < 120_000) {
    if ((await listening(p.game)) && (await listening(p.maint))) {
      console.log(`blakserv is up after ${Math.round((Date.now() - began) / 1000)} s: game on ${p.game}, maintenance on ${p.maint}`);
      return;
    }
    await sleep(1000);
  }
  console.error("blakserv didn't open its ports within 2 minutes; see npm run logs");
  process.exit(1);
}

async function stop() {
  const p = ports();
  if (!(await listening(p.maint))) return console.log("blakserv isn't running");
  console.log("saving and shutting down...");
  await maint("terminate save");
  const began = Date.now();
  while (Date.now() - began < 60_000) {
    if (!(await listening(p.game))) return console.log("blakserv has stopped");
    await sleep(500);
  }
  console.error("blakserv is still running a minute later; close its window if it hangs");
  process.exit(1);
}

async function status() {
  const p = ports();
  const game = await listening(p.game);
  const m = await listening(p.maint);
  console.log(`game port ${p.game}: ${game ? "listening" : "closed"}; maintenance port ${p.maint}: ${m ? "listening" : "closed"}`);
  if (game && m) console.log((await maint("who")).trim());
}

function tail(file, lines) {
  const path = join(RUN, "channel", file);
  if (!existsSync(path)) return console.log(`== ${file}: none yet`);
  const all = readFileSync(path, "latin1").split(/\r?\n/).filter(Boolean);
  console.log(`== ${file} (last ${Math.min(lines, all.length)} of ${all.length})`);
  console.log(all.slice(-lines).join("\n"));
}

const [cmd, ...args] = process.argv.slice(2);
switch (cmd) {
  case "start":
    await start();
    break;
  case "stop":
    await stop();
    break;
  case "restart":
    await stop();
    await start();
    break;
  case "status":
    await status();
    break;
  case "logs": {
    const n = Number(args[0]) || 20;
    tail("error.txt", n);
    tail("debug.txt", n);
    break;
  }
  case "maint":
    if (!args.length) {
      console.error('usage: node tools/server.mjs maint "<command>"');
      process.exit(2);
    }
    console.log((await maint(args.join(" "))).trim());
    break;
  default:
    console.error("usage: node tools/server.mjs start | stop | restart | status | logs [lines] | maint \"<command>\"");
    process.exit(2);
}
