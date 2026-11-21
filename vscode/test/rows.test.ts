// The view models of the two webview views: what a row says about a commit (stackRows.ts) and
// what the Change view offers for a change (changeRows.ts). The pages only draw these.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CommentChain, Rebase, StackCommit } from "../src/api";
import { ChangeState, changeHeading, changeLines, changeVm } from "../src/changeRows";
import { attentionIcon } from "../src/format";
import { SYNC_ICONS, badges, frozen, pinned, pinnedWhy, reviewerWords, stackLines, sync, stackVm, stackWhere } from "../src/stackRows";
import { waitingFor } from "../src/state";
import { attended, chain, comment, commit, content, diff, finding, never, patchsets, shown, stack, state } from "./fixtures";

test("the icon says how a commit relates to Gerrit", () => {
  const states = (extra: Partial<StackCommit>) => sync(commit("c", extra));
  assert.equal(states({ patchset_status: "active" }), "same");
  assert.equal(states({ patchset_status: "newer" }), "changed");
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

const REBASE: Rebase = { branch: "feature", onto: "base000", orig_head: "three00", stop: "edit", applied: true, current: "two0000", unmerged: [] };

test("mid-rebase the stack marks where it stands and dims only what is still to be replayed", () => {
  const commits = [commit("One", { rebase: "done" }), commit("Two", { rebase: "current", is_head: true }), commit("Three", { rebase: "todo" })];
  const stopped = stackVm(stack(commits, { branch: null, rebase_in_progress: true, rebase: REBASE }), undefined, never);
  assert.deepEqual(stopped.rows.map((row) => [row.summary, row.marker, row.dim]), [["Three", null, true], ["Two", "rebase", false], ["One", null, false]]);
  assert.match(stopped.frozen ?? "", /rebase is in progress/);
  assert.deepEqual([stopped.info[0].text, stopped.info[0].detail], ["A rebase is in progress", undefined], "the stack is the live one: nothing is as it was");
  assert.equal(stackLines(stopped)[2], "▶ Two — HEAD · two0000 · the same as on Gerrit");
  // On a conflict the commit it stopped at is only partly there: marked, and dim.
  const conflict = stackVm(stack(commits, { rebase_in_progress: true, rebase: { ...REBASE, stop: "conflict", applied: false, unmerged: ["e.txt"] } }), undefined, never);
  assert.deepEqual([conflict.rows[1].marker, conflict.rows[1].dim], ["conflict", true]);
  assert.equal(conflict.info[0].text, "A rebase stopped on a conflict");
  // Resolved and added: no conflict any more, ready to continue.
  const resolved = stackVm(stack(commits, { rebase_in_progress: true, rebase: { ...REBASE, stop: "resolved", applied: false } }), undefined, never);
  assert.deepEqual([resolved.rows[1].marker, resolved.rows[1].dim], ["rebase", false]);
  assert.deepEqual([resolved.info[0].text, resolved.info[0].detail], ["A rebase stopped with its conflicts resolved", "ready to continue"]);
  assert.match(resolved.rows[1].tooltip, /now resolved: git rebase --continue commits it/);
});

test("a rebase that says nothing of its commits shows the branch as it was, and says so", () => {
  const vm = stackVm(stack([commit("One")], { rebase_in_progress: true, rebase: { ...REBASE, stop: "paused", current: null } }), undefined, never);
  assert.equal(vm.info[0].detail, "this is the branch as it was before");
});

test("the header names the branch being rebased", () => {
  assert.equal(stackWhere(stack([commit("One")], { branch: "feature" })), "feature → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null, rebase_in_progress: true, rebase: REBASE })), "feature (rebasing) → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null, rebase_in_progress: true, rebase: { ...REBASE, branch: null } })), "detached (rebasing) → origin/main");
  assert.equal(stackWhere(stack([commit("One")], { branch: null })), "detached → origin/main");
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
});

// --- Change -------------------------------------------------------------------------

test("the change view shows the message, the files with their line counts, and what is compared", () => {
  const vm = changeVm(state());
  assert.deepEqual(vm.message, { key: shown().change_id, text: "Extract routing\n\nChange-Id: I1", author: "Me <me@example.com>", readOnly: "" });
  assert.deepEqual(vm.files?.rows.map((row) => [row.name, row.dir, row.state, row.added, row.deleted]), [["route.py", "src", "modified", 12, 3], ["logo.png", "", "added", null, null]]);
  assert.deepEqual([vm.files?.added, vm.files?.deleted], [12, 3]);
  assert.deepEqual(changeLines(vm), [
    "Nothing to do — no votes",
    "Commit message — Extract routing  (editable)",
    "Showing — Local commit extract · Against its base commit",
    "Files — 2 · +12 −3",
    "  route.py — src · modified +12 −3",
    "  logo.png — added binary",
    "Comments — none",
  ]);
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

test("the version picker starts on the local commit and lists Gerrit's patchsets, newest first", () => {
  const before = changeVm(state()).versions;
  assert.deepEqual(before, { options: [{ value: "local", label: "Local commit extract" }, { value: "current", label: "Gerrit's current patchset" }], selected: "local" });
  const after = changeVm(state({ patchsets })).versions;
  assert.deepEqual(after?.options.map((option) => option.label), ["Local commit extract", "Patchset 3 (current)", "Patchset 2 (you reviewed it)", "Patchset 1"]);
  // Someone's change: no local commit to choose, Gerrit's current patchset is what is shown.
  assert.equal(changeVm(state({ local: false, version: "current", patchsets })).versions?.selected, "3");
  assert.deepEqual(changeVm(state({ shown: shown({ pushed: false }) })).versions?.options.map((option) => option.value), ["local"]);
});

test("each comparison is offered only where it means something", () => {
  const disabled = (extra: Partial<ChangeState>) => changeVm(state(extra)).modes?.options.filter((option) => option.disabled).map((option) => option.value);
  assert.deepEqual(disabled({}), []);
  assert.deepEqual(disabled({ local: false }), ["gerrit"], "Gerrit against local needs a local commit");
  assert.deepEqual(disabled({ shown: shown({ pushed: false }) }), ["reviewed", "gerrit"], "nothing on Gerrit to compare with");
  assert.deepEqual(disabled({ patchsets: { ...patchsets, reviewed: null } }), ["reviewed"], "never reviewed");
});

test("a review delta names the two versions, and says when there is nothing to show", () => {
  const since = { patch_set: 2, current_patch_set: 3, to_patch_set: null, up_to_date: false, identical: false, rebased: false };
  const gerrit = changeVm(state({ mode: "gerrit", delta: diff({ since: { ...since, patch_set: 3 }, files: [] }) })).files;
  assert.deepEqual([gerrit?.title, gerrit?.summary], ["Gerrit vs. local: patchset 3 → local", "0 files"]);
  // Your commit is the patchset itself: core says so, and the view says there is nothing to push.
  const same = changeVm(state({ mode: "gerrit", delta: diff({ since: { ...since, patch_set: 3, identical: true }, files: [] }) })).files;
  assert.deepEqual([same?.title, same?.summary], ["Gerrit vs. local", "identical to patchset 3 — nothing to push"]);
  const reviewed = changeVm(state({ mode: "reviewed", version: 3, delta: diff({ since: { ...since, to_patch_set: 3, rebased: true } }) })).files;
  assert.equal(reviewed?.title, "Since your review: patchset 2 → patchset 3 (rebased)");
  const never = changeVm(state({ mode: "reviewed", delta: diff({ since: { ...since, patch_set: null }, files: [] }) })).files;
  assert.deepEqual([never?.title, never?.note], ["Since your review", "you have not reviewed this change yet"]);
  // Someone's change you reviewed before: what is new is one click away, whatever is compared.
  const hint = changeVm(state({ local: false, version: "current", delta: diff({ since: { ...since, to_patch_set: 3 } }) })).hint;
  assert.deepEqual(hint, { text: "Since your review: patchset 2 → 3 · 2 files", show: true });
  assert.equal(changeVm(state({ delta: diff({ since }) })).hint, undefined, "your own commit has no review of yours to compare with");
});

test("the commit message is read-only with the reason, and comment chains say what can be done with them", () => {
  assert.equal(changeVm(state({ readOnly: "A rebase is in progress: finish or abort it first." })).message?.readOnly, "A rebase is in progress: finish or abort it first.");
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
  assert.equal(vm.comments?.summary, "1 unresolved of 2 · 1 of yours, not published");
  assert.deepEqual(changeLines(vm).slice(-5), [
    "Comments — 1 unresolved of 2 · 1 of yours, not published",
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
    // Your unpublished draft is yours to finish, not a comment waiting on you; Gerrit does not count it.
    [
      "only your draft",
      { attention_reasons: ["awaiting-review"] },
      [published(3, { comments: [comment("Maybe rename.", { patch_set: 3, state: "draft", author: undefined })] })],
    ],
  ];
  for (const [name, extra, chains] of cases) {
    const vm = changeVm(state({ shown: shown(extra), chains }));
    const listsOpen = (vm.comments?.threads ?? []).some((row) => row.open);
    assert.equal(vm.head?.icon === "comment-unresolved", listsOpen, `${name}: Change view head`);
    // Review chains draws a change and its chain from the same attention reasons.
    assert.equal(attentionIcon(attended(extra.attention_reasons ?? [])).icon === "comment-unresolved", listsOpen, `${name}: Review chains row`);
    assert.equal(attentionIcon(attended(extra.attention_reasons ?? [])).icon, vm.head?.icon, `${name}: both views draw the same icon`);
  }
});
