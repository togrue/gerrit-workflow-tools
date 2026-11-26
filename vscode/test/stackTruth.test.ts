// What Local stack says outside the happy path (states 14-20 of the outside-the-happy-path run):
// how the last step ended, a detached HEAD, a gone upstream, a commit push gives a Change-Id, which
// title commands would do something, and Abort's question.

import assert from "node:assert/strict";
import { test } from "node:test";

import { pushable } from "../src/nextAction";
import { DETACHED_WORDS, abortQuestion, stackLines, stackVm, titleOffers } from "../src/stackRows";
import { commit, never, stack } from "./fixtures";

test("out of a task, the last step's outcome is Local stack's first row, in ger's words; inside one the header says it", () => {
  const outcome = { text: "rebase aborted: feature is at 1234567 top, as before the rebase", ok: true, more: ["kept: your changes to a.txt, not amended, in stash@{0}; `git stash apply` brings them back"] };
  const vm = stackVm(stack([commit("top", { is_head: true })]), undefined, never, () => 0, false, outcome);
  assert.equal(stackLines(vm)[0], `${outcome.text} — ${outcome.more[0]}`);
  assert.equal(vm.info[0].tone, "good");
  assert.equal(stackVm(stack([commit("top")]), undefined, never, () => 0, true, outcome).info.length, 0);
  // Gerrit's refusal of a missing branch, with core's next step: the way to another upstream is offered beside it.
  const refused = { text: "Gerrit rejected the push: branch no-such-branch not found", ok: false, more: ["next: `git branch --set-upstream-to=origin/main`"] };
  const row = stackVm(stack([commit("top")]), undefined, never, () => 0, false, refused).info[0];
  assert.deepEqual([row.tone, row.actions?.map((action) => action.command.id)], ["warn", ["ger.setUpstream"]]);
});

test("a detached HEAD says what a commit made there becomes, and offers a branch", () => {
  const vm = stackVm(stack([commit("top")], { branch: null }), undefined, never);
  assert.equal(vm.info[0].text, DETACHED_WORDS.text);
  assert.deepEqual(vm.info[0].actions?.map((action) => [action.label, action.command.id]), [[DETACHED_WORDS.create, "git.branch"]]);
});

test("an upstream configured and gone is said as gone, not as none", () => {
  const local = { error: { code: "config", message: "The upstream origin/gone of branch 'feature' is gone" }, needs_upstream: true };
  const choice = { branch: "feature", upstream: null, gone: "origin/gone", suggested: "origin/main", why: "nearest", candidates: [] };
  assert.equal(stackVm(local, choice, never).info[0].text, "The upstream origin/gone of feature is gone, so ger cannot tell where your stack starts");
});

test("a commit push gives a Change-Id is said as the push will do it, not as held back", () => {
  const vm = stackVm(stack([commit("no id", { adds_change_id: true, held_back: false, patchset_status: "absent", change_id: null })]), undefined, never);
  assert.ok(vm.rows[0].notes.includes("push adds a Change-Id"));
  assert.match(vm.rows[0].tooltip, /ger push gives it one before its preview and sends it as a new change/);
});

test("the title offers Work on Next and Push only where they would do something", () => {
  const empty = stack([]);
  assert.deepEqual(titleOffers(empty, pushable), { workOnNext: false, push: false });
  const unpushed = stack([commit("new", { patchset_status: "absent", needs_edit: false })]);
  assert.deepEqual(titleOffers(unpushed, pushable), { workOnNext: false, push: true });
  const conflict = stack([commit("stop", { rebase: "current", needs_edit: true })], {
    rebase_in_progress: true,
    push_blocked: { reason: "rebase", message: "a rebase is in progress" },
    rebase: { branch: "feature", onto: null, orig_head: null, stop: "conflict", applied: false, current: null, unmerged: ["a.py"], as_before: false, actions: ["abort", "skip"], refused: {} },
  });
  assert.deepEqual(titleOffers(conflict, pushable), { workOnNext: false, push: false });
  assert.deepEqual(titleOffers({ error: { code: "config", message: "no upstream" }, needs_upstream: true }, pushable), { workOnNext: false, push: false });
});

test("Abort's question names the commits made in the rebase that go with it, and what a stash keeps", () => {
  const question = abortQuestion({ uncommitted: ["calc/ops.py"], rewritten: [{ sha: "46e97e7aa", short_sha: "46e97e7", summary: "ops: note on add" }], resolution: false, asks: true });
  assert.equal(question.message, "Abort the rebase, and with it 1 rewritten commit?");
  assert.match(question.detail, /Gone from the branch with it, made in this rebase: 46e97e7 "ops: note on add"\./);
  assert.match(question.detail, /calc\/ops\.py, not committed, is kept in a stash: git stash apply brings it back\./);
});
