import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, CommitStatus, Overview } from "../src/api";
import {
  age,
  blockedMove,
  blockedSwaps,
  chainDescription,
  chainsWithActivity,
  commentLocation,
  commitVotes,
  droppedOn,
  fileState,
  moved,
  rememberSeen,
  sentence,
  severity,
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

test("moving a commit keeps every commit exactly once", () => {
  assert.deepEqual(moved(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moved(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
});

test("a new comment's location is the file, a line or a range, lines from 1", () => {
  assert.equal(commentLocation("a.py"), "a.py");
  assert.equal(commentLocation("a.py", { start: 4, end: 4 }), "a.py:4");
  assert.equal(commentLocation("a.py", { start: 7, end: 4 }), "a.py:4-7");
});

test("a dropped commit takes the place of the one it lands on, in either direction", () => {
  // Oldest first; the view shows d c b a.
  const stack = ["a", "b", "c", "d"];
  assert.deepEqual(droppedOn(stack, "c", "b"), ["a", "c", "b", "d"], "onto the neighbour below: the two swap");
  assert.deepEqual(droppedOn(stack, "b", "c"), ["a", "c", "b", "d"], "onto the neighbour above: the two swap");
  assert.deepEqual(droppedOn(stack, "d", "a"), ["d", "a", "b", "c"], "newest to the very bottom");
  assert.deepEqual(droppedOn(stack, "a", "d"), ["b", "c", "d", "a"], "oldest to the very top");
  assert.deepEqual(droppedOn(stack, "c", undefined), ["c", "a", "b", "d"], "empty space under the list is the bottom");
  assert.equal(droppedOn(stack, "c", "c"), undefined);
  assert.equal(droppedOn(stack, "x", "c"), undefined);
  assert.equal(moved(["a", "b", "c"], 2, 1), undefined);
  assert.equal(moved(["a", "b", "c"], 0, -1), undefined);
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
