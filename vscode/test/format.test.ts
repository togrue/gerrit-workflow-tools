import assert from "node:assert/strict";
import { test } from "node:test";

import { Chain, CommitStatus, EventsStatus, Overview } from "../src/api";
import {
  age,
  attentionIcon,
  blockedMove,
  blockedSwaps,
  chainDescription,
  chainsWithActivity,
  commentLocation,
  codeFence,
  commentPlainText,
  commentText,
  commitVotes,
  eventsMayCome,
  fileState,
  headlineIcon,
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
  assert.deepEqual(attentionIcon(["review-issues"]), { icon: "warning", severity: "warning" });
  assert.deepEqual(attentionIcon(["review-issues", "unresolved-comments"]), { icon: "comment-unresolved", severity: "warning" });
  assert.deepEqual(attentionIcon(["ci-failed", "unresolved-comments"]), { icon: "error", severity: "error" });
});

// A4: the status bar drew a comment bubble for "Negative vote on 123", which has no comment.
test("the status bar's headline icon is the one its reason gets in Review chains", () => {
  const outgoing = (reason: string) => headlineIcon({ section: "outgoing", reason });
  assert.equal(outgoing("review-issues"), "warning");
  assert.notEqual(outgoing("review-issues"), "comment-unresolved");
  assert.equal(outgoing("unresolved-comments"), "comment-unresolved");
  assert.equal(outgoing("ci-failed"), "error");
  for (const reason of ["ci-failed", "unresolved-comments", "review-issues"]) {
    assert.equal(outgoing(reason), attentionIcon([reason]).icon, reason);
  }
  assert.equal(headlineIcon({ section: "to-review", reason: "waiting-on-you" }), "eye");
  assert.equal(headlineIcon(null), "pass");
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

test("a comment's text shows the Gerrit fix it suggests, and a suggestion block of its text only once", () => {
  const range = { start_line: 2, start_character: 0, end_line: 4, end_character: 0 };
  const fix = {
    fix_id: "a6b3c2d1_0e9f8a7b",
    description: "Use the LIMIT constant",
    in_message: false,
    replacements: [{ path: "epsilon.txt", range, replacement: "LIMIT = 10\nuse(LIMIT)\n", location: "epsilon.txt:2-3" }],
  };
  assert.equal(
    commentText({ message: "Name the limit.", suggested_edits: [fix] }),
    "Name the limit.\n\n**Suggested fix:** Use the LIMIT constant\n`epsilon.txt:2-3` becomes:\n```\nLIMIT = 10\nuse(LIMIT)\n```",
  );
  const deletion = { ...fix, description: "", replacements: [{ ...fix.replacements[0], replacement: "" }] };
  assert.equal(commentText({ message: "", suggested_edits: [deletion] }), "**Suggested fix:**\n`epsilon.txt:2-3`: delete");
  const block = "Typo:\n\n```suggestion\n    return total\n```\n";
  const inMessage = { ...fix, fix_id: null, in_message: true };
  assert.equal(commentText({ message: block, suggested_edits: [inMessage] }), block);
  assert.equal(commentText({ message: "plain" }), "plain");
});

test("a fix is fenced longer than any backtick run in it, so a ``` line of the code cannot close the fence", () => {
  assert.equal(codeFence(["x = 1"]), "```");
  assert.equal(codeFence(["```", "x = `y`", "```"]), "````");
  const range = { start_line: 2, start_character: 0, end_line: 3, end_character: 0 };
  const fix = { fix_id: "f1", description: "", in_message: false, replacements: [{ path: "a.md", range, replacement: "```\ncode\n```\n", location: "a.md:2" }] };
  assert.equal(commentText({ message: "", suggested_edits: [fix] }), "**Suggested fix:**\n`a.md:2` becomes:\n````\n```\ncode\n```\n````");
});

test("the Change view and tooltips get a comment's fixes as plain text, an insertion said as one", () => {
  const range = { start_line: 2, start_character: 0, end_line: 4, end_character: 0 };
  const at = { start_line: 5, start_character: 2, end_line: 5, end_character: 2 };
  const fix = {
    fix_id: "a6b3c2d1_0e9f8a7b",
    description: "Use the LIMIT constant",
    in_message: false,
    replacements: [
      { path: "epsilon.txt", range, replacement: "LIMIT = 10\r\n    use(LIMIT)\r\n", location: "epsilon.txt:2-3" },
      { path: "epsilon.txt", range: at, replacement: "x", location: "epsilon.txt:5.3" },
      { path: "epsilon.txt", range, replacement: "", location: "epsilon.txt:2-3" },
    ],
  };
  assert.equal(
    commentPlainText({ message: "Name the limit.", suggested_edits: [fix] }),
    "Name the limit.\n\nsuggested fix: Use the LIMIT constant\n  epsilon.txt:2-3 becomes:\n  + LIMIT = 10\n  +     use(LIMIT)\n  insert at epsilon.txt:5.3:\n  + x\n  epsilon.txt:2-3: delete",
  );
  assert.equal(commentPlainText({ message: "plain" }), "plain");
});
