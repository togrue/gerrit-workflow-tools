import assert from "node:assert/strict";
import { test } from "node:test";

import { CONTINUE_UNSAVED, DiffMark, SAVE_ALL, StackNow, TabNow, Unsaved, fateOf, liveTitle, markFragment, markOf, readMark, saveBeforeContinue, stackNow, staleTitle } from "../src/staleDiffs";
import { ScriptedUi } from "./e2e/scriptedUi";
import { commit, diff, stack } from "./fixtures";

const live: DiffMark = { commit: "aaa", branch: "feature", own: false, stale: false };
const stale: DiffMark = { ...live, stale: true };
const own: DiffMark = { ...live, own: true };
/** The stack of branch `feature`, HEAD on its last commit. */
/** A tab as a later sweep sees it, its right side loaded; `dirty`: with unsaved edits. */
const clean: TabNow = { dirty: false, loaded: true, first: false };
const dirty: TabNow = { ...clean, dirty: true };
const now = (commits: string[], extra: Partial<StackNow> = {}): StackNow => ({ branch: "feature", head: commits.at(-1) ?? null, commits: new Set(commits), ...extra });

test("a diff tab's mark is read back as written; a tab without one is not the extension's stack diff", () => {
  for (const mark of [live, stale, own, { ...own, stale: true }, { ...live, branch: null }, { ...live, branch: "team/feature&x" }]) {
    assert.deepEqual(readMark(markFragment(mark)), mark);
  }
  assert.equal(readMark(""), undefined);
  assert.equal(readMark("L12"), undefined);
});

test("a tab of a commit still in the stack stays as it is, setting on or off", () => {
  assert.equal(fateOf(live, clean, now(["aaa", "bbb"]), false), "keep");
  assert.equal(fateOf(live, clean, now(["aaa", "bbb"]), true), "keep");
});

test("a tab of a commit that left the stack is marked stale, or closed with the setting on", () => {
  assert.equal(fateOf(live, clean, now(["bbb"]), false), "mark");
  assert.equal(fateOf(live, clean, now(["bbb"]), true), "close");
});

test("a tab with unsaved edits is never closed: it is marked instead", () => {
  assert.equal(fateOf(live, dirty, now([]), true), "mark");
  assert.equal(fateOf(stale, dirty, now([]), true), "keep");
  assert.equal(fateOf(own, dirty, now(["aaa", "bbb"]), true), "mark");
});

test("a tab that may hold unsaved edits VS Code has not read in is marked, not closed, and closed on a later sweep", () => {
  // Your file on the right, its document not loaded (a tab restored at startup): it may have edits.
  const unread: TabNow = { ...clean, loaded: false };
  assert.equal(fateOf(own, unread, now(["zzz", "aaa"], { head: "zzz" }), true), "mark");
  assert.equal(fateOf({ ...own, stale: true }, unread, now(["zzz", "aaa"], { head: "zzz" }), true), "keep");
  assert.equal(fateOf({ ...own, stale: true }, clean, now(["zzz", "aaa"], { head: "zzz" }), true), "close");
  // Both sides the commit's: nothing of yours to lose, loaded or not.
  assert.equal(fateOf(live, unread, now(["bbb"]), true), "close");
  // The first sweep since the extension started closes nothing: the tabs are as VS Code restored them.
  const restored: TabNow = { ...clean, first: true };
  assert.equal(fateOf(live, restored, now(["bbb"]), true), "mark");
  assert.equal(fateOf(stale, restored, now(["bbb"]), true), "keep");
  assert.equal(fateOf(stale, restored, now(["aaa"]), true), "unmark");
  assert.equal(fateOf(stale, clean, now(["bbb"]), true), "close");
});

test("a tab already marked stale stays so; the setting turned on closes it", () => {
  assert.equal(fateOf(stale, clean, now([]), false), "keep");
  assert.equal(fateOf(stale, clean, now([]), true), "close");
});

test("a tab with your file on its right is true only while its commit is HEAD", () => {
  // Standing on it: kept.
  assert.equal(fateOf(own, clean, now(["aaa"]), false), "keep");
  // A commit on top of it, or a rebase stopped below it (it is still to be replayed): your file is another state.
  assert.equal(fateOf(own, clean, now(["aaa", "bbb"]), false), "mark");
  assert.equal(fateOf(own, clean, now(["zzz", "aaa"], { head: "zzz" }), true), "close");
  // A diff of the commit's own files is still true there.
  assert.equal(fateOf(live, clean, now(["zzz", "aaa"], { head: "zzz" }), false), "keep");
  // HEAD on it again (the commit on top dropped, the rebase aborted): unmarked.
  assert.equal(fateOf({ ...own, stale: true }, clean, now(["aaa"]), false), "unmark");
  assert.equal(fateOf({ ...own, stale: true }, clean, now(["aaa", "bbb"]), false), "keep");
});

test("a tab with your file on its right goes stale when its commit leaves the stack with HEAD still on it", () => {
  // Submitted, and reachable from upstream after a fetch: the stack is empty, HEAD has not moved.
  const merged = now([], { head: "aaa" });
  assert.equal(fateOf(own, clean, merged, false), "mark");
  assert.equal(fateOf(own, clean, merged, true), "close");
  // With unsaved edits, or its document not read in, it is marked, never closed.
  assert.equal(fateOf(own, dirty, merged, true), "mark");
  assert.equal(fateOf(own, { ...clean, loaded: false }, merged, true), "mark");
  // As a diff of the commit's own files is.
  assert.equal(fateOf(live, clean, merged, true), "close");
});

test("a tab of another branch's stack is not judged, and a stale one is unmarked once its commit is back", () => {
  // Another branch checked out: its stack holds none of feature's commits, and nothing is said.
  assert.equal(fateOf(live, clean, now(["ccc"], { branch: "change-900" }), false), "keep");
  assert.equal(fateOf(live, clean, now(["ccc"], { branch: "change-900" }), true), "keep");
  assert.equal(fateOf(own, clean, now(["ccc"], { branch: null }), true), "keep");
  // feature back (or a rebase aborted): the commit is in its stack again.
  assert.equal(fateOf(stale, clean, now(["aaa", "bbb"]), false), "unmark");
  assert.equal(fateOf(stale, dirty, now(["aaa"]), true), "unmark");
});

test("during a rebase the tab is judged against the branch being rebased", () => {
  const rebasing = stack([commit("One"), commit("Two")], { branch: null, head: commit("One").sha, rebase_in_progress: true });
  rebasing.rebase = { branch: "feature", onto: null, orig_head: null, stop: null, applied: true, current: null, unmerged: [], as_before: false, actions: [], refused: {} };
  assert.deepEqual(stackNow(rebasing), { branch: "feature", head: commit("One").sha, commits: new Set([commit("One").sha, commit("Two").sha]) });
});

test("in a staged review, a diff with your file on its right is true while the review holds its change", () => {
  const [base, change, next] = [commit("Base"), commit("Change"), commit("Next")];
  const review = (held: typeof change) => ({ number: 2155, patch_set: 1, commit: held.sha, subject: held.summary, staged: true, mode: "worktree" as const, worktree: "/r", branch: "change-2155" });
  // HEAD sits at the change's parent; the change is what it stands for.
  const staged = stack([base, change], { branch: null, head: base.sha, staged_review: review(change) });
  assert.equal(stackNow(staged).head, change.sha);
  // The interdiff to the patchset the review holds has your file on its right too: marked.
  const between = { patch_set: 1, current_patch_set: 2, to_patch_set: 2, up_to_date: false, identical: false, rebased: false };
  const mark = markOf(diff({ commit: change.sha, local_commit: false, since: between }), staged, true);
  assert.deepEqual(mark, { commit: change.sha, branch: null, own: true, stale: false });
  assert.equal(fateOf(mark!, clean, stackNow(staged), false), "keep");
  // Review Next Change: the working files hold the next change, and the tab says it is stale.
  const moved = stack([base, change, next], { branch: null, head: change.sha, staged_review: review(next) });
  assert.equal(fateOf(mark!, clean, stackNow(moved), false), "mark");
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
    "dialog (warning): Save 2 files first? ger rebase --continue then says what they still need.  [Save All] [Continue without saving] [Cancel]",
    "    detail: b.txt and src/e.txt have edits that are not saved, and ger rebase --continue sees only the files on disk. Continue without saving: the edits stay in the editor, git rewrites the files under them, and VS Code says so when you save.",
    '    → pressed "Save All"',
  ]);
});

test("cancelled, nothing is saved and the rebase does not go on", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt");
  ui.script([{ dismiss: true }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, []);
  assert.equal(ui.drain().lines[0], "dialog (warning): Save e.txt first? ger rebase --continue then says what it still needs.  [Save All] [Continue without saving] [Cancel]");
});

test("Continue without saving goes on and leaves the edits in the editor", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt");
  ui.script([{ button: CONTINUE_UNSAVED }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), true);
  assert.deepEqual(saved, []);
  assert.deepEqual(ui.drain().problems, []);
});

test("a file that cannot be saved stops the rebase, and says which", async () => {
  const ui = new ScriptedUi((line) => line);
  const { unsaved, saved } = files("e.txt", "locked.txt");
  ui.script([{ button: SAVE_ALL }]);
  assert.equal(await saveBeforeContinue(ui, unsaved), false);
  assert.deepEqual(saved, ["e.txt"]);
  assert.equal(ui.drain().lines.at(-1), "notification (error): Could not save locked.txt: the rebase did not go on.");
});
