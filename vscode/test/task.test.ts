// The task header: what it says while you review a chain or rework your own, and when it is there.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, Member, Overview, Rebase, ReviewState, Section } from "../src/api";
import { taskKey, taskLines, taskMode, taskVm } from "../src/taskRows";
import { commit, stack } from "./fixtures";

const review: ReviewState = { number: 102, patch_set: 2, commit: "c".repeat(40), subject: "parser", staged: true, mode: "branch", worktree: "/repo", branch: "change-102", return_to: "main" };

function member(number: number, extra: Partial<Member> = {}): Member {
  return { number, change_id: `I${number}`, subject: `change ${number}`, verified: 1, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: null, local_sha: null, sha: null, ...extra };
}

function chainOf(members: Member[]): Chain {
  const top = members.at(-1)!;
  return {
    key: "k",
    top: { number: top.number, change_id: top.change_id, subject: top.subject, url: null },
    project: "p",
    branch: "main",
    owner: { name: "Alice", email: null },
    depth: members.length,
    wait_age_seconds: 0,
    unreviewed_age_seconds: 0,
    last_activity: null,
    verified: 1,
    code_review: null,
    comments_unresolved: 0,
    attention_reasons: [],
    severity: "ok",
    partial_chain: false,
    members,
  };
}

const overview = (members: Member[]): Overview => ({
  host: "g",
  sections: [{ name: "to-review", query: "", summary: {}, chains: [chainOf(members)] } as Section],
  local: stack([]),
});

test("outside a review or a rework there is no header, and nothing steps aside", () => {
  assert.equal(taskMode(stack([])), undefined);
  assert.equal(taskVm(undefined, stack([]), { all: false }), undefined);
  assert.equal(taskKey(undefined, stack([])), undefined);
});

test("reviewing, the header says whose chain, which change of how many, what is left, each change's state in words, and the ways out", () => {
  const members = [
    member(101, { your_votes: { "Code-Review": 1 }, comments_unresolved: 1, comments_awaiting_reply: 1, comments_total: 2 }),
    member(102, { comments_unresolved: 3, comments_total: 3, attention_reasons: ["ci-failed"], your_turn: true }),
    member(103, { not_ready: "WIP" }),
  ];
  const vm = taskVm(overview(members), stack([], { staged_review: review }), { all: false })!;
  assert.equal(taskMode(stack([], { staged_review: review })), "review");
  assert.deepEqual(taskLines(vm), [
    "Reviewing Alice's chain: change 103 — change 2 of 3  [All chains]",
    "Left: 1 of 3 to vote on · 1 not ready for review · 3 comments to read",
    "  1. change 101 — you voted +1 · waits on a reply",
    "▸ 2. change 102 — reviewing · to review · 3 to answer · CI failed",
    "  3. change 103 — not ready: WIP",
    "[Leave review] back to main; the review's branches go",
  ]);
  // Every state has an icon and a word: none is a colour alone.
  for (const step of vm.steps) {
    for (const mark of step.marks) {
      assert.ok(mark.text && (mark.icon || mark.ring), `${step.title}: ${mark.text}`);
    }
  }
  // The comment state is the ring of commentState.ts: the share, and the shape of "to answer".
  assert.deepEqual(vm.steps[1].marks.find((mark) => mark.ring)?.ring, { share: 1, shape: "solid" });
  // All chains: the header offers the way back.
  assert.equal(taskVm(overview(members), stack([], { staged_review: review }), { all: true })?.views.label, "Only this task");
  // All chains holds for the chain, whichever change of it is reviewed.
  assert.equal(taskKey(overview(members), stack([], { staged_review: { ...review, number: 103 } })), taskKey(overview(members), stack([], { staged_review: review })));
});

test("a review whose chain is not in the inbox still says the mode, with the change alone", () => {
  const vm = taskVm(overview([member(500)]), stack([], { staged_review: review }), { all: false, busy: "Checking out 102…" })!;
  assert.deepEqual(taskLines(vm).slice(0, 4), ["Reviewing 102: parser — change 1 of 1  [All chains]", "Left: its chain is not in your inbox", "… Checking out 102…", "▸ 1. parser — reviewing"]);
});

test("reworking, the header says which change of how many, what is left over the chain, and Continue and Abort with what each does", () => {
  const rebase = { stop: "edit", actions: ["continue", "abort"], refused: {}, unmerged: [] } as unknown as Rebase;
  const commits = [
    commit("base", { rebase: "done" }),
    commit("stop", { rebase: "current", comments_unresolved: 2, comments_total: 3, needs_edit: true }),
    commit("next", { rebase: "todo", ci_failures: ["build"], needs_edit: true }),
    commit("top", { rebase: "todo" }),
  ];
  const vm = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false })!;
  assert.deepEqual(taskLines(vm), [
    "Reworking your chain on feature: top — change 2 of 4  [All chains]",
    "Left: 2 comments to answer · 1 CI failure · 2 commits need edits",
    "[Continue] commit this stop as it is, then replay the 2 commits above",
    "[Abort] put the branch back as it was before the rebase",
  ]);
  assert.deepEqual(vm.steps, [], "Local stack, right under the header, is the chain");
  // A conflict: Continue says why it does not run, in core's words.
  const conflict = { ...rebase, stop: "conflict", actions: ["abort", "skip"], refused: { continue: "a.py is still conflicted" }, unmerged: ["a.py"] } as unknown as Rebase;
  const stopped = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase: conflict }), { all: false })!;
  assert.deepEqual(taskLines(stopped).slice(2), [
    "[Continue] (not now: a.py is still conflicted)",
    "[Abort] put the branch back as it was before the rebase",
    "[Skip] leave this commit out and go on",
  ]);
});
