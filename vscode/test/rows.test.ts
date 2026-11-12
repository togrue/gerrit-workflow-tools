// The view models of the two webview views: what a row says about a commit (stackRows.ts) and
// what the Change view offers for a change (changeRows.ts). The pages only draw these.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CommentChain, Diff, Patchsets, ShownCommit, Stack, StackCommit } from "../src/api";
import { ChangeState, changeLines, changeVm } from "../src/changeRows";
import { badges, cloud, frozen, stackLines, stackVm } from "../src/stackRows";
import { waitingFor } from "../src/state";

function commit(summary: string, extra: Partial<StackCommit> = {}): StackCommit {
  const sha = summary.toLowerCase().replace(/\W/g, "").padEnd(40, "0");
  return {
    sha,
    short_sha: sha.slice(0, 7),
    change_id: `I${sha}`,
    summary,
    pushed: true,
    patchset_status: "active",
    verified: null,
    code_review: null,
    comments_unresolved: 0,
    ci_failures: [],
    ci_links: [],
    gerrit_url: "https://g/c/1",
    number: 1,
    last_pushed_votes: null,
    submittable: false,
    work_in_progress: false,
    attention_reasons: [],
    change_status: "NEW",
    updated: null,
    abandoned: false,
    reviewers: [],
    is_head: false,
    held_back: false,
    needs_edit: false,
    ...extra,
  };
}

function stack(commits: StackCommit[], extra: Partial<Stack> = {}): Stack {
  return {
    branch: "feature",
    head: commits.at(-1)?.sha ?? null,
    rebase_in_progress: false,
    stack: { project: "p", target_branch: "origin/main", push_branch: "main" },
    summary: { commits: commits.length, ready: 0, attention: 0, needs_edit: 0, not_on_gerrit: 0 },
    commits,
    ...extra,
  };
}

const never = () => undefined;

test("the cloud says how a commit relates to Gerrit", () => {
  const states = (extra: Partial<StackCommit>) => cloud(commit("c", extra));
  assert.equal(states({ patchset_status: "active" }), "same");
  assert.equal(states({ patchset_status: "newer" }), "changed");
  assert.equal(states({ patchset_status: "newer", diverged: true }), "diverged");
  assert.equal(states({ patchset_status: "outdated" }), "outdated");
  assert.equal(states({ patchset_status: "absent", pushed: false }), "not-pushed");
  assert.equal(states({ patchset_status: "merged-same" }), "merged");
  assert.equal(states({ patchset_status: "unknown" }), "unknown");
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

test("the stack shows newest first, with HEAD and what push would hold back", () => {
  const vm = stackVm(stack([commit("One"), commit("Two", { held_back: true }), commit("Three", { is_head: true, patchset_status: "absent", pushed: false, change_id: null })]), undefined, never);
  assert.deepEqual(vm.rows.map((row) => row.summary), ["Three", "Two", "One"]);
  assert.deepEqual(vm.rows[0].notes, ["HEAD", "three00"]);
  assert.equal(vm.rows[0].key, vm.rows[0].sha, "without a Change-Id the commit id names the row");
  assert.deepEqual(vm.rows.map((row) => row.squash), [true, true, false], "the oldest has nothing below to squash into");
  assert.equal(vm.frozen, null);
  assert.deepEqual(stackLines(vm), ["Three — HEAD · three00 · not on Gerrit", "Two — two0000 · held back · the same as on Gerrit", "One — one0000 · the same as on Gerrit"]);
});

test("mid-rebase the stack marks where it stands and dims what is not replayed yet", () => {
  const commits = [commit("One", { rebase: "done" }), commit("Two", { rebase: "current" }), commit("Three", { rebase: "todo" })];
  const stopped = stackVm(stack(commits, { rebase_in_progress: true, rebase: { applied: true } }), undefined, never);
  assert.deepEqual(stopped.rows.map((row) => [row.summary, row.marker, row.dim]), [["Three", null, true], ["Two", "rebase", false], ["One", null, false]]);
  assert.match(stopped.frozen ?? "", /rebase is in progress/);
  assert.equal(stopped.info[0].text, "A rebase is in progress");
  // On a conflict the commit it stopped at is only partly there: marked, and dim.
  const conflict = stackVm(stack(commits, { rebase_in_progress: true, rebase: { applied: false } }), undefined, never);
  assert.deepEqual([conflict.rows[1].marker, conflict.rows[1].dim], ["conflict", true]);
  assert.equal(conflict.info[0].text, "A rebase stopped on a conflict");
  assert.equal(stackLines(conflict)[2], "▶! Two — two0000 · the same as on Gerrit  (dim)");
});

test("a merge commit in the stack is shown, and says why nothing can be moved", () => {
  const withMerge = stack([commit("One"), commit("Merge side", { merge: true })]);
  assert.match(frozen(withMerge) ?? "", /merge commit/);
  assert.deepEqual(stackVm(withMerge, undefined, never).rows[0].notes, ["mergesi", "merge"]);
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

const shown = (extra: Partial<ShownCommit> = {}): ShownCommit => ({ ...commit("Extract routing"), local_commit: true, comment_chains: [], ...extra });

function diff(extra: Partial<Diff> = {}): Diff {
  return {
    commit: "c".repeat(40),
    base: "b".repeat(40),
    message: "Extract routing\n\nChange-Id: I1",
    author: "Me <me@example.com>",
    local_commit: true,
    files: [
      { path: "src/route.py", status: "M", old_path: null, insertions: 12, deletions: 3 },
      { path: "logo.png", status: "A", old_path: null, insertions: null, deletions: null },
    ],
    ...extra,
  };
}

function state(extra: Partial<ChangeState> = {}): ChangeState {
  return { local: true, shown: shown(), chains: [], base: diff(), delta: undefined, patchsets: undefined, version: "local", mode: "base", readOnly: "", ci: { state: "closed" }, ...extra };
}

const patchsets: Patchsets = { number: 1, current: 3, reviewed: 2, patchsets: [1, 2, 3].map((number) => ({ number, commit: String(number).repeat(40), created: null })) };

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
  const since = { patch_set: 2, current_patch_set: 3, to_patch_set: null, up_to_date: false, rebased: false };
  const gerrit = changeVm(state({ mode: "gerrit", delta: diff({ since: { ...since, patch_set: 3 }, files: [] }) })).files;
  assert.deepEqual([gerrit?.title, gerrit?.summary], ["Gerrit vs. local: patchset 3 → local", "0 files"]);
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
  const comment = (message: string, extra: object = {}) => ({ path: "a.py", line: 4, message, patch_set: 1, state: "published" as const, url: null, author: "Alice", ...extra });
  const chain = (root_id: string, comments: ReturnType<typeof comment>[], extra: Partial<CommentChain> = {}): CommentChain => ({
    root_id,
    path: "a.py",
    line: 4,
    start_line: null,
    url: null,
    resolved: false,
    resolved_after_publish: false,
    outdated: false,
    held: false,
    comments,
    ...extra,
  });
  const vm = changeVm(
    state({
      chains: [
        chain("open", [comment("Please rename this."), comment("Renamed it.", { state: "draft", author: undefined })], { held: true }),
        chain("note", [comment("Check the docs.", { state: "note", author: undefined })]),
        chain("done", [comment("Typo.")], { resolved: true }),
      ],
    }),
  );
  const rows = vm.comments?.threads ?? [];
  assert.deepEqual(rows.map((row) => [row.id, row.reply, row.open, row.held, row.icon]), [["open", true, true, true, "person"], ["note", false, false, false, "note"], ["done", true, false, false, "pass"]]);
  assert.deepEqual(rows[0].replies, [{ author: "you", state: "draft", text: "Renamed it." }]);
  assert.equal(vm.comments?.summary, "1 unresolved of 2 · 1 of yours, not published");
  assert.deepEqual(changeLines(vm).slice(-5), [
    "Comments — 1 unresolved of 2 · 1 of yours, not published",
    "  Please rename this. — kept for you · a.py:4 · Alice · 1 reply  [open, kept for you]",
    "    you — [draft] Renamed it.",
    "  Check the docs. — your private note · a.py:4  [yours]",
    "  Typo. — a.py:4 · Alice  [resolved]",
  ]);
});
