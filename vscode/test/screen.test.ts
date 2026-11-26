// One primary button on screen, and it is the next step (Q2; vscode/README.md, "Next: what it names,
// and when"). Every gallery scenario is drawn by the real pages on the stand-in DOM and its primary
// buttons counted; changeScreen.ts, which puts Next beside the task header's and the change's own
// buttons, is checked for what it makes primary.

import assert from "node:assert/strict";
import { test } from "node:test";

import { changeVm } from "../src/changeRows";
import { changeScreen } from "../src/changeScreen";
import { FakeElement, fakePage } from "./fakePage";
import { scenarios } from "./gallery/scenarios";
import { shown, state } from "./fixtures";
import { TaskVm } from "../src/taskRows";

/** The filled buttons a user sees: `.button` not `.secondary`, neither disabled nor hidden. */
function primaries(root: FakeElement): string[] {
  const visible = (element: FakeElement) => {
    for (let at: FakeElement | null = element; at; at = at.parent) {
      if (at.hidden) {
        return false;
      }
    }
    return true;
  };
  return root
    .all((element) => element.tagName === "BUTTON" && element.classList.contains("button") && !element.classList.contains("secondary"))
    .filter((element) => element.getAttribute("disabled") === null && visible(element))
    .map((element) => element.textContent);
}

test("no view of any scenario draws two primary buttons", () => {
  const twice: string[] = [];
  for (const scenario of scenarios()) {
    for (const [view, model] of [
      ["stack", scenario.stack],
      ["change", scenario.change],
    ] as const) {
      if (!model) {
        continue;
      }
      const found = primaries(fakePage(view, model).root);
      if (found.length > 1) {
        twice.push(`${scenario.name} (${view}): ${found.join(", ")}`);
      }
    }
  }
  assert.deepEqual(twice, [], "one primary button on screen: the next step; draw the others plain");
});

test("the task scenarios name their next step on the one primary button", () => {
  const named = (name: string) => {
    const scenario = scenarios().find((s) => s.name === name);
    assert.ok(scenario?.change, name);
    return primaries(fakePage("change", scenario.change).root);
  };
  assert.deepEqual(named("Task: reworking, stopped on a conflict"), ["Resolve src/routes.ts"]);
  assert.deepEqual(named("Task: reworking, the conflicts resolved"), ["Continue"], "the header's Continue, not a second one");
  assert.deepEqual(named("Task: reworking, stopped to edit"), ["Work on next"]);
  assert.deepEqual(named("Task: reworking, nothing left"), ["Continue"]);
  assert.deepEqual(named("Task: reviewing, Next is Update to patchset 6"), ["Update to patchset 6"]);
  assert.deepEqual(named("Task: reviewing, the last change of the chain after the vote"), ["Leave review"]);
  assert.deepEqual(named("Task: reworking, Continue running in the terminal"), [], "the step before it is still running");
});

const reworkTask = (continueNotNow?: string): TaskVm => ({
  mode: "rework",
  heading: "Reworking your chain",
  title: "t",
  position: "change 1 of 1",
  left: "",
  steps: [],
  views: { label: "All chains", detail: "", command: "ger.showAllChains" },
  actions: [
    { label: "Continue", detail: "", command: "ger.rebase.continue", ...(continueNotNow ? { notNow: continueNotNow } : {}) },
    { label: "Abort", detail: "", command: "ger.rebase.abort" },
  ],
});

test("Next that a button on screen already does makes that button the primary one, and is not drawn twice", () => {
  const vm = changeVm(state({}));
  const resolved = changeScreen(vm, { task: reworkTask(), next: { kind: "continue", label: "Continue", title: "" } });
  assert.equal(resolved.next, undefined);
  assert.deepEqual(resolved.task?.actions.map((action) => Boolean(action.primary)), [true, false]);
  // The note under a staged review behind Gerrit has Update to patchset N: Next names it, the note keeps it as the primary.
  const behind = changeVm(state({ shown: shown({ patchset_status: "outdated" }), reviewing: { behind: true, newest: 6 } }));
  const update = changeScreen(behind, { next: { kind: "update", label: "Update to patchset 6", title: "" } });
  assert.equal(update.next, undefined);
  assert.equal(update.notes?.[0].actions?.[0].primary, true);
});

test("with Next drawn, every other button is plain; reworking, the head holds no rebase buttons", () => {
  const stopped = changeVm(state({ stopped: { stop: "resolved", unmerged: [], actions: ["continue", "abort"] } }));
  assert.equal(stopped.head?.actions?.[0].primary, true, "the change's own view makes Continue the one to press");
  const elsewhere = changeScreen(stopped, { next: { kind: "comment", label: "Next comment", title: "" } });
  assert.deepEqual(elsewhere.head?.actions?.map((action) => Boolean(action.primary)), [false, false]);
  assert.equal(elsewhere.next?.label, "Next comment");
  const reworking = changeScreen(stopped, { task: reworkTask("x is still conflicted"), next: { kind: "conflict", label: "Resolve x", title: "" } });
  assert.equal(reworking.head?.actions, undefined, "the task header holds them");
  assert.deepEqual(reworking.task?.actions.map((action) => Boolean(action.primary)), [false, false], "Continue refused now is never the primary");
  const busy = changeScreen(stopped, { next: { kind: "comment", label: "Next comment", title: "" }, busy: "Checking out 2156…" });
  assert.equal(busy.next, undefined, "while a step runs, it is said in Next's place");
  const header = { ...reworkTask(), busy: "Continuing the rebase in the ger terminal…" };
  const running = changeScreen(stopped, { task: header, next: { kind: "workOnNext", label: "Work on next", title: "" } });
  assert.equal(running.next, undefined, "Continue runs in the terminal: no Next over it");
  assert.equal(running.task?.busy, "Continuing the rebase in the ger terminal…");
  const checkout = changeScreen(stopped, { task: { ...reworkTask(), busy: "Checking out 2156…" }, busy: "Checking out 2156… Go to Definition once it is checked out" });
  assert.equal(checkout.task?.busy, undefined, "said once, in Next's place");
});
