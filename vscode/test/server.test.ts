// `ger serve` killed under the views is started again at once, and that is one line in the log
// (13-28 #18 and S1 of 20-states-after: three lines, and the same warnings once more).

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

import { GerProcess } from "../src/server";

/** A stand-in for `ger serve`: answers /health, says where it listens, warns once on stderr. */
const SERVE = `
const http = require("node:http");
const server = http.createServer((_req, res) => { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify({ name: "ger", version: "0.1.0", api: "1.10.0" })); });
server.listen(0, "127.0.0.1", () => {
  process.stderr.write("warning: ger.token is stored in plain text\\n");
  process.stdout.write(JSON.stringify({ url: "http://127.0.0.1:" + server.address().port + "/api/v", api: "1", token: "t", repo: process.cwd(), pid: process.pid }) + "\\n");
});
process.stdin.on("end", () => process.exit(0));
process.stdin.resume();
`;

test("a ger serve that died is started again, and the log has one line for it", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ger-serve-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const script = path.join(dir, "serve.js");
  fs.writeFileSync(script, SERVE);
  const log: string[] = [];
  // The program the setting names; `serve --exit-with-stdin` comes after it, and the script ignores it.
  const process_ = new GerProcess(dir, () => [process.execPath, script], (line) => log.push(line));
  const again = new Promise<void>((resolve) => {
    process_.onDied = () => void process_.client().then(() => setTimeout(resolve, 200));
  });
  const first = await process_.client();
  await new Promise((resolve) => setTimeout(resolve, 200));
  const before = log.length;
  process.kill(first.hello.pid, "SIGKILL");
  await again;
  const restart = log.slice(before);
  process_.stop();
  assert.equal(restart.length, 1, restart.join("\n"));
  assert.match(restart[0], /^ger serve exited \(.+\) and was started again: listening on http:\/\/127\.0\.0\.1:\d+/);
  assert.ok(log.slice(0, before).some((line) => line.startsWith("starting: ")), "the first start is said in full");
  assert.equal(log.filter((line) => line.startsWith("warning: ")).length, 1, "its warning once, not again with the restart");
});
