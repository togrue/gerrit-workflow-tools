// The scorecard of a walk: steps joined to the actions, requests and terminal commands in their
// time, the budgets, the Windows estimate.

import assert from "node:assert/strict";
import { test } from "node:test";

import { PROCESS_BUDGET, readTrace, renderScorecard, scorecard } from "./scorecard";

const step = (n: number, title: string, budget: string, t: number, reached: number, extra: object = {}) => ({
  kind: "step",
  loop: n < 3 ? "review" : "rework",
  n,
  title,
  budget,
  t,
  reached_ms: reached,
  idle_ms: reached + 300,
  clicks: 1,
  keys: 0,
  typed: 0,
  switches: 1,
  where: ["Change view"],
  input: "real",
  given: ["click x"],
  picture: `0${n}.png`,
  ...extra,
});

const trace = [
  { kind: "step-start", n: 1, t: 1000 },
  { kind: "action", t: 1010, id: "a1", name: "ger.nextComment", first_ms: 40, first: "cursor:file:a.py", settled_ms: 60 },
  step(1, "Next comment", "walk", 1000, 50),
  { kind: "step-start", n: 2, t: 2000 },
  { kind: "action", t: 2005, id: "a2", name: "stack.command ger.openChange", first_ms: 300, first: "paint:change", settled_ms: 1400 },
  { kind: "request", t: 2010, action: "a2", rid: 7, method: "GET", path: "/show", status: 200, ms: 1200 },
  { kind: "serve", t: 2010, rid: 7, action: "a2", ms: 1190, queued_ms: 0, git: { count: 5, ms: 40 }, gerrit: { count: 2, ms: 1100 }, calls: [["gerrit", "GET changes/1/comments", 900]], calls_left_out: 0 },
  { kind: "request", t: 9000, action: null, rid: 8, method: "GET", path: "/overview", status: 200, ms: 50 },
  step(2, "Show the change", "change", 2000, 900),
  { kind: "step-start", n: 3, t: 5000 },
  { kind: "cli", t: 5100, argv: ["edit", "-n"], exit: 0, ms: 3000, git: { count: 30, ms: 900 }, gerrit: { count: 1, ms: 50 }, calls: [["git", "git rebase", 700]], calls_left_out: 0 },
  step(3, "Work on Next", "slow", 5000, 3500, { input: "command", fallbacks: ["ger.workOnNext (no button)"] }),
  { kind: "step-start", n: 4, t: 20000 },
];

test("each step is joined to what happened in its time, and judged by its budget", () => {
  const card = scorecard(readTrace(trace.map((r) => JSON.stringify(r)).join("\n")), { processMs: 80 });
  const [walk, change, slow] = card.steps;
  assert.deepEqual([walk.first_ms, walk.settled_ms, walk.over], [50, 70, []], "first response and settled from the action, budget kept");
  assert.equal(change.first_ms, 305);
  assert.equal(change.settled_ms, 1405, "the action settled after the walk saw its outcome");
  assert.deepEqual(change.over, ["settled 1405 ms > 1000 ms", "progress after 305 ms > 100 ms"]);
  assert.equal(change.requests, 1, "a request outside the step's time is not its");
  assert.equal(change.subprocesses, 5);
  assert.equal(change.windows_ms, 1405 + 5 * 80);
  assert.match(change.causes[0], /GET \/show 1200 ms \(serve 1190 ms: 5 git, 2 Gerrit; GET changes\/1\/comments 900 ms\)/);
  assert.equal(slow.subprocesses, 30, "a ger command in the terminal counts");
  assert.deepEqual(slow.over, ["progress after 3500 ms > 100 ms"], "nothing but its outcome showed");
  assert.match(slow.causes[0], /^terminal: ger edit -n 3000 ms/);
  assert.deepEqual(
    card.loops.map((l) => [l.loop, l.steps, l.user_actions, l.subprocesses, l.over]),
    [
      ["review", 2, 2, 5, 1],
      ["rework", 1, 1, 30, 1],
    ],
  );
  assert.match(card.notes.join("\n"), /1 step\(s\) started and never finished/);
  const text = renderScorecard(card);
  assert.match(text, /\| 2 \| Show the change \| change \| real \| 1 \(1\/0\/0\) \| 1 \| 305 ms \| 1405 ms \| 1 \| 5 \| 2 \| 1805 ms \| \*\*over\*\* \|/);
  assert.match(text, /## Five slowest steps\n\n- \*\*rework 3\. Work on Next\*\*/);
  assert.doesNotMatch(text, /loop\*\*: \d+ subprocesses/, "both loops within their pin");
});

test("a loop that starts more subprocesses than its pin is over budget", () => {
  const card = scorecard(readTrace(trace.map((r) => JSON.stringify(r)).join("\n")), { processMs: 80 });
  const [review] = card.loops;
  assert.equal(review.process_budget, PROCESS_BUDGET.review);
  const more = { ...card, loops: card.loops.map((l) => (l.loop === "review" ? { ...l, subprocesses: PROCESS_BUDGET.review + 1 } : l)) };
  assert.match(renderScorecard(more), new RegExp(`- \\*\\*review loop\\*\\*: ${PROCESS_BUDGET.review + 1} subprocesses > ${PROCESS_BUDGET.review}`));
});
