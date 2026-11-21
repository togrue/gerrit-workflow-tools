// The Change view on a click: drawn at once from what the clicked row knows, filled in answer by
// answer, and never overwritten by an answer for a change selected before (changeLoad.ts).

import assert from "node:assert/strict";
import { test } from "node:test";

import { Diff, Member, ShownCommit, StackCommit } from "../src/api";
import { ChangeDetail, memberStatus, memberTarget, sameChange } from "../src/changeLoad";
import { ChangeState, changeHeading, changeLines, changeVm } from "../src/changeRows";
import { ChangeTarget } from "../src/model";
import { badges } from "../src/stackRows";
import { attended, stack } from "./fixtures";

function row(summary: string, extra: Partial<StackCommit> = {}): StackCommit {
  const sha = summary.toLowerCase().replace(/\W/g, "").padEnd(40, "0");
  return {
    sha,
    short_sha: sha.slice(0, 7),
    change_id: `I${sha}`,
    summary,
    pushed: true,
    patchset_status: "active",
    verified: 1,
    code_review: 2,
    comments_unresolved: 1,
    ci_failures: [],
    ci_links: [],
    gerrit_url: "https://g/c/7",
    number: 7,
    last_pushed_votes: null,
    submittable: false,
    work_in_progress: false,
    change_status: "NEW",
    updated: null,
    abandoned: false,
    reviewers: [],
    is_head: true,
    held_back: false,
    needs_edit: false,
    ...extra,
    ...attended(extra.attention_reasons ?? ["unresolved-comments"]),
  };
}

/** What a click on a Local stack row opens (stackView.commitTarget). */
function target(commit: StackCommit): ChangeTarget {
  return { rev: commit.sha, title: commit.summary, number: commit.number ?? undefined, url: commit.gerrit_url, local: true, changeId: commit.change_id, sha: commit.sha, known: commit };
}

/** `/show`'s answer for `commit`, its files with it. */
function answer(commit: StackCommit): ShownCommit {
  const chain = { root_id: "c1", path: "a.py", line: 3, resolved: false, comments: [{ comment_id: "c1", author: "Ann", message: "Rename this", state: "published" }] };
  return { ...commit, local_commit: true, comment_chains: [chain] as ShownCommit["comment_chains"], new_comment: "draft" };
}

function files(subject: string): Diff {
  return {
    commit: "c".repeat(40),
    base: "b".repeat(40),
    message: `${subject}\n\nChange-Id: I1`,
    author: "Me <me@example.com>",
    local_commit: true,
    files: [{ path: "a.py", status: "M", old_path: null, insertions: 2, deletions: 1 }],
  };
}

/** The view as ChangeView draws it, looked at against its base. */
function drawn(detail: ChangeDetail, extra: Partial<ChangeState> = {}) {
  const state = detail.state({ chains: detail.shown?.comment_chains ?? [], patchsets: undefined, version: "local", mode: "base", readOnly: "", ci: { state: "closed" }, ...extra });
  assert.ok(state, "something is drawn");
  return changeVm(state);
}

const onScreen = { viewVisible: true, firstLineVisible: true };

test("a click draws the Change view at once from the Local stack row: what it needs, its votes, the rest loading", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  detail.select(target(commit), false);
  const vm = drawn(detail);
  assert.equal(vm.head?.text, "Unresolved comments");
  assert.deepEqual(vm.head?.badges, badges(commit).filter(Boolean), "the badges the row wears");
  assert.equal(vm.message, undefined);
  assert.deepEqual(vm.loading, { message: true, readOnly: "" }, "the message block's place, its read-only note included");
  assert.deepEqual([vm.files?.note, vm.comments?.summary], ["loading…", "loading…"]);
  assert.equal(vm.key, commit.change_id, "the page keeps what you opened per change from the first frame on");
  assert.deepEqual(changeLines(vm), [
    "Unresolved comments — V+1 CR+2 💬1",
    "Commit message — loading…",
    "Showing — Local commit extract · Against its base commit",
    "Files — loading…",
    "Comments — loading…",
  ]);
  // No message is drawn yet: the subject is in the title bar, once.
  assert.deepEqual(changeHeading(7, detail.commit()?.summary, vm, onScreen), { title: "Change 7", description: "Extract routing" });
});

test("a Review chains row draws its votes and attention at once, and does not guess whether it is ready to submit", () => {
  const member: Member = { number: 12, change_id: "I12", subject: "Add cache", verified: 1, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: "https://g/c/12", local_sha: null, sha: "f".repeat(40) };
  const detail = new ChangeDetail();
  detail.select({ rev: "change:12", title: "12: Add cache", number: 12, local: false, known: memberStatus(member) }, false);
  const vm = drawn(detail, { version: "current" });
  assert.equal(vm.head?.text, "Loading…", "the row does not say whether Gerrit would submit it");
  assert.deepEqual(vm.head?.badges.map((badge) => badge.kind), ["ci"]);
  assert.equal(changeHeading(12, detail.commit()?.summary, vm, onScreen).description, "Add cache", "the subject, not the row's `12: Add cache`");
});

test("a Review chains member that is a commit of your stack is drawn from that commit, ahead of Gerrit as it is", () => {
  const mine = row("Add cache", { patchset_status: "newer", number: 12, attention_reasons: ["ahead-of-gerrit"] });
  const member: Member = { number: 12, change_id: mine.change_id ?? "", subject: "Add cache", verified: 1, code_review: 2, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: "https://g/c/12", local_sha: mine.sha, sha: "f".repeat(40) };
  const chain = { key: "k", members: [member] } as unknown as Parameters<typeof memberTarget>[1];
  const opened = memberTarget(member, chain, stack([mine]));
  assert.equal(opened.known, mine, "what ger serve says of the commit, not a guess from the row");
  const detail = new ChangeDetail();
  detail.select(opened, false);
  assert.equal(drawn(detail).notes?.[0]?.text, "This commit is ahead of Gerrit");
  // Without the stack (it could not be read) nothing is guessed: the view waits for /show.
  assert.equal(memberTarget(member, chain, undefined).known, undefined);
  assert.equal(memberTarget({ ...member, local_sha: null }, chain, undefined).known?.sha, member.sha, "a change only on Gerrit is drawn from its row");
});

test("each answer fills in its part as it comes: the commit with its files first, the review delta later", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  const token = detail.select(target(commit), false);
  assert.ok(detail.arrive(token, "commit", answer(commit)));
  assert.ok(detail.arrive(token, "base", files("Extract routing")));
  const vm = drawn(detail);
  assert.equal(vm.message?.text, "Extract routing\n\nChange-Id: I1");
  assert.equal(vm.loading, undefined, "the message is drawn: no placeholder");
  assert.deepEqual([vm.files?.summary, vm.comments?.summary, vm.comments?.threads.map((t) => t.id)], ["1 file", "1 unresolved of 1", ["c1"]]);
  assert.deepEqual(detail.loading(), { commit: false, base: false, delta: true, ready: true }, "the delta has not come yet");
  assert.equal(vm.key, commit.change_id, "the same change as before the message came: nothing the page kept is reset");
  // The title keeps its number; the subject leaves it once the message's first line is on screen, as before.
  assert.deepEqual(changeHeading(7, detail.commit()?.summary, vm, onScreen), { title: "Change 7", description: undefined });
  assert.ok(detail.arrive(token, "delta", undefined));
  assert.equal(detail.loading(), undefined);
});

test("an answer for a change selected before never overwrites the change selected now", () => {
  const [first, second] = [row("Extract routing", { number: 7 }), row("Add cache", { number: 8, attention_reasons: [], comments_unresolved: 0 })];
  const detail = new ChangeDetail();
  const old = detail.select(target(first), false);
  const now = detail.select(target(second), false);
  assert.equal(detail.arrive(old, "commit", answer(first)), false);
  assert.equal(detail.arrive(old, "base", files("Extract routing")), false);
  assert.equal(detail.arrive(old, "delta", files("Extract routing")), false);
  assert.equal(detail.fail(old, "change 7 is gone"), false);
  assert.deepEqual([detail.shown, detail.diff, detail.delta, detail.problem], [undefined, undefined, undefined, undefined]);
  const vm = drawn(detail);
  assert.deepEqual([vm.key, vm.head?.text, vm.comments?.summary], [second.change_id, "Nothing to do", "loading…"], "still the second change, from its row");

  assert.ok(detail.arrive(now, "commit", answer(second)));
  assert.equal(detail.arrive(old, "commit", answer(first)), false, "nor after the right one came");
  assert.equal(detail.shown?.summary, "Add cache");
  detail.clear();
  assert.equal(detail.arrive(now, "base", files("Add cache")), false, "closed: nothing is shown any more");
  assert.equal(detail.state({ chains: [], patchsets: undefined, version: "local", mode: "base", readOnly: "", ci: { state: "closed" } }), undefined);
});

test("a reload keeps what is shown until its answers replace it", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  const token = detail.select(target(commit), false);
  detail.arrive(token, "commit", answer(commit));
  detail.arrive(token, "base", files("Extract routing"));
  detail.arrive(token, "delta", undefined);
  const again = detail.select(target(commit), true);
  assert.equal(drawn(detail).message?.text, "Extract routing\n\nChange-Id: I1", "no loading frame in between");
  assert.equal(detail.loading(), undefined);
  assert.ok(detail.arrive(again, "base", files("Extract routing, reworded")));
  assert.equal(drawn(detail).message?.text, "Extract routing, reworded\n\nChange-Id: I1");
});

test("clicking the change already shown keeps what is drawn: a new target naming the same change draws no loading frame", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  const token = detail.select(target(commit), false);
  detail.arrive(token, "commit", answer(commit));
  detail.arrive(token, "base", files("Extract routing"));
  detail.arrive(token, "delta", undefined);
  // Every click builds a new target: Review chains, the threads' head, a headline.
  assert.ok(sameChange(target(commit), target(commit)));
  detail.select(target(commit), false);
  const vm = drawn(detail);
  assert.equal(vm.message?.text, "Extract routing\n\nChange-Id: I1", "the message stays drawn");
  assert.equal(vm.loading, undefined);
  assert.equal(changeHeading(7, detail.commit()?.summary, vm, onScreen).description, undefined, "the subject does not come back into the title bar");

  // Another change, or the same change seen from the other side (Gerrit's, not your commit), starts over.
  assert.equal(sameChange(target(commit), { ...target(commit), local: false }), false);
  detail.select({ ...target(commit), local: false, rev: "change:7" }, false);
  assert.equal(drawn(detail, { version: "current" }).message, undefined);
});

test("a part that cannot be had clears its loading mark and says so, while the others are drawn", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  const token = detail.select(target(commit), false);
  assert.ok(detail.arrive(token, "base", undefined), "the patchset's /diff failed");
  assert.ok(detail.arrive(token, "commit", answer(commit)));
  const vm = drawn(detail);
  assert.equal(vm.loading, undefined, "no message placeholder left");
  assert.equal(vm.files?.note, "the commit is not available here");
  assert.equal(vm.comments?.summary, "1 unresolved of 1");
  assert.deepEqual(detail.loading(), { commit: false, base: false, delta: true, ready: true });
});

test("a /show failure stays shown when a late answer of another part comes", () => {
  const commit = row("Extract routing");
  const detail = new ChangeDetail();
  const token = detail.select(target(commit), false);
  assert.ok(detail.fail(token, "change 7 is gone"));
  assert.ok(detail.arrive(token, "base", files("Extract routing")));
  assert.equal(detail.loading(), undefined, "the late answer brings no loading mark back");
  assert.equal(detail.state({ chains: [], patchsets: undefined, version: "local", mode: "base", readOnly: "", ci: { state: "closed" } })?.loading, undefined);
  assert.equal(detail.problem, "change 7 is gone");
});

test("answers out of order: the review delta before the commit fills in its hint, the rest still loading", () => {
  const member: Member = { number: 12, change_id: "I12", subject: "Add cache", verified: 1, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: "https://g/c/12", local_sha: null, sha: "f".repeat(40) };
  const detail = new ChangeDetail();
  const token = detail.select({ rev: "change:12", title: "12: Add cache", number: 12, local: false, known: memberStatus(member) }, false);
  const delta = { ...files("Add cache"), since: { patch_set: 1, current_patch_set: 2, up_to_date: false, rebased: false } };
  assert.ok(detail.arrive(token, "delta", delta));
  let vm = drawn(detail, { version: "current" });
  assert.equal(vm.hint?.text, "Since your review: patchset 1 → 2 · 1 file");
  assert.deepEqual([vm.loading, vm.comments?.summary, vm.head?.text], [{ message: true, readOnly: "" }, "loading…", "Loading…"]);
  const shown: ShownCommit = { ...memberStatus(member), submittable: true, local_commit: false, comment_chains: [] };
  assert.ok(detail.arrive(token, "commit", shown));
  assert.ok(detail.arrive(token, "base", files("Add cache")));
  vm = drawn(detail, { version: "current" });
  assert.deepEqual([vm.loading, vm.message?.text, vm.head?.text, vm.hint?.show], [undefined, "Add cache\n\nChange-Id: I1", "Ready to submit", true]);
  assert.equal(detail.loading(), undefined);
});

test("while it is not known whether the change is ready to submit, the head wears a loading icon, not the green check", () => {
  const member: Member = { number: 12, change_id: "I12", subject: "Add cache", verified: 1, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: "https://g/c/12", local_sha: null, sha: "f".repeat(40) };
  const detail = new ChangeDetail();
  const token = detail.select({ rev: "change:12", title: "12: Add cache", number: 12, local: false, known: memberStatus(member) }, false);
  let head = drawn(detail, { version: "current" }).head;
  assert.deepEqual([head?.text, head?.icon, head?.tone], ["Loading…", "loading", undefined]);
  detail.arrive(token, "commit", { ...memberStatus(member), submittable: false, local_commit: false, comment_chains: [] });
  head = drawn(detail, { version: "current" }).head;
  assert.equal(head?.text, "Nothing to do");
  assert.notEqual(head?.icon, "loading");
});
