// The inspection points: one record per user action, its first visible response and when it
// settled, joined to the requests it caused and to what ger serve said they cost.

import assert from "node:assert/strict";
import { test } from "node:test";

import { QUIET_MS, TraceRecord, Tracer } from "../src/trace";

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function tracer(): { tracer: Tracer; records: TraceRecord[]; at: (ms: number) => void } {
  const records: TraceRecord[] = [];
  let clock = 1000;
  return { tracer: new Tracer((record) => records.push(record), () => clock), records, at: (ms) => (clock = 1000 + ms) };
}

const serveTrace = JSON.stringify({ action: "a1", ms: 40, queued_ms: 2, git: { count: 3, ms: 30 }, gerrit: { count: 1, ms: 8 }, calls: [["git", "git status", 12]], calls_left_out: 2 });

test("an action records its requests, its first paint and when it settled", async () => {
  const { tracer: t, records, at } = tracer();
  await t.action("ger.nextComment", "command", async () => {
    const span = t.request("GET", "/show");
    assert.equal(span.headers["X-Ger-Action"], "a1", "the request names its action");
    at(50);
    span.end(200, serveTrace);
    const seq = t.render("change");
    at(70);
    t.painted(seq, 1080);
  });
  await t.idle();
  const kinds = records.map((record) => record.kind);
  assert.deepEqual(kinds, ["request", "serve", "action"]);
  const [request, serve, action] = records;
  assert.deepEqual({ ...request }, { kind: "request", t: 1000, action: "a1", rid: 1, method: "GET", path: "/show", status: 200, ms: 50 });
  assert.equal(serve.rid, 1, "the server's record joins the request's");
  assert.equal(serve.action, "a1");
  assert.deepEqual(serve.git, { count: 3, ms: 30 });
  assert.equal(action.name, "ger.nextComment");
  assert.equal(action.first, "paint:change");
  assert.equal(action.first_ms, 80, "the page's own paint time counts");
  assert.equal(action.settled_ms, 80);
  assert.equal(action.requests, 1);
  assert.equal(action.git, 3);
  assert.equal(action.gerrit, 1);
  assert.equal(action.paints, 1);
});

test("a command run by another action is part of it", async () => {
  const { tracer: t, records } = tracer();
  await t.action("change.command ger.openChange", "webview", async () => {
    await t.action("ger.openChange", "command", async () => undefined);
  });
  await t.idle();
  assert.equal(records.length, 1);
  assert.deepEqual(records[0].inner, ["ger.openChange"]);
});

test("a prompt settles the action; its answer is the next action", async () => {
  const { tracer: t, records, at } = tracer();
  let answer: () => void = () => undefined;
  const asked = new Promise<void>((resolve) => (answer = resolve));
  const run = t.action("ger.vote", "command", async () => {
    t.visible("ui:pick");
    const answered = t.waitForUser();
    await asked;
    answered("pick");
    t.request("POST", "/review").end(200, null);
  });
  at(10);
  await pause(QUIET_MS + 50);
  assert.deepEqual(
    records.map((record) => [record.name, record.first, record.requests]),
    [["ger.vote", "ui:pick", 0]],
    "settled while it waits on the user",
  );
  at(5000);
  answer();
  await run;
  await t.idle();
  assert.equal(records.at(-1)?.name, "ger.vote › pick");
  assert.equal(records.at(-1)?.via, "answer");
  assert.equal(records.at(-1)?.requests, 1, "the request after the answer is the answer's");
});

test("what VS Code shows outside any action answers the latest one still open", async () => {
  const { tracer: t, records, at } = tracer();
  let finish: () => void = () => undefined;
  const run = t.action("ger.openDiff", "command", () => new Promise<void>((resolve) => (finish = resolve)));
  at(30);
  t.visible("editor:file:a.py");
  finish();
  await run;
  await t.idle();
  assert.equal(records[0].first, "editor:file:a.py");
  assert.equal(records[0].first_ms, 30);
  t.visible("editor:file:b.py");
  assert.equal(records.length, 1, "nothing is open: nothing to answer");
});

test("a request outside any action is background", () => {
  const { tracer: t, records } = tracer();
  const span = t.request("GET", "/overview");
  assert.equal(span.headers["X-Ger-Action"], "background");
  span.end(200, null);
  span.end(0, null);
  assert.equal(records.length, 1, "ended once");
  assert.equal(records[0].action, null);
});
