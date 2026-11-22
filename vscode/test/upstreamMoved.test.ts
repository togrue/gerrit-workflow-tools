// Local stack's row for an upstream that moved on (#60): what core's `upstream` says, drawn; the
// button runs the rebase it previewed.

import assert from "node:assert/strict";
import { test } from "node:test";

import { UpstreamMoved } from "../src/api";
import { UPSTREAM_WORDS, stackLines, stackVm, upstreamRow } from "../src/stackRows";
import { commit, never, stack } from "./fixtures";

const commits = [commit("Refactor parser init"), commit("Extract command routing")];
const moved = (extra: Partial<UpstreamMoved> = {}): UpstreamMoved => ({ name: "origin/main", behind: 1, conflict: null, preview: true, ...extra });

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
  const conflict = { sha: commits[1].sha, summary: "Extract command routing", files: ["b.txt", "c.txt"] };
  const vm = stackVm(stack(commits, { upstream: moved({ conflict }) }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ['origin/main has 1 new commit — "Extract command routing" would conflict in b.txt, c.txt  [Rebase onto origin/main]']);
  assert.equal(vm.info[0].tone, "warn");
  assert.match(vm.info[0].tooltip ?? "", /the rebase stops there/);
});

test("where the preview could not tell, the row says nothing of conflicts, and the tooltip says why", () => {
  const row = upstreamRow(stack(commits, { upstream: moved({ preview: false }) }));
  assert.equal(row?.detail, undefined);
  assert.match(row?.tooltip ?? "", /cannot be told here/);
});

test("with no local commits the row still offers the rebase, with nothing to preview", () => {
  const vm = stackVm(stack([], { upstream: moved() }), undefined, never);
  assert.deepEqual(stackLines(vm), ["origin/main has 1 new commit  [Rebase onto origin/main]", "No local commits — feature is at origin/main"]);
});

test("during a rebase core serves no upstream: the banner leads alone", () => {
  const rebase = { branch: "feature", onto: null, orig_head: null, stop: "conflict" as const, applied: false, current: null, unmerged: [], as_before: false, actions: ["abort" as const], refused: {} };
  const vm = stackVm(stack(commits, { rebase_in_progress: true, rebase, upstream: null }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ["A rebase stopped on a conflict  [Abort]"]);
  assert.ok(!stackLines(vm).some((line) => line.includes("new commit")));
});
