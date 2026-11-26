// Leaving a review says where you are now (JUDGEMENT-01-12 S6).

import assert from "node:assert/strict";
import { test } from "node:test";

import { leftReview } from "../src/format";

test("leaving a review here says which branch this checkout is back on", () => {
  assert.equal(leftReview({ done: 2145, deleted: ["change-2145"], kept: [], branch: "main", worktree: "/repo", mode: "branch" }, "/repo"), "Left the review of change 2145: this checkout is back on main.");
});

test("leaving a review in the review worktree says the worktree is free", () => {
  assert.equal(leftReview({ done: 2137, deleted: [], kept: [], branch: null, worktree: "/repo-review", mode: "worktree" }, "/repo"), "Left the review of change 2137: the review worktree is free for the next review.");
});
