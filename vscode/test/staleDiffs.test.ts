import assert from "node:assert/strict";
import { test } from "node:test";

import { SAVE_AND_CONTINUE, Unsaved, fateOf, markFragment, readMark, saveBeforeContinue, staleTitle } from "../src/staleDiffs";
import { ScriptedUi } from "./e2e/scriptedUi";

const live = { commit: "aaa", stale: false };

test("a diff tab's mark is read back as written; a tab without one is not the extension's stack diff", () => {
  assert.deepEqual(readMark(markFragment(live)), live);
  assert.deepEqual(readMark(markFragment({ commit: "aaa", stale: true })), { commit: "aaa", stale: true });
  assert.equal(readMark(""), undefined);
  assert.equal(readMark("L12"), undefined);
});

test("a tab of a commit still in the stack stays as it is, setting on or off", () => {
  const stack = new Set(["aaa", "bbb"]);
  assert.equal(fateOf(live, false, stack, false), "keep");
  assert.equal(fateOf(live, false, stack, true), "keep");
});

test("a tab of a commit that left the stack is marked stale, or closed with the setting on", () => {
  const stack = new Set(["bbb"]);
  assert.equal(fateOf(live, false, stack, false), "mark");
  assert.equal(fateOf(live, false, stack, true), "close");
});

test("a tab with unsaved edits is never closed: it is marked instead", () => {
  assert.equal(fateOf(live, true, new Set(), true), "mark");
  assert.equal(fateOf({ commit: "aaa", stale: true }, true, new Set(), true), "keep");
});

test("a tab already marked stale stays so; the setting turned on closes it", () => {
  const stale = { commit: "aaa", stale: true };
  assert.equal(fateOf(stale, false, new Set(), false), "keep");
  assert.equal(fateOf(stale, false, new Set(), true), "close");
  // The commit back in the stack (an abort): it still is the commit the title names.
  assert.equal(fateOf(stale, false, new Set(["aaa"]), false), "keep");
});

test("the stale title is the tab's title marked once", () => {
  assert.equal(staleTitle("b.txt (6827cbfe)"), "b.txt (6827cbfe) — stale");
  assert.equal(staleTitle(staleTitle("b.txt (6827cbfe)")), "b.txt (6827cbfe) — stale");
});

function files(...names: string[]): { unsaved: Unsaved[]; saved: string[] } {
  const saved: string[] = [];
  const unsaved = names.map((name) => ({ name, save: () => Promise.resolve(name !== "locked.txt" && saved.push(name) > 0) }));
  return { unsaved, saved };
}

test("with nothing unsaved, Continue goes on without asking", async () => {
  const ui = new ScriptedUi((line) => line);
  assert.equal(await saveBeforeContinue(ui, []), true);
  assert.deepEqual(ui.drain().lines, []);
});

test("unsaved edits are named in a dialog; Save All and Continue saves each, then goes on", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("b.txt", "src/e.txt");
  ui.script([{ button: SAVE_AND_CONTINUE }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), true);
  assert.deepEqual(saved, ["b.txt", "src/e.txt"]);
  const { lines, problems } = ui.drain();
  assert.deepEqual(problems, []);
  assert.deepEqual(lines, [
    "dialog (warning): Save 2 files before the rebase goes on?  [Save All and Continue] [Cancel]",
    "    detail: b.txt and src/e.txt have edits that are not saved. git rebase --continue goes on with the files as they are on disk: it would leave these edits out, and may then change the files under them.",
    '    → pressed "Save All and Continue"',
  ]);
});

test("cancelled, nothing is saved and the rebase does not go on", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt");
  ui.script([{ dismiss: true }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, []);
  assert.equal(ui.drain().lines[0], "dialog (warning): Save e.txt before the rebase goes on?  [Save All and Continue] [Cancel]");
});

test("a file that cannot be saved stops the rebase, and says which", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt", "locked.txt");
  ui.script([{ button: SAVE_AND_CONTINUE }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, ["e.txt"]);
  assert.equal(ui.drain().lines.at(-1), "notification (error): Could not save locked.txt: the rebase did not go on.");
});
