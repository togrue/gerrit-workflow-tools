// The view models of the two webview views: what a row says about a commit (stackRows.ts) and
// what the Change view offers for a change (changeRows.ts). The pages only draw these.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CommentChain, Rebase, RebaseAction, StackCommit } from "../src/api";
import { ChangeState, READ_ONLY, answeredByDraft, changeHeading, changeLines, changeVm, replyName, toAnswer } from "../src/changeRows";
import { fitSides, modeSides, modesFor } from "../src/compareRows";
import { attentionIcon, reasonWords, votes } from "../src/format";
import { commentState } from "../src/commentState";
import { REBASE_WORDS, SYNC_ICONS, SYNC_WORDS, StackVm, workOnNext, badges, frozen, pinned, pinnedWhy, pushWaysOn, rebaseNotNow, rebaseNotNowFresh, reviewerWords, skipQuestion, stackLines, stoppedAt, sync, stackVm, stackWhere } from "../src/stackRows";
import { waitingFor } from "../src/state";
import { attended, chain, comment, commit, content, diff, finding, never, patchsets, shown, stack, state } from "./fixtures";

test("the icon says how a commit relates to Gerrit", () => {
  const states = (extra: Partial<StackCommit>) => sync(commit("c", extra));
  assert.equal(states({ patchset_status: "active" }), "same");
  assert.equal(states({ patchset_status: "newer", code_change: "rebased" }), "rebased");
  assert.equal(states({ patchset_status: "newer", code_change: "changed" }), "code-changed");
  assert.equal(states({ patchset_status: "newer", code_change: "unknown" }), "ahead");
  assert.equal(states({ patchset_status: "newer", code_change: "changed", diverged: true }), "diverged");
  assert.equal(states({ patchset_status: "newer", diverged: true }), "diverged");
  assert.equal(states({ patchset_status: "outdated" }), "outdated");
  assert.equal(states({ patchset_status: "absent", pushed: false }), "not-pushed");
  assert.equal(states({ patchset_status: "merged-same" }), "merged");
  assert.equal(states({ patchset_status: "unknown" }), "unknown");
  // A local commit is no cloud: every state has its own icon, and none is one.
  const icons = Object.values(SYNC_ICONS);
  assert.equal(new Set(icons).size, icons.length);
  assert.ok(icons.every((name) => !name.includes("cloud")));
});

test("a row carries comments, the review vote and the CI vote as badges, in that order", () => {
  const [comments, review, ci] = badges(commit("c", { comments_unresolved: 2, code_review: -1, verified: 1 }));
  assert.deepEqual([comments?.text, comments?.tone], ["2", "warn"]);
  assert.deepEqual([review?.text, review?.tone, review?.icon], ["-1", "bad", "thumbsdown"]);
  assert.deepEqual([ci?.tone, ci?.icon, ci?.title], ["good", "pass", "Verified +1"]);
  assert.deepEqual(badges(commit("c")), [null, null, null], "nothing to say: no badge, the slot stays");
  assert.equal(badges(commit("c", { ci_failures: ["build"] }))[2]?.title, "CI failed: build");
  // New code since the push: the votes are the last pushed patchset's, shown as that.
  const stale = badges(commit("c", { last_pushed_votes: { verified: -1, code_review: 2 } }));
  assert.deepEqual([stale[1]?.text, stale[1]?.stale, stale[2]?.tone, stale[2]?.stale], ["+2", true, "bad", true]);
  assert.match(stale[1]?.title ?? "", /last pushed patchset/);
});

test("the review slot says when nobody is assigned, and the vote names who is on the change", () => {
  const nobody = badges(commit("c", { attention_reasons: ["no-reviewers"] }))[1];
  assert.deepEqual([nobody?.kind, nobody?.icon, nobody?.tone, nobody?.text], ["review", "person-add", "warn", ""]);
  assert.match(nobody?.title ?? "", /No reviewers assigned/);
  assert.equal(badges(commit("c", { reviewers: [{ name: "alice", cc: false }] }))[1], null, "assigned, no vote yet: nothing to say");
  const reviewers = [
    { name: "alice", cc: false },
    { name: "bob", cc: false },
    { name: "carol", cc: true },
  ];
  const voted = badges(commit("c", { code_review: 2, reviewers }))[1];
  assert.equal(voted?.title, "Code-Review +2\nReviewers: alice, bob (cc: carol)");
  assert.equal(reviewerWords([{ name: "carol", cc: true }]), "Reviewers: none (cc: carol)");
  assert.equal(reviewerWords(undefined), "", "a source that does not say lists nobody");
  const vm = stackVm(stack([commit("One", { reviewers })]), undefined, never);
  assert.match(vm.rows[0].tooltip, /^Reviewers: alice, bob \(cc: carol\)$/m, "the row's tooltip names them too");
});

test("the stack shows newest first, with HEAD and what push would hold back", () => {
  const vm = stackVm(stack([commit("One"), commit("Two", { held_back: true }), commit("Three", { is_head: true, patchset_status: "absent", pushed: false, change_id: null })]), undefined, never);
  assert.deepEqual(vm.rows.map((row) => row.summary), ["Three", "Two", "One"]);
  assert.deepEqual(vm.rows[0].notes, ["HEAD", "three00"]);
  assert.equal(vm.rows[0].key, vm.rows[0].sha, "without a Change-Id the commit id names the row");
  assert.deepEqual(vm.rows.map((row) => row.squash), [true, true, false], "the oldest has nothing below to squash into");
  assert.equal(vm.frozen, null);
  assert.deepEqual(stackLines(vm), ["Three — HEAD · three00 · not on Gerrit", "Two — two0000 · held back · the same as on Gerrit", "One — one0000 · the same as on Gerrit"]);
});

test("ahead of Gerrit, a row tells new code from only rebased, by core's verdict", () => {
  const vm = stackVm(
    stack([
      commit("Rebased", { patchset_status: "newer", code_change: "rebased", code_review: 2 }),
      commit("New code", { patchset_status: "newer", code_change: "changed", number: 7, last_pushed_votes: { verified: null, code_review: 2 } }),
    ]),
    undefined,
    never,
  );
  const [changed, rebased] = vm.rows;
  assert.notEqual(changed.syncIcon, rebased.syncIcon, "two icons");
  assert.deepEqual([changed.syncIcon, rebased.syncIcon], ["diff-modified", "arrow-circle-up"]);
  assert.deepEqual(changed.notes, ["newcode", "code changed"], "the note sits by the id, where held back would");
  assert.deepEqual(rebased.notes, ["rebased"], "only rebased needs no note: its icon and tooltip say it");
  assert.match(changed.tooltip, /^Change 7: its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push$/m);
  assert.match(rebased.tooltip, /^Change 1: only rebased since the last push: the same code, by patch-id \(whitespace and the message aside\), so Gerrit usually keeps the votes when you push$/m);
  assert.equal(rebased.syncTitle, SYNC_WORDS.rebased);
  // The Change view says the same, from the same verdict.
  const detail = (extra: Partial<StackCommit>) => changeVm(state({ shown: shown(commit("c", { patchset_status: "newer", ...extra })) })).notes?.[0]?.detail;
  assert.equal(detail({ code_change: "changed" }), SYNC_WORDS["code-changed"]);
  assert.equal(detail({ code_change: "rebased" }), SYNC_WORDS.rebased);
});

/** What core says of Skip at an edit stop (`RebaseProgress.refusal`). */
const SKIP_REFUSED = "--skip leaves out a commit the rebase stopped on with a conflict, but it stopped with the commit applied (an `edit`)";
const REBASE: Rebase = { branch: "feature", onto: "base000", orig_head: "three00", stop: "edit", applied: true, current: "two0000", unmerged: [], as_before: false, actions: ["continue", "abort"], refused: { skip: SKIP_REFUSED } };
/** What core allows on a conflict, unresolved and resolved (`RebaseProgress.actions`). */
const ON_CONFLICT: RebaseAction[] = ["abort", "skip"];
const RESOLVED: RebaseAction[] = ["continue", "abort", "skip"];

test("mid-rebase the stack marks where it stands and dims only what is still to be replayed", () => {
  const commits = [commit("One", { rebase: "done" }), commit("Two", { rebase: "current", is_head: true }), commit("Three", { rebase: "todo" })];
  const stopped = stackVm(stack(commits, { branch: null, rebase_in_progress: true, rebase: REBASE }), undefined, never);
  assert.deepEqual(stopped.rows.map((row) => [row.summary, row.marker, row.dim]), [["Three", null, true], ["Two", "rebase", false], ["One", null, false]]);
  assert.match(stopped.frozen ?? "", /rebase is in progress/);
  assert.deepEqual([stopped.info[0].text, stopped.info[0].detail], ["A rebase is in progress", undefined], "the stack is the live one: nothing is as it was");
  assert.equal(stackLines(stopped)[2], "▶ Two — HEAD · two0000 · the same as on Gerrit");
  // On a conflict the commit it stopped at is what wants you: a conflict marker, not dimmed; what is still to come stays dim.
  const conflict = stackVm(stack(commits, { rebase_in_progress: true, rebase: { ...REBASE, stop: "conflict", applied: false, unmerged: ["e.txt"], actions: ON_CONFLICT } }), undefined, never);
  assert.deepEqual(conflict.rows.map((row) => [row.marker, row.dim]), [[null, true], ["conflict", false], [null, false]]);
  assert.equal(conflict.info[0].text, "A rebase stopped on a conflict");
  assert.equal(stackLines(conflict)[3], "⚠ Two — HEAD · two0000 · the same as on Gerrit");
  // Resolved and added: no conflict any more, ready to continue.
  const resolved = stackVm(stack(commits, { rebase_in_progress: true, rebase: { ...REBASE, stop: "resolved", applied: false, actions: RESOLVED } }), undefined, never);
  assert.deepEqual([resolved.rows[1].marker, resolved.rows[1].dim], ["rebase", false]);
  assert.deepEqual([resolved.info[0].text, resolved.info[0].detail], ["A rebase stopped with its conflicts resolved", "ready to continue"]);
  assert.match(resolved.rows[1].tooltip, /now resolved: Continue commits it/);
});

test("where the rebase stopped at a commit is core's: its step, then whether the commit is applied, then the stop", () => {
  const at = (rebase: Partial<Rebase>, step: StackCommit["rebase"] = "current") => stoppedAt({ rebase: step }, { rebase: { ...REBASE, ...rebase } });
  assert.equal(at({ stop: "edit", applied: true }), "applied");
  assert.equal(at({ stop: "split", applied: true, current: null }), "applied");
  assert.equal(at({ stop: null, applied: true }), "applied", "core cannot tell why, but the commit is in");
  assert.equal(at({ stop: "conflict", applied: false, unmerged: ["e.txt"] }), "conflict");
  assert.equal(at({ stop: "resolved", applied: false }), "resolved");
  assert.equal(at({ stop: null, applied: false }), "stopped", "core cannot tell: no conflict inferred from a commit not applied");
  assert.equal(at({ stop: "conflict", applied: false }, "done"), null, "not the commit it stopped at");
  assert.equal(stoppedAt({ rebase: "current" }, { rebase: null }), null, "no rebase");
});

test("a resolution committed by hand as another commit is no conflict: core serves no stop, and the row and the Change view say none (PR #90)", () => {
  // GET /stack as core serves it then (tests/test_rebase_go_on.py): the original commit keeps its `current` step, not applied, and no stop.
  const commits = [commit("My own resolution", { rebase: "done", is_head: true }), commit("Two", { rebase: "current" }), commit("Three", { rebase: "todo" })];
  const byHand: Rebase = { ...REBASE, stop: null, applied: false, unmerged: [], actions: ["continue", "abort"], refused: {} };
  const vm = stackVm(stack(commits, { branch: null, rebase_in_progress: true, rebase: byHand }), undefined, never);
  const two = vm.rows.find((row) => row.summary === "Two");
  assert.equal(two?.marker, "rebase", "a neutral marker, not a conflict");
  assert.match(two?.tooltip ?? "", /no telling how this commit stands/);
  assert.doesNotMatch(two?.tooltip ?? "", /conflict/);
  assert.deepEqual([vm.info[0].text, vm.info.length], ["A rebase is in progress", 1], "no conflict words, no conflicted files");
  assert.deepEqual(
    (vm.info[0].actions ?? []).map((action) => `${action.label}${action.primary ? "!" : ""}`),
    ["Continue!", "Abort"],
    "the ways on core serves, Continue the one to press, as wherever core allows it",
  );
});

test("the rebase banner offers the ways on core allows from where it stopped, and lists the files still conflicted", () => {
  const commits = [commit("One", { rebase: "done" }), commit("Two", { rebase: "current", is_head: true }), commit("Three", { rebase: "todo" })];
  const at = (rebase: Partial<Rebase>) => stackVm(stack(commits, { rebase_in_progress: true, rebase: { ...REBASE, ...rebase } }), undefined, never);
  const buttons = (vm: StackVm) => (vm.info[0].actions ?? []).map((action) => `${action.label}${action.primary ? "!" : ""} ${action.command.id}`);
  // Applied whole (an edit, a split): core offers nothing to skip, only to go on or go back.
  assert.deepEqual(buttons(at({ stop: "edit" })), ["Continue! ger.rebase.continue", "Abort ger.rebase.abort"]);
  const split = at({ stop: "split", current: null });
  assert.deepEqual(buttons(split), ["Continue! ger.rebase.continue", "Abort ger.rebase.abort"]);
  assert.deepEqual([split.info[0].text, split.info[0].detail], ["A rebase stopped while you split a commit", "Continue replays the rest on top of HEAD"]);
  // Still conflicted: no Continue until it is resolved; the commit can be left out. Resolved, Continue is the one to press.
  const conflict = at({ stop: "conflict", applied: false, unmerged: ["src/e.txt", "f.txt"], actions: ON_CONFLICT });
  assert.deepEqual(buttons(conflict), ["Abort ger.rebase.abort", "Skip ger.rebase.skip"]);
  assert.deepEqual(buttons(at({ stop: "resolved", applied: false, actions: RESOLVED })), ["Continue! ger.rebase.continue", "Abort ger.rebase.abort", "Skip ger.rebase.skip"]);
  // Each file still unmerged is a row under the banner; a click opens it to resolve.
  assert.deepEqual(conflict.info.slice(1).map((info) => [info.text, info.conflict, info.tone]), [["src/e.txt", "src/e.txt", "bad"], ["f.txt", "f.txt", "bad"]]);
  assert.deepEqual(stackLines(conflict).slice(0, 3), ["A rebase stopped on a conflict  [Abort] [Skip]", "  ⚠ src/e.txt — conflicted", "  ⚠ f.txt — conflicted"]);
  assert.equal(stackLines(at({ stop: "resolved", applied: false, actions: RESOLVED }))[0], "A rebase stopped with its conflicts resolved — ready to continue  [**Continue**] [Abort] [Skip]");
  // git rerere put a recorded resolution in: the banner says so, to check it before Continue.
  assert.equal(
    stackLines(at({ stop: "resolved", applied: false, actions: RESOLVED, reused: ["README.md"] }))[0],
    "A rebase stopped with its conflicts resolved — git reused a recorded resolution for README.md: check it before you continue  [**Continue**] [Abort] [Skip]",
  );
  // The buttons are core's verdict, not the extension's reading of `stop`.
  assert.deepEqual(buttons(at({ stop: "resolved", applied: false, actions: ["abort"] })), ["Abort ger.rebase.abort"]);
  assert.equal(stackVm(stack(commits), undefined, never).info.length, 0, "no rebase, no banner");
});

test("the palette's Continue and Skip run only where core allows them, and say why not", () => {
  const at = (rebase: Partial<Rebase>) => stack([commit("Two", { rebase: "current" })], { rebase_in_progress: true, rebase: { ...REBASE, ...rebase } });
  // Why not is core's refusal, as ger rebase prints it: one wording per stop, not the extension's.
  assert.equal(rebaseNotNow(at({}), "skip"), `${SKIP_REFUSED[0].toUpperCase()}${SKIP_REFUSED.slice(1)}.`);
  assert.equal(rebaseNotNow(at({}), "continue"), undefined);
  const conflict = at({ stop: "conflict", applied: false, unmerged: ["e.txt"], actions: ON_CONFLICT, refused: { continue: "the index has unresolved conflicts; resolve the conflicts in e.txt" } });
  assert.equal(rebaseNotNow(conflict, "continue"), "The index has unresolved conflicts; resolve the conflicts in e.txt.");
  assert.equal(rebaseNotNow(conflict, "skip"), undefined);
  assert.equal(rebaseNotNow(at({ actions: ["abort"], refused: {} }), "skip"), undefined, "core said nothing: ger rebase decides");
  assert.equal(rebaseNotNow(stack([commit("Two")]), "abort"), REBASE_WORDS.noRebase);
  assert.equal(rebaseNotNow(undefined, "skip"), undefined, "before the stack is in, ger rebase decides");
});

test("the palette's Continue looks again before it says no: a git add in the terminal may have resolved the stop", async () => {
  const at = (rebase: Partial<Rebase>) => stack([commit("Two", { rebase: "current" })], { rebase_in_progress: true, rebase: { ...REBASE, ...rebase } });
  const conflict = at({ stop: "conflict", applied: false, unmerged: ["e.txt"], actions: ON_CONFLICT, refused: { continue: "the index has unresolved conflicts; resolve the conflicts in e.txt" } });
  const resolved = at({ stop: "resolved", applied: false, actions: RESOLVED });
  // Loaded before the git add; the refresh reads the stop as it is now: Continue goes ahead.
  let refreshes = 0;
  const staged = { stack: conflict, refresh: async () => void (refreshes++, (staged.stack = resolved)) };
  assert.equal(await rebaseNotNowFresh(staged, "continue"), undefined);
  assert.equal(refreshes, 1);
  // Still conflicted after the fresh look: core's refusal, as before.
  const still = { stack: conflict, refresh: async () => void refreshes++ };
  assert.equal(await rebaseNotNowFresh(still, "continue"), "The index has unresolved conflicts; resolve the conflicts in e.txt.");
  assert.equal(refreshes, 2);
  // Allowed already: no extra load before the terminal runs it.
  assert.equal(await rebaseNotNowFresh({ stack: resolved, refresh: async () => void refreshes++ }, "continue"), undefined);
  assert.equal(refreshes, 2);
});

test("Skip says what it throws away: the commit and the resolution so far; Abort brings back the branch, not the resolution", () => {
  const conflicted = (pushed: boolean) => stack([commit("Two", { rebase: "current", pushed })], { rebase_in_progress: true, rebase: { ...REBASE, stop: "conflict", applied: false, unmerged: ["e.txt"], actions: ON_CONFLICT } });
  const { message, detail } = skipQuestion(conflicted(false));
  assert.equal(message, 'Skip "Two"?');
  assert.match(detail, /the commit and the conflict resolution done so far, staged or not, are thrown away/);
  assert.match(detail, /Abort instead brings back the branch as it was before the rebase, but not the resolution\.$/);
  assert.doesNotMatch(detail, /Abort brings it back/);
  assert.match(skipQuestion(conflicted(true)).detail, /Its change stays open on Gerrit until you abandon it there\.$/);
});

test("Push during a rebase offers only the ways on core allows: no Continue while files are conflicted", () => {
  const at = (rebase: Partial<Rebase>) => stack([commit("Two", { rebase: "current" })], { rebase_in_progress: true, rebase: { ...REBASE, ...rebase } });
  assert.deepEqual(pushWaysOn(at({})), ["continue", "abort"]);
  assert.deepEqual(pushWaysOn(at({ stop: "conflict", applied: false, unmerged: ["e.txt"], actions: ON_CONFLICT })), ["abort"]);
  assert.deepEqual(pushWaysOn(at({ stop: "resolved", applied: false, actions: RESOLVED })), ["continue", "abort"]);
});

test("a rebase that says nothing of its commits shows the branch as it was, and says so", () => {
  const vm = stackVm(stack([commit("One")], { rebase_in_progress: true, rebase: { ...REBASE, stop: "conflict", applied: false, current: null, unmerged: ["e.txt"], as_before: true, actions: ON_CONFLICT } }), undefined, never);
  assert.deepEqual([vm.info[0].text, vm.info[0].detail], ["A rebase stopped on a conflict", "this is the branch as it was before"]);
  assert.deepEqual(vm.info.slice(1).map((info) => info.conflict), ["e.txt"], "it still names what is conflicted");
  // Core says whether the stack is as before; no row having a step (all below onto) does not make it so.
  const live = stackVm(stack([commit("One")], { rebase_in_progress: true, rebase: { ...REBASE, stop: "paused", current: null } }), undefined, never);
  assert.deepEqual([live.info[0].text, live.info[0].detail], ["A rebase is in progress", undefined]);
});

test("the header names the branch being rebased", () => {
  assert.equal(stackWhere(stack([commit("One")], { branch: "feature" })), "feature → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null, rebase_in_progress: true, rebase: REBASE })), "feature (rebasing) → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null, rebase_in_progress: true, rebase: { ...REBASE, branch: null } })), "detached (rebasing) → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null })), "detached → origin/main");
  // A detached HEAD's rebase has no working branch, so no target branch: its stack starts at the rebase's onto.
  const detached = stack([commit("One")], { branch: null, rebase_in_progress: true, rebase: { ...REBASE, branch: null } });
  assert.equal(stackWhere({ ...detached, stack: { ...detached.stack, target_branch: null, push_branch: null } }), "detached (rebasing)");
});

test("a staged review is somebody else's chain: named as ger review prints it, the change under review marked, nothing moves", () => {
  const [base, change] = [commit("Base", { number: 2155, is_head: true }), commit("Change", { number: 2156 })];
  const review = { number: 2156, patch_set: 2, commit: change.sha, subject: "Change", staged: true, mode: "worktree" as const, worktree: "/r", branch: "change-2156" };
  const staged = stack([base, change], { branch: null, head: base.sha, staged_review: review });
  assert.equal(stackWhere(staged), "reviewing 2155 ← [2156]");
  const vm = stackVm(staged, undefined, never);
  // HEAD sits at the parent only to stage the change: not said; the change is.
  assert.deepEqual(vm.rows.map((row) => row.notes[0]), ["under review", base.short_sha]);
  assert.deepEqual([vm.review, vm.movable, vm.rows.some((row) => row.squash)], [true, 0, false]);
  assert.equal(vm.frozen, frozen(staged));
  assert.match(frozen(staged) ?? "", /somebody else's commits/);
  const behind = { name: "origin/main", behind: 1, argv: ["rebase", "origin/main"], conflict: null, preview: true, reason: null, dropped: [] };
  assert.deepEqual(stackVm({ ...staged, upstream: behind }, undefined, never).info, [], "no Rebase onto origin/main: core refuses it there");
});

test("a merge is one row; it and what is below it stay, as ger serve says, what is above it still moves", () => {
  const withMerge = stack([commit("One", { pinned: true }), commit("Merge side", { merge: true, pinned: true }), commit("Two"), commit("Three")]);
  assert.equal(frozen(withMerge), null, "a merge no longer freezes the whole stack");
  assert.equal(pinned(withMerge), 2);
  const vm = stackVm(withMerge, undefined, never);
  assert.equal(vm.movable, 2, "drops land no lower than under Two");
  assert.deepEqual(vm.rows.map((row) => [row.summary, row.merge, row.pinned, row.squash]), [
    ["Three", false, false, true],
    ["Two", false, false, false],
    ["Merge side", true, true, false],
    ["One", false, true, false],
  ]);
  assert.deepEqual(vm.rows[2].notes, ["mergesi", "merge"]);
  assert.match(vm.rows[2].tooltip, /commits it brings in are not part of the stack/);
  assert.match(pinnedWhy(withMerge), /"Merge side" is a merge commit/);
  assert.equal(pinned(stack([commit("One")])), 0);
  assert.equal(stackVm(stack([commit("One")]), undefined, never).movable, 1);
});

test("without an upstream the stack offers one to track; while ger is not running it says so", () => {
  const vm = stackVm({ error: { code: "GIT", message: "no upstream" }, needs_upstream: true }, { branch: "topic", upstream: null, suggested: "origin/main", why: "created from it", candidates: [] }, never);
  assert.deepEqual(vm.info.map((info) => info.text), ["topic has no upstream, so ger cannot tell where your stack starts", "Track origin/main", "Choose another upstream branch…"]);
  assert.deepEqual(vm.info[1].command, { id: "ger.setUpstream", args: ["origin/main"] });
  assert.equal(waitingFor("ready", "Your local stack"), undefined);
  assert.equal(waitingFor("starting", "Your local stack")?.text, "Starting ger serve…");
  assert.equal(waitingFor("setup", "Your local stack")?.link?.command, "ger.overview.focus");
  // Setup wanting, with ger's reason: said in ger's words, with the step that fixes it.
  const refused = waitingFor("setup", "Your local stack", undefined, "ger.webUrl must use https://");
  assert.deepEqual(refused, { text: "Your local stack shows here once ger can ask Gerrit. ger says: ger.webUrl must use https://", link: { label: "Run ger setup", command: "ger.setup" } });
});

// --- Change -------------------------------------------------------------------------

test("the change view shows the message, the files with their line counts, and what is compared", () => {
  const vm = changeVm(state());
  assert.deepEqual(vm.message, { key: shown().change_id, text: "Extract routing", changeId: "I1", author: "Me <me@example.com>", readOnly: "", plain: false });
  assert.deepEqual(vm.files?.rows.map((row) => [row.name, row.dir, row.state, row.added, row.deleted]), [["route.py", "src", "modified", 12, 3], ["logo.png", "", "added", null, null]]);
  assert.deepEqual([vm.files?.added, vm.files?.deleted], [12, 3]);
  assert.deepEqual(changeLines(vm), [
    "Nothing to do — no votes",
    "Commit message — Extract routing  (editable)",
    "Comparing — Base commit → Local commit extract · default: Against its base commit",
    "Files — 2 · +12 −3",
    "  route.py — src · modified +12 −3",
    "  logo.png — added binary",
    "Comments — none",
  ]);
});

test("stopped by a rebase on a conflict, the change view says what is left and how to go on, not what its votes will do", () => {
  const ahead = shown({ patchset_status: "newer", attention_reasons: ["ahead-of-gerrit", "ci-failed"], ci_failures: ["Verified"], verified: -1 });
  const conflict = changeVm(state({ shown: ahead, stopped: { stop: "conflict", unmerged: ["src/d.txt"], actions: ON_CONFLICT } }));
  assert.equal(conflict.head?.text, "Conflict in src/d.txt — resolve it, then Continue");
  assert.deepEqual([conflict.head?.icon, conflict.head?.tone], ["warning", "bad"]);
  assert.deepEqual(conflict.head?.actions?.map((action) => action.command.id), ["ger.rebase.abort", "ger.rebase.skip"], "the ways on core serves: no Continue over open conflicts");
  assert.deepEqual(conflict.notes, [], "\"ahead of Gerrit… only rebased: the votes stay\" is not true yet");
  // What Gerrit has stays true: a newer patchset there, and the last pushed patchset's failed builds and their logs.
  const outdated = changeVm(state({ shown: shown({ patchset_status: "outdated" }), stopped: { stop: "conflict", unmerged: ["src/d.txt"], actions: ON_CONFLICT } }));
  assert.deepEqual(outdated.notes?.map((note) => note.text), ["Gerrit has a newer patchset than this commit"]);
  assert.deepEqual([conflict.ci?.summary, conflict.ci?.state], ["Verified", "closed"], "the failed builds are named");
  const logs = (stopped: ChangeState["stopped"], contents: ReturnType<typeof content>[]) =>
    changeVm(state({ shown: ahead, stopped, ci: { state: "loaded", contents, introduced: null } }));
  const atConflict: ChangeState["stopped"] = { stop: "conflict", unmerged: ["src/d.txt"], actions: ON_CONFLICT };
  assert.deepEqual(logs(atConflict, [content(true, [finding("undeclared identifier", "error", "e1")])]).ci?.links.map((link) => link.url), [content(true, []).provenance.url], "its log links");
  // Why there is no log is about this commit's build, which there is not yet: not said.
  assert.deepEqual(changeLines(logs(atConflict, [])).slice(-1), ["CI — Verified"]);
  assert.deepEqual(changeLines(logs(undefined, [])).slice(-2), ["CI — Verified", "  No CI link was found for the failed build"], "applied, it is said");
  // The conflicted files and the buttons are the banner's: one function draws each, from the same served stop.
  const banner = stackVm(stack([commit("One", { rebase: "current" })], { rebase_in_progress: true, rebase: { ...REBASE, stop: "conflict", applied: false, unmerged: ["src/d.txt"], actions: ON_CONFLICT } }), undefined, never).info;
  assert.deepEqual(conflict.conflicts?.rows, banner.slice(1));
  assert.deepEqual(conflict.head?.actions, banner[0].actions);
  assert.deepEqual([conflict.files?.title, conflict.files?.rows.length], ["Files, as before the rebase", 2], "the commit as it was, and it says so");
  assert.deepEqual(changeLines(conflict).slice(0, 6), [
    "Conflict in src/d.txt — resolve it, then Continue — V-1  [Abort] [Skip]",
    "Commit message — Extract routing  (editable)",
    "Comparing — Base commit → Local commit extract · default: Against its base commit",
    "Conflicts — 1 file",
    "  ⚠ src/d.txt — conflicted",
    "Files, as before the rebase — 2 · +12 −3",
  ]);
  assert.equal(changeVm(state({ stopped: { stop: "conflict", unmerged: ["a", "b", "c"], actions: ON_CONFLICT } })).head?.text, "Conflicts in 3 files — resolve them, then Continue");
  // Resolved and added: Continue is the one to press; the rows stay hidden until it is applied.
  const resolved = changeVm(state({ shown: ahead, stopped: { stop: "resolved", unmerged: [], actions: RESOLVED } }));
  assert.equal(resolved.head?.text, "Conflicts resolved — Continue");
  assert.deepEqual(resolved.head?.actions?.map((action) => `${action.label}${action.primary ? "!" : ""}`), ["Continue!", "Abort", "Skip"]);
  assert.deepEqual([resolved.conflicts, resolved.notes, resolved.ci?.summary], [undefined, [], "Verified"]);
  // A patchset of the change is no commit the rebase holds: its files are not labelled as it.
  assert.equal(changeVm(state({ stopped: { stop: "conflict", unmerged: ["a"], actions: ON_CONFLICT }, version: 3 })).files?.title, "Files");
});

test("the change view says the subject once, in the commit message: its title bar names only the change number", () => {
  const vm = changeVm(state());
  assert.equal(changeHeading(7), "Change 7");
  assert.equal(changeHeading(undefined), "Change");
  assert.equal(JSON.stringify(vm).split("Extract routing").length - 1, 1, "no tooltip repeats it");
});

test("the CI section does not repeat the head's \"CI failed\": it names the failing builds, or nothing", () => {
  const failed = (ci_failures: string[]) => changeVm(state({ shown: shown({ ci_failures, verified: -1, attention_reasons: ["ci-failed"] }) }));
  assert.equal(failed(["Verified"]).ci?.summary, "Verified");
  assert.equal(failed([]).ci?.summary, "");
  assert.equal(changeLines(failed([])).at(-1), "CI");
});

test("the change view shows the votes as the Local stack's badges, the same ones", () => {
  const voted = shown({ code_review: -1, verified: 1, comments_unresolved: 2 });
  const head = changeVm(state({ shown: voted })).head;
  assert.deepEqual(head?.badges, badges(voted).filter(Boolean));
  assert.deepEqual(head?.badges.map((badge) => [badge.kind, badge.icon, badge.text]), [["comments", "comment-unresolved", "2"], ["review", "thumbsdown", "-1"], ["ci", "pass", ""]]);
  assert.deepEqual(changeVm(state({ shown: shown({ pushed: false, code_review: 1 }) })).head?.badges, [], "not on Gerrit: no votes to show");
});

test("To starts on the local commit and lists Gerrit's patchsets, newest first; From lists them and the base commit below", () => {
  const before = changeVm(state()).compare;
  assert.deepEqual(before?.to, { options: [{ value: "local", label: "Local commit extract" }, { value: "current", label: "Gerrit's current patchset" }], selected: "local" });
  assert.deepEqual(before?.from.options.map((option) => option.value), ["current", "reviewed", "base"], "the patchsets not known yet: the ones a mode names");
  assert.equal(before?.from.selected, "base");
  const after = changeVm(state({ patchsets })).compare;
  assert.deepEqual(after?.to.options.map((option) => option.label), ["Local commit extract", "Patchset 3 (current)", "Patchset 2 (you reviewed it)", "Patchset 1"]);
  assert.deepEqual(after?.from.options.map((option) => option.label), ["Patchset 3 (current)", "Patchset 2 (you reviewed it)", "Patchset 1", "Base commit"]);
  // Someone's change: no local commit to choose, Gerrit's current patchset is what is shown.
  assert.equal(changeVm(state({ local: false, version: "current", patchsets })).compare?.to.selected, "3");
  const unpushed = changeVm(state({ shown: shown({ pushed: false }) })).compare;
  assert.deepEqual([unpushed?.from.options.map((option) => option.value), unpushed?.to.options.map((option) => option.value)], [["base"], ["local"]]);
});

test("before the patchset list has come, Gerrit's current patchset is named by its number when the commit's status says it", () => {
  const numbered = changeVm(state({ shown: shown({ current_patch_set: 15 }) })).compare;
  assert.deepEqual(numbered?.to.options.at(-1), { value: "current", label: "Gerrit's patchset 15 (current)" });
  assert.equal(numbered?.from.options[0].label, "Gerrit's patchset 15 (current)");
  // An older ger serve does not say it: the words alone, as before.
  assert.equal(changeVm(state()).compare?.to.options.at(-1)?.label, "Gerrit's current patchset");
});

test("From offers only patchsets older than To; the symbolic sides show as the patchsets they are", () => {
  const disabled = (extra: Partial<ChangeState>) => changeVm(state({ patchsets, ...extra })).compare?.from.options.filter((option) => option.disabled).map((option) => option.value);
  assert.deepEqual(disabled({}), [], "against your commit, every patchset is older");
  assert.deepEqual(disabled({ version: 2 }), ["3", "2"]);
  assert.deepEqual(disabled({ version: "current" }), ["3"]);
  const reviewed = changeVm(state({ patchsets, from: "reviewed", version: "current" })).compare;
  assert.deepEqual([reviewed?.from.selected, reviewed?.to.selected], ["2", "3"]);
  const never = changeVm(state({ patchsets: { ...patchsets, reviewed: null }, from: "reviewed", version: "current" })).compare?.from;
  assert.deepEqual([never?.selected, never?.options.at(-2)], ["reviewed", { value: "reviewed", label: "Patchset you last reviewed (none)", disabled: true }]);
});

test("a mode is From and To for a change; one the change does not allow is its base comparison", () => {
  const all = { pushed: true, local: true };
  assert.deepEqual(modeSides("base", all), { from: "base", to: "local" });
  assert.deepEqual(modeSides("reviewed", all), { from: "reviewed", to: "current" });
  assert.deepEqual(modeSides("gerrit", all), { from: "current", to: "local" });
  assert.deepEqual(modeSides("gerrit", { pushed: true, local: false }), { from: "base", to: "current" }, "someone's change: no commit of yours");
  assert.deepEqual(modeSides("reviewed", { pushed: false, local: true }), { from: "base", to: "local" }, "not on Gerrit: no patchset");
  assert.deepEqual(modesFor({ pushed: true, local: false }), ["base", "reviewed"]);
  assert.deepEqual(modesFor({ pushed: false, local: true }), ["base"]);
});

test("To at or below From sets From back to the base commit; From older than To stays", () => {
  assert.deepEqual(fitSides(2, 3, patchsets), { from: 2, to: 3 });
  assert.deepEqual(fitSides(3, 2, patchsets), { from: "base", to: 2 });
  assert.deepEqual(fitSides("current", "current", patchsets), { from: "base", to: "current" });
  assert.deepEqual(fitSides("current", "local", patchsets), { from: "current", to: "local" });
  // The patchsets not known yet: the same symbolic side on both ends is no comparison, and nothing refits it once they come.
  assert.deepEqual(fitSides("current", "current", undefined), { from: "base", to: "current" });
  assert.deepEqual(fitSides("current", "local", undefined), { from: "current", to: "local" });
  assert.deepEqual(fitSides("reviewed", "current", undefined), { from: "reviewed", to: "current" }, "since your review: core says when there is nothing new");
});

test("the mode toggle is lit while From and To are the default's, and names the next mode a click makes the default", () => {
  const toggle = (extra: Partial<ChangeState>) => changeVm(state({ patchsets, ...extra })).compare?.mode;
  const lit = toggle({});
  assert.deepEqual([lit?.mode, lit?.icon, lit?.next, lit?.active], ["base", "git-commit", "reviewed", true]);
  assert.equal(lit?.title, "Default comparison: Against its base commit. Click for Since my last review.");
  const byHand = toggle({ from: 1 });
  assert.equal(byHand?.active, false);
  assert.match(byHand?.title ?? "", /picked by hand for this change/);
  // Resolved: "current" is patchset 3, so picking patchset 3 by hand is the default's From still.
  assert.equal(toggle({ mode: "gerrit", from: 3 })?.active, true);
  assert.equal(toggle({ mode: "gerrit", from: "current" })?.next, "base", "the last mode wraps to the first");
  assert.equal(toggle({ mode: "gerrit", local: false, version: "current" })?.next, "base", "someone's change skips Gerrit vs. local");
});

test("a review delta names the two versions, and says when there is nothing to show", () => {
  const since = { patch_set: 2, current_patch_set: 3, to_patch_set: null, up_to_date: false, identical: false, rebased: false };
  const gerrit = changeVm(state({ from: "current", delta: diff({ since: { ...since, patch_set: 3 }, files: [] }) })).files;
  assert.deepEqual([gerrit?.title, gerrit?.summary], ["Gerrit vs. local: patchset 3 → local", "0 files"]);
  // Your commit is the patchset itself: core says so, and the view says there is nothing to push.
  const same = changeVm(state({ from: "current", delta: diff({ since: { ...since, patch_set: 3, identical: true }, files: [] }) })).files;
  assert.deepEqual([same?.title, same?.summary], ["Gerrit vs. local", "identical to patchset 3 — nothing to push"]);
  const reviewed = changeVm(state({ from: "reviewed", version: 3, delta: diff({ since: { ...since, to_patch_set: 3, rebased: true } }) })).files;
  assert.equal(reviewed?.title, "Since your review: patchset 2 → patchset 3 (rebased)");
  const never = changeVm(state({ from: "reviewed", delta: diff({ since: { ...since, patch_set: null }, files: [] }) })).files;
  assert.deepEqual([never?.title, never?.note], ["Since your review", "you have not reviewed this change yet"]);
  // Two patchsets picked by hand, neither the reviewed one nor Gerrit's against yours: named as compared.
  const picked = changeVm(state({ patchsets, from: 1, version: 3, delta: diff({ since: { ...since, patch_set: 1, to_patch_set: 3 } }) })).files;
  assert.equal(picked?.title, "Compared: patchset 1 → patchset 3");
  // Someone's change you reviewed before: what is new is one click away, whatever is compared.
  const hint = changeVm(state({ local: false, version: "current", delta: diff({ since: { ...since, to_patch_set: 3 } }) })).hint;
  assert.deepEqual(hint, { text: "Since your review: patchset 2 → 3 · 2 files", show: true });
  assert.equal(changeVm(state({ delta: diff({ since }) })).hint, undefined, "your own commit has no review of yours to compare with");
});

test("a change checked out for review reads as theirs: behind Gerrit, Update to patchset N leads; else what is new since your review", () => {
  const since = { patch_set: 2, current_patch_set: 3, to_patch_set: null, up_to_date: false, identical: false, rebased: false };
  const behind = changeVm(state({ shown: shown({ patchset_status: "outdated" }), reviewing: { behind: true, newest: 3 }, delta: diff({ since }) }));
  assert.deepEqual(behind.notes?.map((note) => [note.text, note.actions?.map((action) => [action.label, action.command.id, action.primary])]), [
    ["Gerrit has a newer patchset than the one checked out", [["Update to patchset 3", "ger.reviewUpdate", true]]],
  ]);
  assert.equal(behind.hint, undefined, "the update says what there is to do; a delta to the old commit would not");
  const unknown = changeVm(state({ shown: shown({ patchset_status: "outdated" }), reviewing: { behind: true, newest: null } }));
  assert.equal(unknown.notes?.[0].actions?.[0].label, "Update to the newest patchset");
  const current = changeVm(state({ reviewing: { behind: false, newest: 3 }, delta: diff({ since }) }));
  assert.deepEqual(current.hint, { text: "Since your review: patchset 2 → 3 · 2 files", show: true });
  // Nothing differs since the review: no line. A staged review compares your checkout, where core's up_to_date is false.
  const one = changeVm(state({ reviewing: { behind: false, newest: 1 }, delta: diff({ since: { ...since, patch_set: 1, current_patch_set: 1 }, files: [] }) }));
  assert.equal(one.hint, undefined, "patchset 1 → 1 · 0 files says nothing");
  const sameFiles = changeVm(state({ local: false, version: "current", delta: diff({ since: { ...since, to_patch_set: 3 }, files: [] }) }));
  assert.equal(sameFiles.hint, undefined, "no file differs");
  const upToDate = changeVm(state({ local: false, version: "current", delta: diff({ since: { ...since, patch_set: 3, to_patch_set: 3, up_to_date: true } }) }));
  assert.equal(upToDate.hint, undefined, "you reviewed the current patchset");
});

test("the message box holds what core serves without the Change-Id footer; the Change-Id is said beside it", () => {
  const vm = changeVm(state({ base: diff({ message: "Fix it\n\nBug: 12\nChange-Id: I1", message_text: "Fix it\n\nBug: 12", change_id: "I1" }) }));
  assert.deepEqual([vm.message?.text, vm.message?.changeId], ["Fix it\n\nBug: 12", "I1"]);
  const none = changeVm(state({ base: diff({ message: "Fix it", message_text: "Fix it", change_id: null }) }));
  assert.deepEqual([none.message?.text, none.message?.changeId], ["Fix it", ""]);
});

test("a message that cannot be reworded here is plain text; one held by a rebase stays a box with the reason", () => {
  for (const readOnly of [READ_ONLY.remote, READ_ONLY.patchset]) {
    assert.equal(changeVm(state({ readOnly })).message?.plain, true, readOnly);
    assert.equal(changeVm(state({ readOnly, base: undefined, loading: { commit: false, base: true, delta: false, ready: false } })).loading?.plain, true, "its place too");
  }
  assert.equal(changeVm(state({ readOnly: READ_ONLY.rebase })).message?.plain, false);
  assert.equal(changeVm(state()).message?.plain, false);
});

test("the commit message is read-only with the reason, and comment chains say what can be done with them", () => {
  assert.equal(changeVm(state({ readOnly: READ_ONLY.rebase })).message?.readOnly, "The message cannot be edited during a rebase: reword it once the rebase is finished or aborted.");
  const vm = changeVm(
    state({
      chains: [
        chain("open", [comment("Please rename this."), comment("Renamed it.", { state: "draft", author: undefined })], { held: true }),
        chain("note", [comment("Check the docs.", { state: "note", author: undefined, comment_id: "n1" })]),
        chain("done", [comment("Typo.")], { resolved: true }),
      ],
    }),
  );
  const rows = vm.comments?.threads ?? [];
  assert.deepEqual(rows.map((row) => [row.id, row.reply, row.open, row.held, row.icon]), [["open", true, true, true, "person"], ["note", false, false, false, "note"], ["done", true, false, false, "pass"]]);
  assert.deepEqual(rows[0].replies, [{ author: "you", state: "draft", text: "Renamed it." }]);
  // What you have not published can be edited and deleted; a draft Gerrit gave no id cannot be named, so neither.
  assert.deepEqual(rows.map((row) => [row.edit, row.discard]), [[false, ""], [true, "Delete Your Private Note…"], [false, ""]]);
  // A row shows the first line; the whole text is there to expand. Only a chain on a file's line opens in the editor.
  const long = changeVm(
    state({ chains: [chain("whole", [comment("Looks good overall.\n\nBut the second half needs a test.", { path: "/PATCHSET_LEVEL", line: null })], { path: "/PATCHSET_LEVEL", line: null })] }),
  ).comments?.threads[0];
  assert.deepEqual([long?.text, long?.body, long?.author, long?.file, long?.reply], ["Looks good overall.", "Looks good overall.\n\nBut the second half needs a test.", "Alice", false, true]);
  assert.deepEqual(rows.map((row) => row.file), [true, true, true]);
  // A Gerrit fix suggestion shows below the text as ger serve writes it in `text`: the Change view and tooltip print text as it is.
  const plain = "Name it.\n\nsuggested fix: Set x\n  a.py:4 becomes:\n  +     x = 1";
  const fixed = changeVm(state({ chains: [chain("fix", [comment("Name it.", { text: plain, markdown: "Name it.\n\n**Suggested fix:** Set x\n`a.py:4` becomes:\n```\n    x = 1\n```" })])] })).comments?.threads[0];
  assert.deepEqual([fixed?.body, fixed?.tooltip], [plain, `Alice:\n${plain}`]);
  const replied = changeVm(state({ chains: [chain("open", [comment("Please rename this."), comment("Renamed it.", { state: "draft", comment_id: "d1" })])] }));
  assert.deepEqual([replied.comments?.threads[0].edit, replied.comments?.threads[0].discard], [false, "Delete Your Draft Reply…"]);
  // The Resolved checkbox of a reply box starts where the chain stands once your drafts are published.
  const boxes = changeVm(
    state({
      chains: [
        chain("open", [comment("Please rename this.")]),
        chain("answered", [comment("Rename it."), comment("Done", { state: "draft", comment_id: "d2" })], { resolved_after_publish: true }),
        chain("closed", [comment("Nit.")], { resolved: true, resolved_after_publish: true }),
      ],
    }),
  ).comments?.threads;
  assert.deepEqual(
    boxes?.map((row) => [row.id, row.resolving, row.draft]),
    [
      ["open", false, false],
      ["answered", true, true],
      ["closed", true, false],
    ],
  );
  assert.equal(vm.comments?.summary, "1 answered by your drafts (Vote and Publish sends them) · 1 of yours, not published");
  assert.deepEqual(changeLines(vm).slice(-5), [
    "Comments — 1 answered by your drafts (Vote and Publish sends them) · 1 of yours, not published",
    "  Please rename this. — kept for you · a.py:4 · Alice · 1 reply  [open, kept for you]",
    "    you — [draft] Renamed it.",
    "  Check the docs. — your private note · a.py:4  [yours]",
    "  Typo. — a.py:4 · Alice  [resolved]",
  ]);
});

test("the CI group lists errors first and keeps the warnings behind a count", () => {
  const failing = shown({ ci_failures: ["Verified"], attention_reasons: ["ci-failed"] });
  const both = [finding("unused variable", "warning", "w1"), finding("undeclared identifier", "error", "e1")];
  const vm = changeVm(state({ shown: failing, ci: { state: "loaded", contents: [content(true, both)], introduced: new Set(["w1", "e1"]) } }));
  const [link] = vm.ci?.links ?? [];
  assert.deepEqual([link.detail, link.findings.map((row) => row.text), link.warnings.map((row) => row.text)], ["1 finding · 1 warning", ["undeclared identifier"], ["unused variable"]]);
  assert.deepEqual(changeLines(vm).slice(-2), ["    undeclared identifier — cc · src/main.c:3 · introduced here", "    1 warning — collapsed"]);
  // With no error to explain the failure, the warnings are the lead and stay in view.
  const only = changeVm(state({ shown: failing, ci: { state: "loaded", contents: [content(false, [both[0]])], introduced: null } }));
  assert.deepEqual([only.ci?.links[0].findings.length, only.ci?.links[0].warnings.length], [1, 0]);
});

test("the last pushed patchset's CI findings stay while you fix them, said to be from before your changes", () => {
  const moved = shown({ ci_links: [{ label: "jenkins", url: "u", source: "message" }], attention_reasons: ["ahead-of-gerrit"] });
  const error = { ...finding("undeclared identifier", "error", "e1"), line: 5, patchset_line: 3 };
  const vm = changeVm(state({ shown: moved, ci: { state: "loaded", contents: [{ ...content(true, [error]), patch_set: 2 }], introduced: null } }));
  const [link] = vm.ci?.links ?? [];
  assert.deepEqual([link.icon, link.tone, link.detail], ["history", "dim", "from patchset 2, before your local changes · 1 finding"]);
  assert.equal(link.findings[0].line, 5, "a click opens the line where it is now");
  assert.ok(link.findings[0].notes.includes("src/main.c:5 (line 3 on patchset 2)"));
});

test("an unstable build is named so, with a warning (not the error), and still counts as CI to look at", () => {
  const link = { label: "jenkins", url: "https://ci.example.org/job/Widget/55/", source: "message" };
  const unstable = commit("c", { ci_failures: ["jenkins"], verified: -1, attention_reasons: ["ci-failed"], ci_links: [{ ...link, verdict: "unstable" }] });
  const ci = badges(unstable)[2];
  assert.deepEqual([ci?.icon, ci?.tone, ci?.title], ["warning", "warn", "CI unstable: jenkins"]);
  const failed = badges({ ...unstable, ci_links: [{ ...link, verdict: "unstable" }, { ...link, label: "lint", verdict: "failed" }] })[2];
  assert.deepEqual([failed?.icon, failed?.tone], ["error", "bad"], "one failed build among them: CI failed");
  const vm = changeVm(state({ shown: shown({ ...unstable }), ci: { state: "loaded", contents: [{ ...content(true, []), verdict: "unstable" }], introduced: null } }));
  assert.deepEqual([vm.ci?.summary, vm.ci?.links[0].icon, vm.ci?.links[0].tone, vm.ci?.links[0].detail], ["jenkins (unstable)", "warning", "warn", "unstable · 0 findings"]);
});

// A4: Review chains showed a comment icon for a negative vote, where the Change view listed no comment.
test("a change wears the comment icon exactly when the Change view lists an open comment", () => {
  const published = (patch_set: number, extra: Partial<CommentChain> = {}): CommentChain => ({
    root_id: `c${patch_set}`,
    path: "a.py",
    line: 4,
    start_line: null,
    url: null,
    resolved: false,
    resolved_after_publish: false,
    outdated: patch_set < 3,
    held: false,
    comments: [comment("Rename this.", { patch_set })],
    ...extra,
  });
  const cases: [string, Partial<StackCommit>, CommentChain[]][] = [
    // The ger-playground change of A4: a negative vote and no comment at all.
    ["negative vote, no comment", { code_review: -1, attention_reasons: ["review-issues", "awaiting-review"] }, []],
    ["Gerrit has a newer patchset", { patchset_status: "outdated", attention_reasons: ["outdated-patchset", "awaiting-review"] }, []],
    ["only resolved comments", { attention_reasons: ["awaiting-review"] }, [published(3, { resolved: true })]],
    ["open comment on an older patchset", { comments_unresolved: 1, attention_reasons: ["awaiting-review", "unresolved-comments"] }, [published(1)]],
    ["open comment and a negative vote", { code_review: -1, comments_unresolved: 1, attention_reasons: ["review-issues", "unresolved-comments"] }, [published(3)]],
    // A patchset-level comment is listed as "whole change", and it is open all the same.
    ["open patchset-level comment", { comments_unresolved: 1, attention_reasons: ["awaiting-review", "unresolved-comments"] }, [published(3, { path: "/PATCHSET_LEVEL", line: null })]],
    // Your published reply is the last word: open, but it waits on the reviewer, not on you.
    [
      "your reply is the last word",
      { comments_unresolved: 1, comments_awaiting_reply: 1, attention_reasons: ["awaiting-review", "awaiting-reply"] },
      [published(3, { turn: "theirs", comments: [comment("Rename this.", { patch_set: 3 }), comment("Why?", { patch_set: 3, author: "you" })] })],
    ],
    // Your unpublished draft is yours to finish, not a comment waiting on you; Gerrit does not count it.
    [
      "only your draft",
      { attention_reasons: ["awaiting-review"] },
      [published(3, { comments: [comment("Maybe rename.", { patch_set: 3, state: "draft", author: undefined })] })],
    ],
  ];
  for (const [name, extra, chains] of cases) {
    const vm = changeVm(state({ shown: shown(extra), chains }));
    const listsOpen = (vm.comments?.threads ?? []).some((row) => row.open && !row.waiting);
    assert.equal(vm.head?.icon === "comment-unresolved", listsOpen, `${name}: Change view head`);
    // Review chains draws a change and its chain from the same attention reasons.
    assert.equal(attentionIcon(attended(extra.attention_reasons ?? [])).icon === "comment-unresolved", listsOpen, `${name}: Review chains row`);
    assert.equal(attentionIcon(attended(extra.attention_reasons ?? [])).icon, vm.head?.icon, `${name}: both views draw the same icon`);
  }
});

test("a comment chain whose last published word is yours waits on a reply: a plain bubble, not a call to act", () => {
  const theirs = chain("asked", [comment("Rename this."), comment("Why?", { author: "you" })], { turn: "theirs" });
  const [row] = changeVm(state({ chains: [theirs] })).comments?.threads ?? [];
  assert.deepEqual([row.icon, row.tone, row.open, row.waiting, row.notes[0]], ["comment", "dim", true, true, "waits on a reply"]);
  const [mine] = changeVm(state({ chains: [{ ...theirs, turn: "yours" }] })).comments?.threads ?? [];
  assert.deepEqual([mine.icon, mine.waiting], ["comment-unresolved", false]);
  const badge = (extra: Partial<StackCommit>) => badges(shown(extra))[0];
  const waiting = badge({ comments_unresolved: 2, comments_awaiting_reply: 2 });
  assert.deepEqual([waiting?.icon, waiting?.tone, waiting?.title], ["comment", "plain", "2 comment chains open · 2 waiting on a reply"]);
  const open = badge({ comments_unresolved: 2, comments_awaiting_reply: 1, comments_total: 5 });
  assert.deepEqual([open?.icon, open?.tone, open?.text, open?.title], ["comment-unresolved", "warn", "2", "2 of 5 comment chains open · 1 to answer · 1 waiting on a reply"]);
  assert.equal(reasonWords(["awaiting-reply"]), "comments wait on a reply");
});

test("a row shows its comments as a share, blue while drafts of yours wait (even with some left to answer), yellow while one waits for your answer", () => {
  const badge = (extra: Partial<StackCommit>, drafts = 0) => badges(shown(extra), drafts)[0];
  const done = badge({ comments_unresolved: 3, comments_drafted: 3, comments_to_answer: 0, comments_total: 4, attention_reasons: ["drafts-to-publish"] });
  assert.deepEqual([done?.icon, done?.tone, done?.text, done?.title], ["comment", "info", "3", "3 of 4 comment chains open · 3 answered by your drafts"]);
  // Drafts of yours lead (the owner's call): blue, dashed; what is left to answer is said in the tooltip.
  const mixed = badge({ comments_unresolved: 3, comments_drafted: 1, comments_to_answer: 2 });
  assert.deepEqual([mixed?.icon, mixed?.tone, mixed?.text, mixed?.title, mixed?.ring?.shape], ["comment", "info", "3", "3 comment chains open · 2 to answer · 1 answered by your drafts", "dashed"]);
  const open = badge({ comments_unresolved: 3, comments_drafted: 0, comments_to_answer: 3 });
  assert.deepEqual([open?.icon, open?.tone, open?.ring?.shape], ["comment-unresolved", "warn", "solid"]);
  // All resolved: a plain bubble with its share; drafts of yours on it (known to the Change view) turn it blue.
  assert.deepEqual([badge({ comments_unresolved: 0, comments_total: 2 })?.tone, badge({ comments_unresolved: 0, comments_total: 2 })?.title], ["plain", "All 2 comment chains resolved"]);
  assert.deepEqual([badge({ comments_unresolved: 0, comments_total: 0 }, 1)?.tone, badge({ comments_unresolved: 0, comments_total: 0 }, 1)?.title], ["info", "A draft of yours not published"]);
  assert.deepEqual([badge({ comments_unresolved: 0, comments_total: null })?.text, badge({ comments_unresolved: 0, comments_total: null })?.title], ["", "Every comment chain resolved"]);
  // None at all, or a ger serve before API 1.5 that does not count them: no badge for nothing.
  assert.equal(badge({ comments_unresolved: 0, comments_total: 0 }), null);
  assert.equal(badge({ comments_unresolved: 0 }), null);
  // A ger serve before API 1.4 sends no drafted count: every chain not waiting on a reply is left to answer.
  assert.equal(badge({ comments_unresolved: 3, comments_awaiting_reply: 1 })?.title, "3 comment chains open · 2 to answer · 1 waiting on a reply");
  assert.equal(votes({ verified: 1, code_review: null, comments_unresolved: 3, comments_drafted: 1, comments_to_answer: 2 }), "V+1 💬3 open ✎1");
  assert.equal(votes({ verified: null, code_review: null, comments_unresolved: 3, comments_drafted: 3, comments_to_answer: 0, comments_total: 5 }), "💬3 open ✎3");
  assert.equal(reasonWords(["drafts-to-publish"]), "drafts to publish with the push");
});

test("the comment share cannot be misread: the open count, a ring for the share, a shape per state", () => {
  const state = (extra: Partial<StackCommit>, drafts = 0) => commentState({ comments_unresolved: 0, ...extra }, drafts);
  // 3 open of 3 is not "all done": the number is what is open, the ring full.
  const all = state({ comments_unresolved: 3, comments_to_answer: 3, comments_total: 3 });
  assert.deepEqual([all?.text, all?.label, all?.share, all?.shape, all?.words], ["3", "3 open", 1, "solid", "3 of 3 comment chains open · 3 to answer"]);
  const drafts = state({ comments_unresolved: 3, comments_drafted: 2, comments_to_answer: 1, comments_total: 5 });
  assert.deepEqual([drafts?.tone, drafts?.share, drafts?.words], ["draft", 0.6, "3 of 5 comment chains open · 1 to answer · 2 answered by your drafts"]);
  assert.deepEqual([state({ comments_unresolved: 2, comments_drafted: 2, comments_to_answer: 0, comments_total: 4 })?.shape], ["dashed"]);
  assert.deepEqual([state({ comments_unresolved: 1, comments_awaiting_reply: 1, comments_total: 4 })?.shape], ["thin"]);
  const done = state({ comments_total: 4 });
  assert.deepEqual([done?.text, done?.label, done?.share, done?.shape], ["", "all resolved", 0, "tick"]);
  // The webview draws the ring from the badge; a text row says it in words.
  assert.deepEqual(badges(shown({ comments_unresolved: 3, comments_to_answer: 3, comments_total: 3 }))[0]?.ring, { share: 1, shape: "solid" });
  assert.equal(votes({ verified: null, code_review: null, comments_unresolved: 0, comments_total: 4 }), "no votes");
});

test("what is left to answer: not a chain your draft answers, nor one waiting on a reply, nor your own", () => {
  const chains = [
    chain("open", [comment("Please rename this.")], { turn: "yours" }),
    chain("answered", [comment("Typo."), comment("Done", { state: "draft", author: undefined })], { turn: "draft", resolved_after_publish: true }),
    chain("waiting", [comment("Why?"), comment("Because.", { author: "Me" })], { turn: "theirs" }),
    chain("mine", [comment("Check the docs.", { state: "note", author: undefined })]),
    chain("closed", [comment("Nit.")], { resolved: true }),
  ];
  assert.deepEqual(chains.filter(toAnswer).map((c) => c.root_id), ["open"]);
  assert.deepEqual(chains.filter(answeredByDraft).map((c) => c.root_id), ["answered"]);
});

test("Work on Next: on from the stop to the next commit that needs edits, else the walk's start, else nothing", () => {
  // Oldest first, as GET /stack sends them.
  const bottom = commit("Bottom", { needs_edit: true, rebase: "done" });
  const stopped = commit("Stopped", { needs_edit: true, rebase: "current" });
  const middle = commit("Middle", { needs_edit: false, rebase: "todo" });
  const top = commit("Top", { needs_edit: true, rebase: "todo" });
  const after = commit("After", { needs_edit: true, rebase: "todo" });
  assert.deepEqual(workOnNext(stack([bottom, stopped, middle, top, after], { rebase_in_progress: true })), { kind: "continue", next: top });
  assert.deepEqual(workOnNext(stack([bottom, stopped, middle], { rebase_in_progress: true })), { kind: "continue", next: undefined });
  const resting = (needs: number) => stack([commit("A")], { summary: { commits: 1, ready: 0, attention: 0, needs_edit: needs, not_on_gerrit: 0 } });
  assert.deepEqual([workOnNext(resting(2)).kind, workOnNext(resting(0)).kind], ["start", "nothing"]);
});

test("a thread of your local commit says where its lines are in your file now, or that they changed", () => {
  const threads = changeVm(
    state({
      chains: [
        chain("moved", [comment("Sort a copy.")], { line: 11, worktree: { start_line: 13, line: 13, outdated: false } }),
        chain("gone", [comment("Typo.")], { line: 4, worktree: { start_line: 3, line: 3, outdated: true } }),
        chain("same", [comment("Nit.")], { line: 4, worktree: { start_line: 4, line: 4, outdated: false } }),
      ],
    }),
  ).comments?.threads;
  assert.deepEqual(
    threads?.map((row) => row.notes),
    [
      ["a.py:11", "line 13 in your file", "Alice"],
      ["a.py:4", "outdated: changed since", "Alice"],
      ["a.py:4", "Alice"],
    ],
  );
});

test("a fixup row names the commit it folds into, and that commit how many wait for it", () => {
  const target = commit("Add median", { number: 2168 });
  const fix = commit("fixup! Add median", { fixup_of: target.sha, change_id: null, pushed: false });
  const vm = stackVm(stack([target, commit("Other"), fix, { ...fix, sha: "f".repeat(40), short_sha: "fffffff" }]), undefined, never);
  const notes = (summary: string) => vm.rows?.filter((row) => row.summary === summary).map((row) => row.notes);
  assert.deepEqual(notes("Add median"), [[target.short_sha, "2 fixups pending"]]);
  // Newest first: the second fixup, then the first.
  assert.deepEqual(notes("fixup! Add median"), [
    ["fffffff", "→ 2168"],
    [fix.short_sha, "→ 2168"],
  ]);
  assert.deepEqual(notes("Other"), [[commit("Other").short_sha]]);
});

test("the reply box names whom it answers only where that is sure: the chain gone to, else the one person all answer", () => {
  // Change 2176 in the playground: one comment, by Alice. A box left saying Bob from another change named the wrong person.
  const alice = chain("a", [comment("Population or sample standard deviation?", { author: "pg-alice" })]);
  const bob = chain("b", [comment("Why?", { author: "pg-alice" }), comment("Because.", { author: "pg-bob" })]);
  const mine = chain("m", [comment("A note", { state: "note", author: undefined, comment_id: "n" })]);
  assert.equal(replyName([alice, mine]), "pg-alice", "every chain that can be answered answers Alice");
  assert.equal(replyName([alice, bob]), undefined, "two people: no name, rather than the wrong one");
  assert.equal(replyName([alice, bob], bob), "pg-bob", "the chain gone to, or the cursor is in: its last word on Gerrit");
  assert.equal(replyName([bob], { ...bob, comments: [...bob.comments, comment("Draft", { state: "draft", author: undefined, comment_id: "d" })] }), "pg-bob", "your draft is not who it answers");
  assert.equal(replyName([]), undefined);
});
