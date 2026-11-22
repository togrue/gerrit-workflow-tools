// The Local stack's page (media/stack.js) run on the stand-in DOM of fakePage.ts: which rows a
// click, a key or a right-click selects, what the Change view is asked to show, and what each
// row hands VS Code's context menu.

import assert from "node:assert/strict";
import { test } from "node:test";

import { stackVm } from "../src/stackRows";
import { FakeElement, fakePage } from "./fakePage";
import { commit, never, stack } from "./fixtures";

/** Three commits, shown newest first: Third, Second, First. */
function stackPage() {
  const commits = [commit("First"), commit("Second"), commit("Third")];
  const model = stackVm(stack(commits), undefined, never);
  const page = fakePage("stack", model);
  const list = page.root.querySelector(".list") as FakeElement;
  const row = (summary: string) => list.querySelectorAll(".row").find((element) => element.textContent.includes(summary)) as FakeElement;
  const selected = () =>
    list
      .querySelectorAll(".row")
      .filter((element) => element.classList.contains("selected"))
      .map((element) => element.querySelector(".label")?.textContent);
  const focused = () => list.querySelectorAll(".row").find((element) => element.classList.contains("focused"))?.querySelector(".label")?.textContent;
  const context = (summary: string) => JSON.parse(row(summary).getAttribute("data-vscode-context") ?? "{}");
  const opened = () => (page.flush(), page.posted).filter((message) => message.type === "open").map((message) => [commits.find((c) => c.sha === message.sha)?.summary, message.keepFocus]);
  /** A row clicked (or right-clicked, double-clicked), and a pause after it. */
  const click = (summary: string, init = {}, type = "click") => {
    row(summary).dispatch(type, init);
    page.flush();
  };
  /** A key pressed, and a pause after it. */
  const key = (name: string, init = {}) => {
    const event = list.dispatch("keydown", { key: name, ...init });
    page.flush();
    return event;
  };
  return { ...page, model, commits, list, row, selected, focused, context, opened, click, key };
}

/** The page drawn again for `rows`, as after a rewrite. */
function redraw(page: ReturnType<typeof stackPage>, rows: typeof page.model.rows) {
  page.send({ type: "render", vm: { ...page.model, rows } });
  return page.root.querySelector(".list") as FakeElement;
}

test("a click selects one row and shows it, the keyboard staying in the list; a double click moves to the Change view", () => {
  const page = stackPage();
  assert.deepEqual(page.selected(), []);
  page.click("Second");
  assert.deepEqual(page.selected(), ["Second"]);
  assert.equal(page.focused(), "Second");
  assert.equal(page.row("Second").getAttribute("aria-selected"), "true");
  assert.equal(page.row("Third").getAttribute("aria-selected"), "false");
  page.click("Third");
  assert.deepEqual(page.selected(), ["Third"]);
  page.click("Third", {}, "dblclick");
  assert.deepEqual(page.opened(), [
    ["Second", true],
    ["Third", true],
    ["Third", false],
  ]);
});

test("Ctrl+click adds or takes away a row, Shift+click selects the rows from the last one clicked, and each shows the clicked row", () => {
  const page = stackPage();
  page.click("Third");
  page.click("First", { ctrlKey: true });
  assert.deepEqual(page.selected(), ["Third", "First"]);
  page.click("Third", { metaKey: true });
  assert.deepEqual(page.selected(), ["First"]);
  page.click("First", { shiftKey: true });
  assert.deepEqual(page.selected(), ["Third", "Second", "First"], "from Third: the last Ctrl+click sets the anchor, also when it takes the row away");
  assert.deepEqual(
    page.opened().map(([summary]) => summary),
    ["Third", "First", "Third", "First"],
  );
});

test("the arrow keys walk the rows, Shift extends, Home and End jump, Escape keeps the focused row (and shows nothing new), Ctrl+A takes all", () => {
  const page = stackPage();
  page.key("ArrowDown");
  assert.deepEqual(page.selected(), ["Third"], "nothing focused: the first row");
  page.key("ArrowDown");
  assert.deepEqual(page.selected(), ["Second"]);
  assert.equal(page.row("Second").scrolledIntoView, true);
  page.key("ArrowDown", { shiftKey: true });
  assert.deepEqual(page.selected(), ["Second", "First"]);
  page.key("ArrowDown");
  assert.deepEqual(page.selected(), ["First"], "past the end: the last row");
  page.key("Home", { shiftKey: true });
  assert.deepEqual(page.selected(), ["Third", "Second", "First"]);
  assert.equal(page.focused(), "Third");
  page.key("Escape");
  assert.deepEqual(page.selected(), ["Third"]);
  page.key("End");
  assert.deepEqual(page.selected(), ["First"]);
  page.key("ArrowUp");
  assert.deepEqual(page.selected(), ["Second"]);
  const all = page.key("a", { ctrlKey: true });
  assert.deepEqual(page.selected(), ["Third", "Second", "First"]);
  assert.equal(all.defaultPrevented, true);
  page.key("Enter");
  assert.deepEqual(page.opened(), [
    ["Third", true],
    ["Second", true],
    ["First", true],
    ["First", true],
    ["Third", true],
    ["First", true],
    ["Second", true],
    ["Second", false],
  ]);
  assert.equal(page.key("x").defaultPrevented, false, "other keys are left to VS Code");
});

test("a row's context menu gets that row, or every selected row when it is one of several", () => {
  const page = stackPage();
  page.click("Third");
  page.click("Second", { ctrlKey: true });
  const [first, second, third] = page.commits;
  assert.deepEqual(
    page.context("Second"),
    { webviewSection: "commit", preventDefaultContextMenuItems: true, sha: second.sha, shas: [third.sha, second.sha], gerUrl: true, gerSquash: true, gerChangeId: true, gerMulti: true },
  );
  assert.deepEqual(page.context("First").shas, [first.sha], "a row outside the selection: only itself");
  assert.equal(page.context("First").gerMulti, false);
  page.click("First", {}, "contextmenu");
  assert.deepEqual(page.selected(), ["First"], "right-clicking an unselected row selects it alone");
  page.click("Third", { ctrlKey: true });
  page.click("Third", {}, "contextmenu");
  assert.deepEqual(page.selected(), ["Third", "First"], "right-clicking a selected row keeps the selection");
});

test("a row's button runs its command on that row without changing the selection", () => {
  const page = stackPage();
  page.click("Third");
  const edit = page.row("First").querySelector("button") as FakeElement;
  edit.click();
  assert.deepEqual(page.posted.at(-1), { type: "command", command: "ger.stack.edit", args: [{ sha: page.commits[0].sha }] });
  assert.deepEqual(page.selected(), ["Third"]);
});

test("the selection follows a commit through a rewrite by its key, and lets go of one that left", () => {
  const page = stackPage();
  page.click("Third");
  page.click("First", { ctrlKey: true });
  const rewritten = page.model.rows.filter((row) => !row.summary.includes("Third")).map((row) => ({ ...row, sha: row.sha.replace(/^./, "f") }));
  const list = redraw(page, rewritten);
  const rows = list.querySelectorAll(".row");
  const labels = (cls: string) => rows.filter((element) => element.classList.contains(cls)).map((element) => element.querySelector(".label")?.textContent);
  assert.deepEqual(labels("selected"), ["First"]);
  assert.deepEqual(labels("focused"), ["First"], "the focused row stays");
});

test("walking past rows without a pause shows only the row walked to", () => {
  const page = stackPage();
  page.list.dispatch("keydown", { key: "ArrowDown" });
  page.list.dispatch("keydown", { key: "ArrowDown" });
  assert.deepEqual(page.opened(), [["Second", true]]);
});

test("a row rewritten while the Change view waits to show it is shown as it is now; a dropped one, not at all", () => {
  const page = stackPage();
  page.list.dispatch("keydown", { key: "ArrowDown" });
  const rewritten = page.model.rows.map((row) => ({ ...row, sha: row.summary === "Third" ? "f".repeat(40) : row.sha }));
  redraw(page, rewritten);
  assert.deepEqual(
    (page.flush(), page.posted.filter((message) => message.type === "open").map((message) => message.sha)),
    ["f".repeat(40)],
  );
  const list = redraw(page, page.model.rows);
  list.dispatch("keydown", { key: "ArrowDown" });
  redraw(page, page.model.rows.filter((row) => row.summary !== "Second"));
  page.flush();
  assert.equal(page.posted.filter((message) => message.type === "open").length, 1, "Second went: nothing more is shown");
});

test("Enter on a row's button is the button's, and Enter or Escape with no row focused is VS Code's", () => {
  const page = stackPage();
  assert.equal(page.key("Escape").defaultPrevented, false);
  assert.equal(page.key("Enter").defaultPrevented, false);
  page.click("Third");
  const edit = page.row("First").querySelector("button") as FakeElement;
  const enter = edit.dispatch("keydown", { key: "Enter" });
  page.flush();
  assert.equal(enter.defaultPrevented, false, "the browser presses the button");
  assert.deepEqual(page.opened(), [["Third", true]], "Enter on First's button does not show Third again");
});
