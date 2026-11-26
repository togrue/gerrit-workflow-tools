// Next, the review focus and the comments a hover lists: what each makes of served state.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, Member, Overview, Rebase, ReviewState, Section } from "../src/api";
import { commentEntries } from "../src/commentEntries";
import { NextState, nextAction } from "../src/nextAction";
import { focusedMember, reviewFocus } from "../src/reviewFocus";
import { changeVm, seenWhere } from "../src/changeRows";
import { chain, comment, commit, diff, shown, stack, state as fixtureState } from "./fixtures";

const file = (path: string) => ({ path, status: "M", old_path: null, insertions: 1, deletions: 0 });

const review: ReviewState = { number: 102, patch_set: 2, commit: "c".repeat(40), subject: "parser", staged: true, mode: "branch", worktree: "/repo", branch: "review/102" };

function member(number: number, extra: Partial<Member> = {}): Member {
  return { number, change_id: `I${number}`, subject: `change ${number}`, verified: 1, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: null, local_sha: null, sha: null, ...extra };
}

function reviewChain(key: string, numbers: number[]): Chain {
  const members = numbers.map((number) => member(number));
  const top = members.at(-1)!;
  return {
    key,
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

const overview = (chains: Chain[]): Overview => ({
  host: "g",
  sections: [{ name: "to-review", query: "", summary: {}, chains } as Section, { name: "outgoing", query: "", summary: {}, chains: [] } as Section],
  local: stack([]),
});

test("a staged review here narrows Review chains to its chain; without one, or with its chain not in the inbox, nothing is narrowed", () => {
  const chains = [reviewChain("a", [100]), reviewChain("b", [101, 102, 103])];
  const focus = reviewFocus(overview(chains), stack([], { staged_review: review }));
  assert.equal(focus?.chain.key, "b");
  assert.deepEqual(focusedMember(focus!).above, true, "103 is above the change reviewed");
  assert.equal(reviewFocus(overview(chains), stack([])), undefined);
  assert.equal(reviewFocus(overview([chains[0]]), stack([], { staged_review: review })), undefined);
  const top = reviewFocus(overview([reviewChain("c", [101, 102])]), stack([], { staged_review: review }));
  assert.equal(focusedMember(top!).above, false, "the top of its chain");
});

function reviewing(extra: Partial<NextState> = {}): NextState {
  return {
    stack: stack([], { staged_review: review }),
    shown: shown({ own: false }),
    chains: [],
    local: true,
    reviewing: true,
    visited: new Set(),
    files: ["a.py", "b.py"],
    seenFiles: new Set(),
    yourTurn: true,
    above: true,
    ...extra,
  };
}

// The rules of vscode/README.md, "Next: what it names, and when", one row each, in order.
test("reviewing, Next updates to Gerrit's newest patchset, then goes through the comments not read, the files not seen, the vote, then the next change or out", () => {
  const open = chain("open", [comment("Why?")], { turn: "yours" });
  const seen = { seenFiles: new Set(["a.py", "b.py"]) };
  const draft = chain("mine", [comment("Typo", { state: "draft", comment_id: "d", author: undefined })]);
  const rows: [string, Partial<NextState>, string | undefined][] = [
    ["1 Gerrit has a newer patchset than the one checked out", { chains: [open], shown: shown({ own: false, patchset_status: "outdated" }), newest: 6 }, "Update to patchset 6"],
    ["1 the newest not known yet", { shown: shown({ own: false, patchset_status: "outdated" }) }, "Update to the newest patchset"],
    ["2 an open comment yours to read, not looked at", { chains: [open] }, "Next comment"],
    ["3 files not seen, read in the combined diff in front", { chains: [open], visited: new Set(["open"]), combinedActive: true }, "Mark 2 files seen"],
    ["3 a file not seen, read in the combined diff in front", { seenFiles: new Set(["a.py"]), combinedActive: true }, "Mark 1 file seen"],
    ["4 a file of the comparison not seen", { chains: [open], visited: new Set(["open"]) }, "Next file"],
    ["5 your turn on Gerrit", seen, "Vote and publish"],
    ["5 all seen, the combined diff still in front", { ...seen, combinedActive: true }, "Vote and publish"],
    ["5 not your turn, but a draft of yours waits", { ...seen, yourTurn: false, chains: [{ ...draft, resolved: true }] }, "Vote and publish"],
    ["6 voted, the chain goes on above", { ...seen, yourTurn: false }, "Next change"],
    ["7 voted, the top of the chain", { ...seen, yourTurn: false, above: false }, "Leave review"],
    ["- nothing loaded yet", { shown: undefined }, undefined],
  ];
  for (const [rule, state, label] of rows) {
    assert.equal(nextAction(reviewing(state))?.label, label, rule);
  }
  assert.match(nextAction(reviewing(seen))?.title ?? "", /Alt\+Enter/);
});

test("reviewing a change you voted on that waits on the author, your own published comments are not what is next", () => {
  // 11-focus, 1c: 2156 waits on the author; its open chains end with your word.
  const yours = chain("asked", [comment("Rename this?", { author: "you" })], { turn: "theirs" });
  const replied = chain("replied", [comment("Why?"), comment("Because.", { author: "you" })], { turn: "theirs" });
  const voted = reviewing({ chains: [yours, replied], seenFiles: new Set(["a.py", "b.py"]), yourTurn: false });
  assert.equal(nextAction(voted)?.label, "Next change");
});

test("reworking your commit, Next goes through each conflicted file by name, Continue, what is left to answer, the attention walk, then the push", () => {
  const top = commit("top", { patchset_status: "newer", needs_edit: true });
  const needs = stack([top], { summary: { commits: 1, ready: 0, attention: 1, needs_edit: 1, not_on_gerrit: 0 } });
  const rework = (extra: Partial<NextState>): NextState => ({ ...reviewing(), stack: needs, shown: shown({ own: true }), reviewing: false, ...extra });
  const ask = chain("ask", [comment("Rename this.")], { turn: "yours" });
  const done = stack([{ ...top, needs_edit: false }], { summary: { commits: 1, ready: 1, attention: 0, needs_edit: 0, not_on_gerrit: 0 } });
  const conflict = { ...needs, rebase_in_progress: true, rebase: { stop: "conflict", actions: ["abort", "skip"], unmerged: ["a.py", "b.py"] } as unknown as Rebase };
  const second = { ...needs, rebase_in_progress: true, rebase: { stop: "conflict", actions: ["abort", "skip"], unmerged: ["b.py"] } as unknown as Rebase };
  const resolved = { ...needs, rebase_in_progress: true, rebase: { stop: "resolved", actions: ["continue", "abort", "skip"], unmerged: [] } as unknown as Rebase };
  const rows: [string, Partial<NextState>, string | undefined][] = [
    ["1 a conflict stop: the first file still conflicted, by name", { stack: conflict, chains: [ask] }, "Resolve a.py"],
    ["1 that one resolved and staged: the next one", { stack: second, chains: [ask] }, "Resolve b.py"],
    ["2 none left: Continue", { stack: resolved, chains: [ask] }, "Continue"],
    ["3 a comment left to answer, not gone to", { chains: [ask] }, "Next comment"],
    // 11-focus, 4d: gone to, a reply typed into its box but not saved: still left to answer.
    ["3 a comment gone to, not answered", { chains: [ask], visited: new Set(["ask"]) }, "Next comment"],
    ["4 a commit needs edits", {}, "Work on next"],
    ["6 nothing needs edits, new code to send", { stack: done }, "Push"],
    ["- a push would be refused", { stack: { ...done, push_blocked: { reason: "rebase", message: "x" } } }, undefined],
  ];
  for (const [rule, state, label] of rows) {
    assert.equal(nextAction(rework(state))?.label, label, rule);
  }
  // Rule 5: a rebase underway, no commit above the stop needs edits: the header's Continue, by its name.
  const stopped = stack([{ ...top, rebase: "current", needs_edit: false }], { rebase_in_progress: true, rebase: { unmerged: [] } as unknown as Rebase });
  assert.deepEqual([nextAction(rework({ stack: stopped }))?.kind, nextAction(rework({ stack: stopped }))?.label], ["continue", "Continue"]);
  // A fix saved and not amended at the stop: nothing here goes on (Continue would refuse); the stop says why.
  const unamended = stack([{ ...top, rebase: "current", needs_edit: false }], { rebase_in_progress: true, rebase: { unmerged: [], uncommitted: ["a.txt"] } as unknown as Rebase });
  assert.equal(nextAction(rework({ stack: unamended })), undefined);
  // Somebody's change you only look at: review it.
  assert.equal(nextAction({ ...rework({}), local: false, shown: shown({ own: false, pushed: true }) })?.label, "Review change");
});

test("a hover lists who said what where, and offers Reply Done on an open chain, Apply only on your own commit's suggestion", () => {
  const suggestion = comment("Use this:\n```suggestion\nx = 1\n```", {
    suggested_edits: [{ fix_id: null, description: "", in_message: true, replacements: [{ path: "src/a.py", range: { start_line: 3, start_character: 0, end_line: 3, end_character: 5 }, replacement: "x = 1\n", location: "src/a.py:3" }] }],
  });
  const [entry, closed] = commentEntries([chain("s", [suggestion], { path: "src/a.py", line: 3 }), chain("c", [comment("Nit.")], { resolved: true })], true);
  assert.deepEqual([entry.author, entry.where, entry.state, entry.tone, entry.done, entry.apply], ["Alice", "a.py:3", "to answer", "open", true, true]);
  assert.equal(entry.text, "Use this: ```suggestion", "its first two lines");
  assert.deepEqual([closed.state, closed.done, closed.apply], ["resolved", false, false]);
  assert.equal(commentEntries([chain("s", [suggestion])], false)[0].apply, false, "a change you review: nothing is written into your file");
});

test("files seen: the ticks are Gerrit's reviewed flag of the patchset looked at, else kept here; the Files list says how many", () => {
  // A staged review: its patchset. Somebody's change on Gerrit: the patchset looked at. Your own commit: here only.
  assert.deepEqual(seenWhere({ number: 7, rev: "abc", local: true }, "local", { staged: 2 }), { key: "7/2", number: 7, patchSet: 2 });
  assert.deepEqual(seenWhere({ number: 7, rev: "change:7", local: false }, "current", { current: 3 }), { key: "7/3", number: 7, patchSet: 3 });
  assert.deepEqual(seenWhere({ number: 7, rev: "change:7", local: false }, 1, { current: 3 }), { key: "7/1", number: 7, patchSet: 1 });
  assert.deepEqual(seenWhere({ number: 7, changeId: "I7", rev: "abc", local: true }, "local", {}), { key: "I7/local" });
  assert.equal(seenWhere(undefined, "local", {}), undefined);
  const two = diff({ files: [file("a.py"), file("b.py")] });
  const theirs = changeVm(fixtureState({ base: two, shown: shown({ own: false }), seen: new Set(["b.py"]) }));
  assert.equal(theirs.files?.summary, "1 of 2 files seen");
  assert.deepEqual(theirs.files?.rows.map((row) => row.seen), [false, true]);
  // Your own commit says nothing of it until you tick one.
  assert.equal(changeVm(fixtureState({ base: two, shown: shown({ own: true }) })).files?.summary, "2 files");
  // Next file goes to the first not seen, and is no longer next once all are.
  assert.equal(nextAction(reviewing({ files: ["a.py", "b.py"], seenFiles: new Set(["b.py"]) }))?.title, "1 of 2 files not seen yet (Alt+Enter)");
});
