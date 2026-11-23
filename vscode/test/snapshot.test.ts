// The snapshot file both the gallery and a user's report use (src/snapshot.ts): what is read from
// one, and what is refused with words that say why.

import assert from "node:assert/strict";
import { test } from "node:test";

import { changeVm } from "../src/changeRows";
import { SNAPSHOT_FORMAT, Snapshot, readSnapshot } from "../src/snapshot";
import { stackVm } from "../src/stackRows";
import { commit, never, stack, state } from "./fixtures";

const file = (extra: object = {}) => JSON.stringify({ format: 1, name: "one commit", stack: null, change: null, ...extra });

test("a snapshot is read back as it was written, view models included", () => {
  const written: Snapshot = { format: SNAPSHOT_FORMAT, name: "one commit", stack: stackVm(stack([commit("One")]), undefined, never), change: changeVm(state()) };
  const read = readSnapshot(JSON.stringify(written));
  // What JSON cannot carry (a key that is undefined) is not in the file either.
  assert.deepEqual(read, JSON.parse(JSON.stringify(written)));
  assert.equal(read.stack?.rows[0].summary, "One");
  assert.equal(read.change?.message?.text, "Extract routing");
});

test("keys a snapshot does not know are left out, and a view that is not there is null", () => {
  const read = readSnapshot(JSON.stringify({ format: 1, name: "  from a report ", extension: "0.1.0", ger: "1.4.2", taken: "2026-11-20", change: { empty: "Nothing selected" } }));
  assert.deepEqual(read, { format: 1, name: "from a report", stack: null, change: { empty: "Nothing selected" } });
  // A view that only says why it shows nothing has no rows to list.
  assert.deepEqual(readSnapshot(file({ stack: { waiting: { text: "Starting ger serve…" } } })).stack, { waiting: { text: "Starting ger serve…" } });
});

test("a file that is not a snapshot is refused, saying what is wrong with it", () => {
  assert.throws(() => readSnapshot("{ not json"), /^Error: not JSON: /);
  assert.throws(() => readSnapshot("[]"), /a snapshot is a JSON object/);
  assert.throws(() => readSnapshot(file({ format: 2 })), /"format" is 2: this reads format 1/);
  assert.throws(() => readSnapshot(JSON.stringify({ name: "x" })), /"format" is undefined/);
  assert.throws(() => readSnapshot(file({ name: " " })), /"name" must say what the snapshot shows/);
  assert.throws(() => readSnapshot(file({ change: "a change" })), /"change" must be a view model \(an object\) or null/);
  assert.throws(() => readSnapshot(file({ stack: [] })), /"stack" must be a view model/);
  assert.throws(() => readSnapshot(file({ stack: { rows: [] } })), /"stack" needs its "rows" and "info" lists/);
});
