// The keyboard reaches everything the pointer does, and the icons say their words to it (state 23:
// the vote badges and the comment ring were mouse-only). Every gallery scenario is drawn by the
// real pages on the stand-in DOM; what answers a click must be a stop of the tab order (a button,
// a link, a field, or `tabindex="0"`) or inside the list, which walks its rows with the arrow
// keys; every badge group with something to say is one stop, named by its words. views.css shows a
// focus ring on all of them.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { stackVm } from "../src/stackRows";
import { FakeElement, fakePage } from "./fakePage";
import { commit, never, stack } from "./fixtures";
import { scenarios } from "./gallery/scenarios";

const NATIVE = new Set(["BUTTON", "A", "SELECT", "TEXTAREA", "INPUT"]);

/** Whether the keyboard reaches `element`: natively, by its tabindex, or as a row of a list that has the focus. */
function reachable(element: FakeElement): boolean {
  if (NATIVE.has(element.tagName) || element.getAttribute("tabindex") === "0") {
    return true;
  }
  return Boolean(element.closest(".list"));
}

/** What answers a click: a listener of its own. */
const clickable = (element: FakeElement) => (element.listeners.get("click") ?? []).length > 0;

test("everything a click answers in any scenario is reachable with the keyboard, and has a name", () => {
  const missed: string[] = [];
  for (const scenario of scenarios()) {
    for (const [view, model] of [
      ["stack", scenario.stack],
      ["change", scenario.change],
    ] as const) {
      if (!model) {
        continue;
      }
      const { root } = fakePage(view, model);
      for (const element of root.all(clickable)) {
        const name = element.getAttribute("aria-label") ?? element.getAttribute("title") ?? element.textContent.trim();
        if (!reachable(element) || !name) {
          missed.push(`${scenario.name} (${view}): ${element.tagName.toLowerCase()} "${name.slice(0, 40)}"`);
        }
      }
      // Each badge group that has something to say is one stop with its words (the list's rove: tabindex 0 or -1).
      for (const group of root.all((element) => element.classList.contains("badges") && element.children.length > 0)) {
        if (group.all((element) => element.classList.contains("badge") && Boolean(element.getAttribute("title"))).length === 0) {
          continue;
        }
        if (group.getAttribute("tabindex") === null || !group.getAttribute("aria-label")) {
          missed.push(`${scenario.name} (${view}): a badge group without a stop or words`);
        }
      }
    }
  }
  assert.deepEqual([...new Set(missed)], []);
});

test("Local stack: Tab reaches the focused row's badges, named by their words, and Enter on them shows the comments", () => {
  const commits = [commit("First", { code_review: 2 }), commit("Second", { comments_unresolved: 2, comments_total: 3 }), commit("Third", { verified: -1 })];
  const page = fakePage("stack", stackVm(stack(commits), undefined, never));
  const stops = () => page.root.querySelectorAll(".badges").filter((group) => group.getAttribute("tabindex") === "0");
  // No row focused yet: the first row's (newest first) is the stop.
  assert.equal(stops().length, 1);
  assert.match(stops()[0].getAttribute("aria-label") ?? "", /CI failed/);
  // The row's own name carries them too, read out while the list has the focus.
  const row = page.root.querySelectorAll(".row")[1];
  assert.match(row.getAttribute("aria-label") ?? "", /^Second\. .*2 of 3 comment chains open/);
  // Walking the list moves the stop along.
  const list = page.root.querySelector(".list") as FakeElement;
  list.dispatch("keydown", { key: "ArrowDown" });
  list.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(stops().length, 1);
  assert.match(stops()[0].getAttribute("aria-label") ?? "", /2 of 3 comment chains open/);
  assert.equal(list.getAttribute("aria-activedescendant"), row.getAttribute("id"));
  // A redraw (a refresh) keeps it.
  page.send({ type: "render", vm: stackVm(stack(commits), undefined, never) });
  assert.equal((page.root.querySelector(".list") as FakeElement).getAttribute("aria-activedescendant"), row.getAttribute("id"));
  stops()[0].dispatch("keydown", { key: "Enter" });
  page.flush();
  assert.ok(page.posted.some((message) => message.type === "comments"), "Enter asks for the comments, as hovering the ring does");
});

test("state 24: a row's Edit Commit Here keeps the keyboard across a redraw, and comes after the row's badges", () => {
  const commits = [commit("A", { code_review: 1 }), commit("B"), commit("C", { verified: 1 })];
  const page = fakePage("stack", stackVm(stack(commits), undefined, never));
  const rowOf = (summary: string) => page.root.querySelectorAll(".row").find((row) => row.textContent.includes(summary))!;
  const pencil = (row: FakeElement) => row.all((element) => element.getAttribute("data-command") === "ger.stack.edit")[0];
  const kids = rowOf("A").children.filter((child): child is FakeElement => typeof child !== "string");
  assert.ok(kids.findIndex((child) => child.classList.contains("badges")) < kids.findIndex((child) => child.classList.contains("actions")), "Tab reaches the badges first");
  pencil(rowOf("B")).focus();
  // Showing the row the list moved to redraws the view: the pencil under the keyboard is a new element then.
  page.send({ type: "render", vm: stackVm(stack(commits), undefined, never) });
  const now = page.active() as FakeElement;
  assert.equal(now, pencil(rowOf("B")), "the same row's pencil has the keyboard again");
  now.dispatch("keydown", { key: "Enter" });
  now.click();
  assert.deepEqual(page.posted.filter((message) => message.type === "command").map((message) => (message.args as { sha: string }[])[0].sha), [commits[1].sha]);
  // The rebase it started freezes the stack: no pencil any more, the keyboard goes back to the list.
  page.send({ type: "render", vm: stackVm(stack(commits, { rebase_in_progress: true }), undefined, never) });
  assert.ok((page.active() as FakeElement).classList.contains("list"));
});

test("the stylesheet draws a focus ring on every kind of stop", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "..", "media", "views.css"), "utf8");
  for (const selector of ["button:focus-visible", "a:focus-visible", '[tabindex="0"]:not(.list):focus-visible', ".badges[tabindex]:focus", ".step:focus", ".fold:focus", ".list:focus .row.focused"]) {
    assert.ok(css.includes(selector), `views.css: ${selector}`);
  }
});
