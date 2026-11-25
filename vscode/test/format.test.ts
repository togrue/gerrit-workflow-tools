import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { Chain, CommitStatus, EventsStatus, Headline, Overview, Rebase, RebaseHeadline, RebaseStop } from "../src/api";
import {
  age,
  attentionIcon,
  blockedMove,
  blockedSwaps,
  chainDescription,
  chainsWithActivity,
  commentLocation,
  commitVotes,
  eventsMayCome,
  fileState,
  headlineIcon,
  markdownText,
  movedTo,
  rebaseItem,
  refreshMode,
  reviewOpened,
  rememberSeen,
  sentence,
  sortedChains,
  swapKey,
  vote,
  votes,
  WORDED_REASONS,
  headlineNews,
} from "../src/format";
import { attended } from "./fixtures";

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
    partial_chain: false,
    members: [],
    ...extra,
    ...attended(extra.attention_reasons ?? []),
  };
}

function overview(...chains: Chain[]): Overview {
  return { host: "g", sections: [{ name: "to-review", query: "", summary: {}, chains }], local: { error: { code: "GIT", message: "" } } };
}

test("the icon follows the severity core gave; unresolved comments draw the comment bubble only as a warning", () => {
  assert.deepEqual(attentionIcon(attended(["review-issues"])), { icon: "warning", severity: "warning" });
  assert.deepEqual(attentionIcon(attended(["review-issues", "unresolved-comments"])), { icon: "comment-unresolved", severity: "warning" });
  assert.deepEqual(attentionIcon(attended(["ci-failed", "unresolved-comments"])), { icon: "error", severity: "error" });
  assert.deepEqual(attentionIcon(attended(["not-pushed"])), { icon: "clock", severity: "pending" });
  assert.deepEqual(attentionIcon(attended([])), { icon: "pass", severity: "ok" });
  // Somebody's change that waits on your review wears the eye To review gives it; worse news wins.
  assert.deepEqual(attentionIcon(attended(["waiting-on-you"])), { icon: "eye", severity: "pending" });
  assert.deepEqual(attentionIcon(attended(["waiting-on-you", "ci-failed"])), { icon: "error", severity: "error" });
});

test("every attention reason core names has words", () => {
  const core = Object.keys(JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "test", "attention.json"), "utf8")));
  assert.deepEqual([...WORDED_REASONS].sort(), core.sort());
});

// A4: the status bar drew a comment bubble for "Negative vote on 123", which has no comment.
test("the status bar's headline icon is the one its reason gets in Review chains", () => {
  const outgoing = (reason: string) => headlineIcon({ reason, severity: attended([reason]).severity });
  assert.equal(outgoing("review-issues"), "warning");
  assert.notEqual(outgoing("review-issues"), "comment-unresolved");
  assert.equal(outgoing("unresolved-comments"), "comment-unresolved");
  assert.equal(outgoing("ci-failed"), "error");
  for (const reason of ["ci-failed", "unresolved-comments", "review-issues"]) {
    assert.equal(outgoing(reason), attentionIcon(attended([reason])).icon, reason);
  }
  assert.equal(headlineIcon({ reason: "waiting-on-you", severity: "pending" }), "eye");
  assert.equal(headlineIcon(null), "pass");
});

// #62: during a rebase the status bar kept the headline; core now makes the rebase the headline.
test("the status bar says where a rebase stopped, by the change number or else the subject, and what is next", () => {
  const at = (stop: RebaseStop | null, number: number | null, subject: string | null = "Cleanup after experiment"): RebaseHeadline => ({
    section: "local",
    reason: "rebase",
    severity: stop === "conflict" ? "error" : "warning",
    stop,
    chain: null,
    number,
    subject,
    sha: subject ? "a".repeat(40) : null,
    url: null,
    text: "core's sentence",
  });
  const text = (headline: RebaseHeadline) => rebaseItem(headline).text;
  assert.equal(text(at("edit", 101)), "Rebase stopped at 101");
  assert.equal(text(at("split", 101)), "Rebase stopped to split 101");
  assert.equal(text(at("conflict", 103)), "Conflict in 103");
  // A fixup stopped folding: core names the commit it folds into, and says so.
  assert.equal(text({ ...at("conflict", 2168), folding: true }), "Conflict in 2168, folding a fixup into it");
  assert.equal(text(at("resolved", 103)), "Conflict resolved in 103");
  assert.equal(text(at("paused", null, null)), "Rebase paused");
  assert.equal(text(at(null, null, null)), "Rebase underway");
  // Not on Gerrit: its subject, cut short.
  // Not on Gerrit: its subject, cut short and quoted, so it does not read as a file.
  assert.equal(text(at("edit", null)), 'Rebase stopped at "Cleanup after experiment"');
  assert.equal(text(at("conflict", null, "A subject far too long for the status bar")), 'Conflict in "A subject far too long for th…"');
  // No commit to name: a split whose commit was reset away.
  assert.equal(text(at("split", null, null)), "Rebase stopped");

  // What to do next, with the conflicted files.
  const rebase = (unmerged: string[]): Rebase => ({ branch: "feature", onto: null, orig_head: null, stop: "conflict", applied: false, current: null, unmerged, as_before: false, actions: ["abort", "skip"], refused: { continue: "the index has unresolved conflicts" } });
  assert.match(rebaseItem(at("edit", 101)).next, /amend .*Continue in Local stack/);
  assert.match(rebaseItem(at("conflict", 103), rebase(["d.txt"])).next, /^Resolve the conflicts in d\.txt and git add/);
  assert.match(rebaseItem(at("conflict", 103), rebase(["d.txt", "e.txt"])).next, /conflicts in 2 files .*Abort/);
  assert.match(rebaseItem(at("resolved", 103)).next, /resolved: Continue in Local stack/);
  for (const stop of ["edit", "split", "conflict", "resolved", "paused", null] as const) {
    assert.match(rebaseItem(at(stop, 101)).next, /Continue in Local stack/, String(stop));
  }
  // A resolution committed by hand under the apply backend: core serves no stop, refuses Continue
  // and offers Skip; the status bar says what core says, not "git rebase --continue".
  const byHand: Rebase = {
    ...rebase([]),
    stop: null,
    refused: { continue: "the commit it stopped on was committed by hand, so nothing is left to apply and `git rebase --continue` would stop again: `git rebase --skip` goes on from HEAD, or `git rebase --abort`" },
  };
  assert.equal(
    rebaseItem(at(null, 101), byHand).next,
    "The commit it stopped on was committed by hand, so nothing is left to apply and `git rebase --continue` would stop again: `git rebase --skip` goes on from HEAD, or `git rebase --abort`.",
  );
  // Where core allows Continue, its refusals of other ways on change nothing.
  assert.match(rebaseItem(at(null, 101), { ...byHand, actions: ["continue", "abort"], refused: { skip: "no" } }).next, /^Finish it with Continue in Local stack/);
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

test("a rebase is never news, and the headline from before it is not news when it ends", () => {
  const ci: Headline = { section: "outgoing", reason: "ci-failed", severity: "error", chain: "k", number: 102, subject: "s", url: null, text: "CI failed" };
  const rebase = { section: "local", reason: "rebase", severity: "warning", stop: "edit", chain: null, number: 103, subject: "t", sha: null, url: null, text: "r" } as Headline;
  let state = headlineNews(undefined, ci, false);
  assert.deepEqual(state, { news: false, last: "ci-failed:102" }, "first sight is not news");
  state = headlineNews(state.last, rebase, true);
  assert.deepEqual(state, { news: false, last: "ci-failed:102" }, "a rebase is yours, and not remembered");
  state = headlineNews(state.last, ci, true);
  assert.equal(state.news, false, "the CI failure from before the rebase is not news again");
  const other: Headline = { ...ci, number: 104 };
  assert.equal(headlineNews(state.last, other, true).news, true, "a new headline in a background load is news");
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

test("your own push or vote is not news, and counts as seen", () => {
  const first = overview(chain("a", "2026-11-09T10:00:00Z"));
  const seen = rememberSeen(first, {});
  const pushed = overview({ ...chain("a", "2026-11-09T12:00:00Z"), last_activity_yours: true });
  const after = rememberSeen(pushed, seen);
  assert.equal(chainsWithActivity(pushed, after).size, 0, "your push rings no bell");
  const reviewed = overview(chain("a", "2026-11-09T13:00:00Z"));
  assert.deepEqual([...chainsWithActivity(reviewed, rememberSeen(reviewed, after))], ["a"], "a reviewer after it does");
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

test("text in a Markdown tooltip shows as it is: no &nbsp; between its words, no markup or icon read into it", () => {
  const refusal = "ger.webUrl must use https:// (got 'http://g:8081'): opt in with `git config ger.allowInsecureHttp true`";
  const shown = markdownText(refusal);
  assert.ok(!shown.includes("&nbsp;"));
  // Unescaping every backslash-escaped character gives the text back.
  assert.equal(shown.replace(/\\(.)/g, "$1"), refusal);
  assert.equal(markdownText("$(warning) *a* [b](c) <d> & e"), "\\$\\(warning\\) \\*a\\* \\[b\\]\\(c\\) \\<d\\> \\& e");
  assert.equal(markdownText("one\ntwo"), "one\n\ntwo");
});
