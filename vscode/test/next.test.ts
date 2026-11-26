// Next, the review focus and the comments a hover lists: what each makes of served state.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, Member, Overview, ReviewState, Section } from "../src/api";
import { commentEntries } from "../src/commentEntries";
import { NextState, nextAction } from "../src/nextAction";
import { focusedMember, reviewFocus } from "../src/reviewFocus";
import { chain, comment, commit, shown, stack } from "./fixtures";

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

test("reviewing, Next goes through the comments not read, the files not opened, the vote, then the next change or out", () => {
  const open = chain("open", [comment("Why?")]);
  assert.equal(nextAction(reviewing({ chains: [open] }))?.label, "Next comment");
  assert.equal(nextAction(reviewing({ chains: [open], visited: new Set(["open"]) }))?.label, "Next file");
  const seen = { seenFiles: new Set(["a.py", "b.py"]) };
  assert.equal(nextAction(reviewing(seen))?.label, "Vote and publish");
  assert.match(nextAction(reviewing(seen))?.title ?? "", /Alt\+Enter/);
  // Voted, nothing of yours left unpublished: on along the chain, out at its top.
  assert.equal(nextAction(reviewing({ ...seen, yourTurn: false }))?.label, "Next change");
  assert.equal(nextAction(reviewing({ ...seen, yourTurn: false, above: false }))?.label, "Leave review");
  // A draft of yours still waits: the vote publishes it, whoever's turn it is.
  const draft = chain("mine", [comment("Typo", { state: "draft", comment_id: "d", author: undefined })]);
  assert.equal(nextAction(reviewing({ ...seen, yourTurn: false, chains: [{ ...draft, resolved: true }] }))?.kind, "vote");
  // Nothing loaded yet: no Next to press.
  assert.equal(nextAction(reviewing({ shown: undefined })), undefined);
});

test("reworking your commit, Next goes through what is left to answer, then the attention walk, then the push", () => {
  const top = commit("top", { patchset_status: "newer", needs_edit: true });
  const needs = stack([top], { summary: { commits: 1, ready: 0, attention: 1, needs_edit: 1, not_on_gerrit: 0 } });
  const rework = (extra: Partial<NextState>): NextState => ({ ...reviewing(), stack: needs, shown: shown({ own: true }), reviewing: false, ...extra });
  const ask = chain("ask", [comment("Rename this.")], { turn: "yours" });
  assert.equal(nextAction(rework({ chains: [ask] }))?.label, "Next comment");
  assert.equal(nextAction(rework({ chains: [ask], visited: new Set(["ask"]) }))?.label, "Work on next");
  const done = stack([{ ...top, needs_edit: false }], { summary: { commits: 1, ready: 1, attention: 0, needs_edit: 0, not_on_gerrit: 0 } });
  assert.equal(nextAction(rework({ stack: done }))?.label, "Push");
  assert.equal(nextAction(rework({ stack: { ...done, push_blocked: { reason: "rebase", message: "x" } } })), undefined, "a push would be refused: no Push offered");
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
