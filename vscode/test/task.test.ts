// The task header: what it says while you review a chain or rework your own, and when it is there.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, Member, Overview, Rebase, ReviewState, Section } from "../src/api";
import { taskFold, taskKey, taskLines, taskMode, taskVm } from "../src/taskRows";
import { chainCounts, changeVm } from "../src/changeRows";
import { chain, comment, commit, shown, stack, state as fixtureState } from "./fixtures";

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
    "Left: 1 of 3 to vote on · 1 not ready for review · 3 comments to answer",
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

test("a change of the chain the inbox no longer lists stays in the header, marked so, and the count stays (W7)", () => {
  // 102 got a new patchset: its Verified vote went, and with it its place in the to-review query.
  const vm = taskVm(overview([member(103, { your_turn: true })]), stack([], { staged_review: review }), { all: false, chain: [102, 103] })!;
  assert.equal(vm.position, "change 1 of 2");
  assert.deepEqual(taskLines(vm).slice(1, 4), ["Left: 2 of 2 to vote on · 1 not in your inbox now", "▸ 1. parser — reviewing · not in your inbox now", "  2. change 103 — to review"]);
});

test("a change of the chain the inbox lists apart (on an older base) is shown as the inbox has it, not as missing", () => {
  const apart: Overview = { host: "g", sections: [{ name: "to-review", query: "", summary: {}, chains: [chainOf([member(102, { your_turn: true })]), chainOf([member(103, { your_turn: true })])] } as Section], local: stack([]) };
  const vm = taskVm(apart, stack([], { staged_review: review }), { all: false, chain: [102, 103] })!;
  assert.deepEqual(taskLines(vm).slice(1, 4), ["Left: 2 of 2 to vote on", "▸ 1. change 102 — reviewing · to review", "  2. change 103 — to review"]);
});

test("the change under review stays in the header when core's chain (open changes only) leaves it out: abandoned meanwhile", () => {
  const vm = taskVm(overview([member(103, { your_turn: true })]), stack([], { staged_review: review }), { all: false, chain: [103, 104] })!;
  assert.equal(vm.position, "change 1 of 3");
  assert.equal(taskLines(vm)[2], "▸ 1. parser — reviewing · not in your inbox now");
});

test("while Gerrit refuses, the header keeps the chain it knew and says what is left is unknown, never 'not in your inbox' (W2)", () => {
  const away: Overview = { host: "g", error: { code: "GERRIT", message: "rejected", reason: "credentials" }, local: stack([]) };
  const vm = taskVm(away, stack([], { staged_review: review }), { all: false, chain: [101, 102, 103], inboxUnknown: true, subjects: new Map([[101, "lexer"]]) })!;
  assert.equal(vm.position, "change 2 of 3");
  // Said once, in what is left; each change keeps the name the inbox last gave it.
  assert.deepEqual(taskLines(vm).slice(1, 4), ["Left: what is left is unknown: Gerrit could not be asked", "  1. lexer — ", "▸ 2. parser — reviewing"]);
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
    "Left: 2 comments to answer · CI failed on 1 commit · 2 commits need edits · 2 commits to replay",
    "  1. base — replayed",
    "▸ 2. stop — stopped here · 2 to answer · needs edits",
    "  3. next — to come · needs edits · CI failed",
    "  4. top — to come",
    "[Continue] commit this stop as it is, then replay the 2 commits above",
    "[Abort] put the branch back as it was before the rebase",
  ]);
  // The chain is the header's, oldest first, as while reviewing: Local stack steps aside. A click shows the commit.
  assert.deepEqual(vm.steps.map((step) => [step.key, step.phase]), commits.map((c) => [c.sha, c.rebase]));
  // The Change view's count of the commit it shows wins over the served one: the header and the view say one number.
  const shown = { key: commits[1].sha, counts: { comments_unresolved: 2, comments_to_answer: 1, comments_drafted: 1, comments_awaiting_reply: 0, comments_total: 3 } };
  const counted = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false, shown })!;
  assert.equal(taskLines(counted)[1], "Left: 1 comment to answer · CI failed on 1 commit · 2 commits need edits · 2 commits to replay");
  assert.equal(taskLines(counted)[3], "▸ 2. stop — stopped here · 1 to answer · your drafts · needs edits");
  // A step running in the terminal: the header says so, and its buttons wait for it.
  const busy = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false, busy: "Continuing the rebase in the ger terminal…" })!;
  assert.deepEqual(taskLines(busy).filter((line) => line.startsWith("…") || line.startsWith("[")), [
    "… Continuing the rebase in the ger terminal…",
    "[Continue] (not now: the step before it is still running)",
    "[Abort] (not now: the step before it is still running)",
  ]);
  // A conflict: Continue says why it does not run in one line; core's whole refusal, commands and all, is its tooltip.
  const refusal = "the index has unresolved conflicts; resolve the conflicts in a.py, `git add` them, then `ger rebase --continue` to finish";
  const conflict = { ...rebase, stop: "conflict", actions: ["abort", "skip"], refused: { continue: refusal }, unmerged: ["a.py"] } as unknown as Rebase;
  const stopped = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase: conflict }), { all: false })!;
  assert.deepEqual(taskLines(stopped).slice(6), [
    "[Continue] (not now: resolve a.py first)",
    "[Abort] put the branch back as it was before the rebase",
    "[Skip] leave this commit out and go on",
  ]);
  assert.equal(stopped.actions[0].tooltip, refusal);
  // State 17 of the outside-the-happy-path run: "nothing left to answer or fix" sat over an open conflict, C still to come.
  assert.equal(taskLines(stopped)[1], "Left: 1 conflict to resolve · 2 comments to answer · CI failed on 1 commit · 2 commits need edits · 2 commits to replay");
});

test("at an edit stop with a fix saved and not amended, the header says it, and Continue waits for it before it is pressed", () => {
  // State 16 of the outside-the-happy-path run: only the commit's file was listed, and Continue offered to "commit this stop as it is".
  const refusal = "you have unstaged changes to calc/ops.py; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`";
  const rebase = { stop: "edit", actions: ["abort"], refused: { continue: refusal }, unmerged: [], uncommitted: ["calc/ops.py"] } as unknown as Rebase;
  const commits = [commit("stop", { rebase: "current" })];
  const vm = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false })!;
  assert.equal(taskLines(vm)[1], "Left: calc/ops.py not amended");
  assert.deepEqual(vm.actions.map((action) => [action.label, action.notNow]), [
    ["Continue", "calc/ops.py changed, not amended: amend or stash it first"],
    ["Abort", undefined],
  ]);
  assert.equal(vm.actions[0].tooltip, refusal);
});

test("staged files at an edit stop are listed before Continue, which says it amends them in", () => {
  // Continue amends what is staged without a word: the view lists those files beside the ones it waits for.
  const rebase = { stop: "edit", applied: true, actions: ["continue", "abort"], refused: {}, unmerged: [], uncommitted: [], staged: ["calc/ops.py", "README.md"] } as unknown as Rebase;
  const commits = [commit("stop", { rebase: "current" }), commit("above", { rebase: "todo" })];
  const vm = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false })!;
  assert.equal(vm.actions[0].detail, "amend the 2 staged files into this commit, then replay the 1 commit above");
  const view = changeVm(fixtureState({ notAmended: ["calc/ops.py"], staged: ["calc/ops.py", "README.md"] }));
  assert.equal(view.conflicts?.title, "Changed at this stop");
  assert.equal(view.conflicts?.summary, "2 files", "a file staged and changed again is one file");
  assert.deepEqual(view.conflicts?.rows.map((row) => [row.text, row.detail]), [
    ["calc/ops.py", "changed, not amended"],
    ["calc/ops.py", "staged, goes into this commit"],
    ["README.md", "staged, goes into this commit"],
  ]);
  assert.equal(changeVm(fixtureState({ staged: ["README.md"] })).conflicts?.title, "Staged, go into this commit");
  assert.equal(changeVm(fixtureState({})).conflicts, undefined);
});

test("how the last step ended is said in the header until the next action, in ger's words, and not while a step runs", () => {
  const rebase = { stop: "conflict", actions: ["abort", "skip"], refused: {}, unmerged: ["a.py"] } as unknown as Rebase;
  const commits = [commit("stop", { rebase: "current" })];
  const outcome = { text: "refused: you have unstaged changes to a.py", ok: false, more: ["next: `git add` it"] };
  const vm = taskVm(undefined, stack(commits, { rebase_in_progress: true, rebase }), { all: false, outcome })!;
  assert.deepEqual(taskLines(vm).slice(2, 4), ["⚠ refused: you have unstaged changes to a.py", "  next: `git add` it"]);
});

test("the header and the Change view say one number of comments for the change shown: the view's count wins over the served one", () => {
  // Served with the inbox: 3 to answer. The Change view's chains: one yours to read, one your draft
  // answers, one waiting on the author's reply to you, one resolved.
  const members = [member(101, { your_votes: { "Code-Review": 1 } }), member(102, { comments_unresolved: 3, comments_total: 4, your_turn: true })];
  const chains = [
    chain("read", [comment("Why?")]),
    chain("answered", [comment("Rename?"), comment("Done", { state: "draft", comment_id: "d", author: undefined })]),
    chain("waiting", [comment("Typo"), comment("Which?", { author: "you" })], { turn: "theirs" }),
    chain("done", [comment("Ok")], { resolved: true }),
  ];
  const view = changeVm(fixtureState({ local: false, shown: shown({ own: false }), chains }));
  assert.equal(view.comments?.summary, "1 to answer · 1 answered by your drafts (Vote and Publish sends them) · 1 waiting on a reply");
  const counts = chainCounts(chains);
  const header = taskVm(overview(members), stack([], { staged_review: review }), { all: false, shown: { key: "102", counts } })!;
  assert.equal(taskLines(header)[1], "Left: 1 of 2 to vote on · 1 comment to answer");
  assert.match(taskLines(header)[3], /1 to answer/);
  // Without the view's count (it is loading), the served one.
  assert.match(taskLines(taskVm(overview(members), stack([], { staged_review: review }), { all: false })!)[1], /3 comments to answer/);
});

test("a long chain folds, in a narrow side bar, to the steps around the current one and what the rest are (state 22)", () => {
  const step = (position: number, phase: "done" | "current" | "todo") => ({ key: String(position), position, title: `matrix demo: step ${position}`, current: phase === "current", phase, marks: [], tooltip: "" });
  const eight = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => step(n, n < 4 ? "done" : n === 4 ? "current" : "todo"));
  assert.deepEqual(taskFold(eight, "rework"), { from: 2, to: 4, before: "2 replayed", after: "3 to come" });
  // A fold of one step saves no line: it is shown.
  const six = eight.slice(0, 6).map((_s, i) => step(i + 1, i < 1 ? "done" : i === 1 ? "current" : "todo"));
  assert.deepEqual(taskFold(six, "rework"), { from: 0, to: 2, before: "", after: "3 to come" });
  // Reviewing: earlier and later changes; five or fewer: nothing folds.
  const review = eight.map((s) => ({ ...s, phase: undefined }));
  assert.deepEqual(taskFold(review, "review"), { from: 2, to: 4, before: "2 earlier", after: "3 later" });
  assert.equal(taskFold(eight.slice(0, 5), "rework"), undefined);
});
