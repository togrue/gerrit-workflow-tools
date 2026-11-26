// When Gerrit cannot be asked, or has not answered yet: the views say it as unknown, with the
// reason and the ways on, and draw what git alone knows meanwhile (JUDGEMENT-01-12 W3, W4, W5, N4).

import assert from "node:assert/strict";
import { test } from "node:test";

import { gerritAway, gerritProblem, problemLines } from "../src/problemRows";
import { stackLines, stackVm } from "../src/stackRows";
import { waitingFor } from "../src/state";
import { commit, never, stack } from "./fixtures";

const NO_ANSWER = { code: "GERRIT", message: "Gerrit at 10.255.255.1:8081 does not answer (waited 10 s)", reason: "no-answer" };
const REFUSED = { code: "GERRIT", message: "Gerrit at g.example rejected the credentials (HTTP 401); check ger.user and ger.token, or run `ger setup`", reason: "credentials" };

test("a Gerrit that does not answer offers Retry first, then its address, then the log", () => {
  assert.equal(gerritAway(NO_ANSWER), true);
  assert.deepEqual(problemLines(gerritProblem(NO_ANSWER)), [
    "Gerrit cannot be asked: what it says is unknown, not none.",
    "  Gerrit at 10.255.255.1:8081 does not answer (waited 10 s)",
    "  [**Retry**] [Check the Gerrit Address (ger setup)…] [Show Log]",
  ]);
});

test("refused credentials offer ger setup first: Retry alone would only be refused again", () => {
  assert.deepEqual(gerritProblem(REFUSED).actions.map((action) => [action.label, action.primary ?? false]), [
    ["Check Credentials (ger setup)…", true],
    ["Retry", false],
    ["Show Log", false],
  ]);
});

test("a Gerrit error about one change is not taken for Gerrit being away", () => {
  assert.equal(gerritAway({ code: "GERRIT", message: "Gerrit HTTP 409: change is closed", reason: "change is closed" }), false);
  assert.equal(gerritAway(undefined), false);
});

test("Local stack draws git's stack at once while Gerrit is asked, and says so once", () => {
  const vm = stackVm(stack([commit("One", { patchset_status: "unknown" })], { local_only: true }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ["Asking Gerrit: what it says of each commit shows once it answers"]);
  assert.equal(vm.rows.length, 1);
  assert.equal(vm.waiting, undefined);
});

test("Local stack says once why every commit's Gerrit state is unknown, with Retry", () => {
  const vm = stackVm(stack([commit("One", { patchset_status: "unknown" })], { gerrit_unavailable: NO_ANSWER }), undefined, never);
  assert.deepEqual(stackLines(vm).slice(0, 1), ["Gerrit state unknown  (Gerrit at 10.255.255.1:8081 does not answer (waited 10 s))  [Retry]"]);
});

test("while ger serve runs and Gerrit is asked, the views say that, not that ger serve is starting", () => {
  assert.deepEqual(waitingFor("asking", "A change", undefined, undefined, 10), { text: "Asking Gerrit… If it does not answer within seconds, the views say so." });
});
