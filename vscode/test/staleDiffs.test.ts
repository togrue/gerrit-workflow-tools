import assert from "node:assert/strict";
import { test } from "node:test";

import { DiffMark, SAVE_ALL, StackNow, Unsaved, fateOf, liveTitle, markFragment, markOf, readMark, saveBeforeContinue, stackNow, staleTitle } from "../src/staleDiffs";
import { ScriptedUi } from "./e2e/scriptedUi";
import { commit, diff, stack } from "./fixtures";

const live: DiffMark = { commit: "aaa", branch: "feature", own: false, stale: false };
const stale: DiffMark = { ...live, stale: true };
const own: DiffMark = { ...live, own: true };
/** The stack of branch `feature`, HEAD on its last commit. */
const now = (commits: string[], extra: Partial<StackNow> = {}): StackNow => ({ branch: "feature", head: commits.at(-1) ?? null, commits: new Set(commits), ...extra });

test("a diff tab's mark is read back as written; a tab without one is not the extension's stack diff", () => {
  for (const mark of [live, stale, own, { ...own, stale: true }, { ...live, branch: null }, { ...live, branch: "team/feature&x" }]) {
    assert.deepEqual(readMark(markFragment(mark)), mark);
  }
  assert.equal(readMark(""), undefined);
  assert.equal(readMark("L12"), undefined);
});

test("a tab of a commit still in the stack stays as it is, setting on or off", () => {
  assert.equal(fateOf(live, false, now(["aaa", "bbb"]), false), "keep");
  assert.equal(fateOf(live, false, now(["aaa", "bbb"]), true), "keep");
});

test("a tab of a commit that left the stack is marked stale, or closed with the setting on", () => {
  assert.equal(fateOf(live, false, now(["bbb"]), false), "mark");
  assert.equal(fateOf(live, false, now(["bbb"]), true), "close");
});

test("a tab with unsaved edits is never closed: it is marked instead", () => {
  assert.equal(fateOf(live, true, now([]), true), "mark");
  assert.equal(fateOf(stale, true, now([]), true), "keep");
  assert.equal(fateOf(own, true, now(["aaa", "bbb"]), true), "mark");
});

test("a tab already marked stale stays so; the setting turned on closes it", () => {
  assert.equal(fateOf(stale, false, now([]), false), "keep");
  assert.equal(fateOf(stale, false, now([]), true), "close");
});

test("a tab with your file on its right is true only while its commit is HEAD", () => {
  // Standing on it: kept.
  assert.equal(fateOf(own, false, now(["aaa"]), false), "keep");
  // A commit on top of it, or a rebase stopped below it (it is still to be replayed): your file is another state.
  assert.equal(fateOf(own, false, now(["aaa", "bbb"]), false), "mark");
  assert.equal(fateOf(own, false, now(["zzz", "aaa"], { head: "zzz" }), true), "close");
  // A diff of the commit's own files is still true there.
  assert.equal(fateOf(live, false, now(["zzz", "aaa"], { head: "zzz" }), false), "keep");
  // HEAD on it again (the commit on top dropped, the rebase aborted): unmarked.
  assert.equal(fateOf({ ...own, stale: true }, false, now(["aaa"]), false), "unmark");
  assert.equal(fateOf({ ...own, stale: true }, false, now(["aaa", "bbb"]), false), "keep");
});

test("a tab of another branch's stack is not judged, and a stale one is unmarked once its commit is back", () => {
  // Another branch checked out: its stack holds none of feature's commits, and nothing is said.
  assert.equal(fateOf(live, false, now(["ccc"], { branch: "change-900" }), false), "keep");
  assert.equal(fateOf(live, false, now(["ccc"], { branch: "change-900" }), true), "keep");
  assert.equal(fateOf(own, false, now(["ccc"], { branch: null }), true), "keep");
  // feature back (or a rebase aborted): the commit is in its stack again.
  assert.equal(fateOf(stale, false, now(["aaa", "bbb"]), false), "unmark");
  assert.equal(fateOf(stale, true, now(["aaa"]), true), "unmark");
});

test("during a rebase the tab is judged against the branch being rebased", () => {
  const rebasing = stack([commit("One"), commit("Two")], { branch: null, head: commit("One").sha, rebase_in_progress: true });
  rebasing.rebase = { branch: "feature", onto: null, orig_head: null, stop: null, applied: true, current: null, unmerged: [], as_before: false, actions: [], refused: {} };
  assert.deepEqual(stackNow(rebasing), { branch: "feature", head: commit("One").sha, commits: new Set([commit("One").sha, commit("Two").sha]) });
});

test("only a diff whose right side is a local commit of the stack is marked", () => {
  const one = commit("One");
  const local = stack([one]);
  // Against its base, or Gerrit vs. local: the local commit on the right.
  assert.deepEqual(markOf(diff({ commit: one.sha }), local, true), { commit: one.sha, branch: "feature", own: true, stale: false });
  assert.deepEqual(markOf(diff({ commit: one.sha, since: { patch_set: 2, current_patch_set: 2, to_patch_set: null, up_to_date: false, identical: false, rebased: false } }), local, false), {
    commit: one.sha,
    branch: "feature",
    own: false,
    stale: false,
  });
  // Patchset to patchset, even where the stack has the right side's very commit: Gerrit's, never stale.
  const between = { patch_set: 1, current_patch_set: 2, to_patch_set: 2, up_to_date: false, identical: false, rebased: false };
  assert.equal(markOf(diff({ commit: one.sha, since: between }), local, false), undefined);
  assert.equal(markOf(diff({ commit: one.sha, local_commit: false }), local, false), undefined);
  // A commit not in the stack, or no stack known.
  assert.equal(markOf(diff({ commit: commit("Other").sha }), local, false), undefined);
  assert.equal(markOf(diff({ commit: one.sha }), undefined, false), undefined);
});

test("the stale title is the tab's title marked once, and unmarked back", () => {
  assert.equal(staleTitle("b.txt (6827cbfe)"), "b.txt (6827cbfe) — stale");
  assert.equal(staleTitle(staleTitle("b.txt (6827cbfe)")), "b.txt (6827cbfe) — stale");
  assert.equal(liveTitle(staleTitle("b.txt (6827cbfe)")), "b.txt (6827cbfe)");
  assert.equal(liveTitle("b.txt (6827cbfe)"), "b.txt (6827cbfe)");
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

test("unsaved edits are named in a dialog; Save All saves each, then goes on", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("b.txt", "src/e.txt");
  ui.script([{ button: SAVE_ALL }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), true);
  assert.deepEqual(saved, ["b.txt", "src/e.txt"]);
  const { lines, problems } = ui.drain();
  assert.deepEqual(problems, []);
  assert.deepEqual(lines, [
    "dialog (warning): Save 2 files before the rebase goes on?  [Save All] [Cancel]",
    "    detail: b.txt and src/e.txt have edits that are not saved. ger rebase --continue sees only the files on disk: saved, it says what the edits still need before the rebase can go on.",
    '    → pressed "Save All"',
  ]);
});

test("cancelled, nothing is saved and the rebase does not go on", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt");
  ui.script([{ dismiss: true }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, []);
  assert.equal(ui.drain().lines[0], "dialog (warning): Save e.txt before the rebase goes on?  [Save All] [Cancel]");
});

test("a file that cannot be saved stops the rebase, and says which", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt", "locked.txt");
  ui.script([{ button: SAVE_ALL }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, ["e.txt"]);
  assert.equal(ui.drain().lines.at(-1), "notification (error): Could not save locked.txt: the rebase did not go on.");
});
