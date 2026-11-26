// Local stack's row for an upstream that moved on (#60): what core's `upstream` says, drawn; the
// button runs the rebase it previewed.

import assert from "node:assert/strict";
import { test } from "node:test";

import { UpstreamMoved } from "../src/api";
import { UPSTREAM_WORDS, stackLines, stackVm, upstreamRow } from "../src/stackRows";
import { commit, never, stack } from "./fixtures";

const commits = [commit("Refactor parser init"), commit("Extract command routing")];
const moved = (extra: Partial<UpstreamMoved> = {}): UpstreamMoved => ({
  name: "origin/main",
  behind: 1,
  argv: ["rebase", "origin/main"],
  conflict: null,
  preview: true,
  reason: null,
  dropped: [],
  ...extra,
});

test("an upstream that has not moved on, or none at all, has no row", () => {
  assert.equal(upstreamRow(stack(commits)), undefined);
  assert.equal(upstreamRow(stack(commits, { upstream: null })), undefined);
  assert.equal(upstreamRow(stack(commits, { upstream: moved({ behind: 0 }) })), undefined);
});

test("a moved upstream says how many commits it has, and offers the rebase onto it in the ger terminal", () => {
  const row = upstreamRow(stack(commits, { upstream: moved({ behind: 3 }) }));
  assert.equal(row?.text, "origin/main has 3 new commits");
  assert.equal(row?.detail, UPSTREAM_WORDS.clean);
  assert.equal(row?.tone, "plain");
  assert.deepEqual(
    row?.actions?.map((a) => [a.label, a.command.id]),
    [["Rebase onto origin/main", "ger.rebase.upstream"]],
  );
  assert.match(row?.actions?.[0].title ?? "", /^ger rebase origin\/main, in the ger terminal/);
  assert.match(row?.tooltip ?? "", /Every commit applies on it as it is now/);
});

test("the conflict preview names the commit the rebase would stop at and its files", () => {
  const conflict = { sha: commits[1].sha, summary: "Extract command routing", files: ["b.txt", "c.txt"], kind: "conflict" as const };
  const vm = stackVm(stack(commits, { upstream: moved({ conflict }) }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ['origin/main has 1 new commit — "Extract command routing" would conflict in b.txt, c.txt  [Rebase onto origin/main]']);
  assert.equal(vm.info[0].tone, "warn");
  assert.match(vm.info[0].tooltip ?? "", /the rebase stops there/);
});

test("a commit whose change is already upstream: the rebase stops on it as empty, for you to drop it", () => {
  const conflict = { sha: commits[0].sha, summary: "Refactor parser init", files: [], kind: "empty" as const };
  const vm = stackVm(stack(commits, { upstream: moved({ conflict }) }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ['origin/main has 1 new commit — "Refactor parser init" would become empty: its change is already upstream  [Rebase onto origin/main]']);
  assert.equal(vm.info[0].tone, "warn");
  assert.ok(vm.info[0].tooltip?.includes(UPSTREAM_WORDS.stopsEmpty), vm.info[0].tooltip);
});

test("the button runs core's one command, and the tooltip says what the rebase drops", () => {
  const dropped = [{ sha: commits[0].sha, summary: "Refactor parser init" }];
  const row = upstreamRow(stack(commits, { upstream: moved({ argv: ["rebase", "origin/main", "--drop-merged-equivalent"], dropped }) }));
  assert.match(row?.actions?.[0].title ?? "", /^ger rebase origin\/main --drop-merged-equivalent, in the ger terminal/);
  assert.match(row?.tooltip ?? "", /1 commit is already upstream and will be dropped\./);
});

test("where the preview could not tell, the row says nothing of conflicts, and the tooltip gives core's reason", () => {
  const reason = "a merge in the stack: git rebase does not replay it";
  const row = upstreamRow(stack(commits, { upstream: moved({ preview: false, reason }) }));
  assert.equal(row?.detail, undefined);
  assert.match(row?.tooltip ?? "", /cannot be told here \(a merge in the stack: git rebase does not replay it\)/);
});

test("with no local commits the row still offers the rebase, and the branch is behind it", () => {
  const vm = stackVm(stack([], { upstream: moved() }), undefined, never);
  assert.deepEqual(stackLines(vm), ["origin/main has 1 new commit  [Rebase onto origin/main]", "No local commits — feature is behind origin/main"]);
  const level = stackVm(stack([], { upstream: moved({ behind: 0 }) }), undefined, never);
  // The title says "feature → origin/main" already: the row does not say it again.
  assert.deepEqual(stackLines(level), ["No local commits"]);
});

test("during a rebase core serves no upstream: the banner leads alone", () => {
  const rebase = { branch: "feature", onto: null, orig_head: null, stop: "conflict" as const, applied: false, current: null, unmerged: [], as_before: false, actions: ["abort" as const], refused: {} };
  const vm = stackVm(stack(commits, { rebase_in_progress: true, rebase, upstream: null }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ["A rebase stopped on a conflict  [Abort]"]);
  assert.ok(!stackLines(vm).some((line) => line.includes("new commit")));
});
