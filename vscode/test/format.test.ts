import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, CommitStatus, EventsStatus, Overview } from "../src/api";
import {
  age,
  blockedMove,
  blockedSwaps,
  chainDescription,
  chainsWithActivity,
  commentLocation,
  commitVotes,
  eventsMayCome,
  fileState,
  movedTo,
  refreshMode,
  reviewOpened,
  rememberSeen,
  sentence,
  severity,
  sortedChains,
  swapKey,
  vote,
  votes,
} from "../src/format";

function chain(key: string, lastActivity: string, extra: Partial<Chain> = {}): Chain {
  return {
    key,
    top: { number: 1, change_id: "I1", subject: "s", url: null },
    project: "p",
    branch: "main",
    owner: { name: "alice", email: null },
    depth: 2,
    wait_age_seconds: 4 * 3600,
    unreviewed_age_seconds: 3 * 86400,
    last_activity: lastActivity,
    verified: 1,
    code_review: null,
    comments_unresolved: 2,
    attention_reasons: [],
    partial_chain: false,
    members: [],
    ...extra,
  };
}

function overview(...chains: Chain[]): Overview {
  return { host: "g", sections: [{ name: "to-review", query: "", summary: {}, chains }], local: { error: { code: "GIT", message: "" } } };
}

test("severity takes the most urgent reason", () => {
  assert.equal(severity(["awaiting-review", "ci-failed", "unresolved-comments"]), "error");
  assert.equal(severity(["awaiting-review", "unresolved-comments"]), "warning");
  assert.equal(severity(["not-pushed"]), "pending");
  assert.equal(severity([]), "ok");
});

test("votes and ages read like ger's own output", () => {
  assert.equal(vote("CR", 2), "CR+2");
  assert.equal(vote("V", -1), "V-1");
  assert.equal(vote("V", null), "V·");
  assert.deepEqual([age(59), age(12 * 60), age(47 * 3600), age(3 * 86400)], ["0m", "12m", "47h", "3d"]);
  assert.equal(chainDescription(chain("k", ""), "to-review"), "alice · 2 changes · V+1 💬2 · waiting 3d · active 4h ago");
  assert.equal(chainDescription(chain("k", ""), "outgoing"), "2 changes · V+1 💬2 · active 4h ago");
  assert.equal(votes({ verified: null, code_review: null, comments_unresolved: 0 }), "no votes");
  assert.deepEqual([fileState("A"), fileState("R"), sentence("unresolved comments")], ["added", "renamed", "Unresolved comments"]);
});

test("activity is what moved since the chain was last seen", () => {
  const first = overview(chain("a", "2026-11-09T10:00:00Z"), chain("b", "2026-11-09T10:00:00Z"));
  // First sight is not news.
  const seen = rememberSeen(first, {});
  assert.equal(chainsWithActivity(first, seen).size, 0);

  const later = overview(chain("a", "2026-11-09T12:00:00Z"), chain("b", "2026-11-09T10:00:00Z"));
  const kept = rememberSeen(later, seen);
  assert.deepEqual([...chainsWithActivity(later, kept)], ["a"]);
  // Looking at the chain clears it; a chain that left the inbox is forgotten.
  const marked = rememberSeen(later, kept, ["a"]);
  assert.equal(chainsWithActivity(later, marked).size, 0);
  assert.deepEqual(Object.keys(rememberSeen(overview(chain("b", "x")), marked)), ["b"]);
});

test("a new comment's location is the file, a line or a range, lines from 1", () => {
  assert.equal(commentLocation("a.py"), "a.py");
  assert.equal(commentLocation("a.py", { start: 4, end: 4 }), "a.py:4");
  assert.equal(commentLocation("a.py", { start: 7, end: 4 }), "a.py:4-7");
});

test("dragged commits land together in the gap the line showed, in the order they had", () => {
  // As shown: newest first. Gap 0 is above the top row, 4 under the last.
  const shown = ["d", "c", "b", "a"];
  assert.deepEqual(movedTo(shown, ["b"], 0), ["b", "d", "c", "a"], "one commit to the top");
  assert.deepEqual(movedTo(shown, ["d"], 4), ["c", "b", "a", "d"], "under the last row: the oldest");
  assert.deepEqual(movedTo(shown, ["c"], 3), ["d", "b", "c", "a"], "one place down");
  assert.deepEqual(movedTo(shown, ["a", "d"], 2), ["c", "d", "a", "b"], "two that were apart land together, newest first");
  assert.deepEqual(movedTo(shown, ["c", "b"], 4), ["d", "a", "c", "b"], "a block moves as a block");
  // Next to itself nothing moves: both gaps around a commit, and any gap inside a selected block.
  assert.equal(movedTo(shown, ["c"], 1), undefined);
  assert.equal(movedTo(shown, ["c"], 2), undefined);
  assert.equal(movedTo(shown, ["c", "b"], 2), undefined);
  assert.equal(movedTo(shown, ["x"], 0), undefined, "not a commit of the stack");
  assert.equal(movedTo(shown, ["c"], 5), undefined);
});

test("chains sort by last activity either way, or stay as the service ordered them", () => {
  const chains = [chain("urgent", "2026-11-02T00:00:00Z"), chain("old", "2026-11-01T00:00:00Z"), chain("new", "2026-11-03T00:00:00Z")];
  const keys = (sort: Parameters<typeof sortedChains>[1]) => sortedChains(chains, sort).map((c) => c.key);
  assert.deepEqual(keys("state"), ["urgent", "old", "new"]);
  assert.deepEqual(keys("newest"), ["new", "urgent", "old"]);
  assert.deepEqual(keys("oldest"), ["old", "urgent", "new"]);
  assert.deepEqual(chains.map((c) => c.key), ["urgent", "old", "new"], "the overview itself is not reordered");
});

test("the conflict preview blocks only the swaps that would conflict", () => {
  const conflict = { sha: "c", summary: "Rework d.txt", files: ["d.txt"] };
  const blocked = blockedSwaps({
    head: "c",
    available: true,
    swaps: [
      { lower: "a", upper: "b", conflict: null },
      { lower: "b", upper: "c", conflict },
    ],
  });
  assert.equal(blocked.get(swapKey("a", "b")), undefined);
  assert.equal(blocked.get(swapKey("b", "c")), conflict);
  assert.equal(blockedSwaps(undefined).size, 0);
  assert.equal(blockedMove("Add d.txt", "Rework d.txt", conflict), '"Add d.txt" cannot change places with "Rework d.txt": "Rework d.txt" would conflict in d.txt.');
});

test("a commit whose code moved on shows the pushed votes as last pushed; a rebased one its own", () => {
  const base = { verified: null, code_review: null, comments_unresolved: 2, last_pushed_votes: { verified: -1, code_review: 2 } };
  assert.equal(commitVotes(base as unknown as CommitStatus), "last pushed V-1 CR+2 💬2");
  const rebased = { ...base, verified: -1, code_review: 2, last_pushed_votes: null };
  assert.equal(commitVotes(rebased as unknown as CommitStatus), "V-1 CR+2 💬2");
  const never = { ...base, last_pushed_votes: { verified: null, code_review: null } };
  assert.equal(commitVotes(never as unknown as CommitStatus), "💬2");
});

test("the tooltip says whether events or the poll keep the views current, and why", () => {
  const status = (state: EventsStatus["state"], reason: string | null = null): EventsStatus => ({ state, reason, source: "ssh://me@g:29418" });
  const local = " Commits, checkouts and rebases here refresh them at once.";
  assert.equal(
    refreshMode(status("live"), 900),
    `Following Gerrit's event stream (ssh://me@g:29418): review activity refreshes the views, and a poll every 15m is the safety net.${local}`,
  );
  assert.equal(refreshMode(undefined, 120), `Refreshing every 2m.${local}`);
  assert.equal(refreshMode(status("connecting"), 120), `Refreshing every 2m.${local}`);
  assert.equal(refreshMode(status("unavailable", "Permission denied (publickey)."), 120), `Refreshing every 2m; no Gerrit event stream: Permission denied (publickey).${local}`);
  assert.equal(refreshMode(status("retrying", "Connection closed"), 120), `Refreshing every 2m while Gerrit's event stream is down: Connection closed.${local}`);
  assert.deepEqual(
    (["off", "connecting", "live", "retrying", "unavailable"] as const).map((state) => eventsMayCome(status(state))),
    [false, true, true, true, false],
  );
  assert.equal(eventsMayCome(undefined), false);
});

test("a review checkout says where the change is: the review worktree, or this checkout switched", () => {
  const review = { number: 900, patch_set: 2, branch: "change-900", worktree: "/r-review", staged: true };
  assert.equal(reviewOpened(review, false), "Change 900 (patchset 2) is checked out for review on change-900 in /r-review, with its changes staged.");
  assert.equal(
    reviewOpened({ ...review, staged: false }, true),
    "Change 900 (patchset 2) is checked out for review: this checkout was switched to change-900. Leave Review Checkout switches back.",
  );
});
