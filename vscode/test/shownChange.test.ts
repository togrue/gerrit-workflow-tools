// Which change the Change view shows, and why (shownChange.ts): the review's change wherever the
// review moved from, the rebase's stop, your selection through rewrites; the "not in the local stack
// any more" sentence only when that is what happened (states 6, 11, 21, 25 of 20-states-after).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReviewState } from "../src/api";
import type { ChangeTarget } from "../src/model";
import { Followed, LEFT_THE_STACK, STACK_UNREADABLE, follow, reviewTarget } from "../src/shownChange";
import { commit, stack } from "./fixtures";

const review = (number: number, sha: string, extra: Partial<ReviewState> = {}): ReviewState => ({
  number,
  change_id: `I${number}`,
  patch_set: 2,
  commit: sha,
  subject: `change ${number}`,
  staged: true,
  mode: "branch",
  worktree: "/repo",
  branch: `change-${number}`,
  ...extra,
});

const seen = (target: ChangeTarget | undefined, s: ReturnType<typeof stack> | undefined, extra: { holding?: boolean; stackError?: boolean; shownChangeId?: string } = {}) => ({
  target,
  shownChangeId: extra.shownChangeId,
  stack: s,
  stackError: extra.stackError ?? false,
  holding: extra.holding ?? false,
});

const abandoned = commit("geometry: add helpers", { number: 2303, change_id: "I2303" });
const next = commit("tests: cover geometry", { number: 2304, change_id: "I2304" });

test("state 11: the review moved on from an abandoned change: its new change is shown, with its own Change-Id", () => {
  const before: Followed = { review: { number: 2303, patch_set: 2, commit: abandoned.sha } };
  const shownBefore = reviewTarget(review(2303, abandoned.sha), stack([abandoned]));
  // The step's /stack answered: the review holds 2304, whose parent (the abandoned change) is HEAD.
  const now = stack([abandoned, next], { staged_review: review(2304, next.sha) });
  const { decision, after } = follow(before, seen(shownBefore, now));
  assert.equal(decision.kind, "review");
  assert.equal(after.review?.number, 2304);
  // The target the review's change is opened as never borrows the Change-Id of the change shown before.
  const target = reviewTarget(review(2304, next.sha, { change_id: "I2304" }), stack([abandoned]));
  assert.deepEqual([target.number, target.changeId, target.rev, target.local], [2304, "I2304", next.sha, true]);
});

test("state 11: once the review's change is shown, its parent in the stack is no reason to show another", () => {
  const now = stack([abandoned, next], { staged_review: review(2304, next.sha) });
  const target = reviewTarget(review(2304, next.sha), now);
  const { decision } = follow({ review: { number: 2304, patch_set: 2, commit: next.sha } }, seen(target, now, { shownChangeId: "I2304" }));
  assert.equal(decision.kind, "none");
});

test("state 25: a review started in a shell is followed: the header's change is the body's", () => {
  const left = reviewTarget(review(2137, "a".repeat(40)), undefined);
  const now = stack([next], { staged_review: review(2142, next.sha) });
  assert.equal(follow({}, seen(left, now)).decision.kind, "review");
});

test("a step started from the views shows its own result: nothing follows the review meanwhile", () => {
  const now = stack([next], { staged_review: review(2304, next.sha) });
  const { decision, after } = follow({ review: { number: 2303, patch_set: 2, commit: abandoned.sha } }, seen(undefined, now, { holding: true }));
  assert.equal(decision.kind, "none");
  assert.equal(after.review?.number, 2304, "and the review it moved to is not followed again after it");
  assert.equal(follow(after, seen(undefined, now)).decision.kind, "none");
});

test("state 6: after Leave Review the change shown is its Gerrit page, not \"not in the local stack any more\"", () => {
  const target = reviewTarget(review(2145, next.sha), stack([next]));
  const { decision } = follow({ review: { number: 2145, patch_set: 2, commit: next.sha } }, seen(target, stack([])));
  assert.equal(decision.kind, "open");
  if (decision.kind === "open") {
    assert.deepEqual([decision.target.rev, decision.target.local, decision.target.number], ["change:2145", false, 2145]);
  }
});

test("a commit of your stack that left it is said to have left, and shown again once it is back", () => {
  const mine = commit("stats: add mode()", { number: 2097, change_id: "I2097" });
  const target: ChangeTarget = { rev: mine.sha, title: mine.summary, local: true, changeId: mine.change_id, number: 2097 };
  const gone = follow({}, seen(target, stack([commit("other")])));
  assert.deepEqual(gone.decision, { kind: "close", why: LEFT_THE_STACK(mine.summary) });
  const amended = { ...mine, sha: "b".repeat(40) };
  const back = follow(gone.after, seen(undefined, stack([amended])));
  assert.equal(back.decision.kind, "open");
  assert.equal(back.after.lost, undefined);
});

test("state 21: while the stack cannot be read the commit is not said to have left it, and comes back with it", () => {
  const mine = commit("stats: add mode()", { number: 2097, change_id: "I2097" });
  const target: ChangeTarget = { rev: mine.sha, title: mine.summary, local: true, changeId: mine.change_id, number: 2097 };
  const unreadable = follow({}, seen(target, undefined, { stackError: true }));
  assert.deepEqual(unreadable.decision, { kind: "close", why: STACK_UNREADABLE(mine.summary) });
  const back = follow(unreadable.after, seen(undefined, stack([mine])));
  assert.equal(back.decision.kind, "open", "Local stack lists it again: so does the Change view");
});

test("a rebase that stops somewhere new is followed there; staying at one stop leaves the view to you", () => {
  const [a, b] = [commit("A", { rebase: "current" }), commit("B", { rebase: "todo" })];
  const rebasing = stack([a, b], { rebase_in_progress: true, rebase: { current: a.sha } as never });
  const first = follow({}, seen(undefined, rebasing));
  assert.equal(first.decision.kind, "stop");
  const target: ChangeTarget = { rev: b.sha, title: "B", local: true, changeId: b.change_id };
  assert.equal(follow(first.after, seen(target, rebasing)).decision.kind, "none");
});
