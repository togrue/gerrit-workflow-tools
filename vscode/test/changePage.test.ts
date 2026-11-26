// The Change view's page (media/change.js) run on the stand-in DOM of fakePage.ts, so its
// buttons can be pressed in a unit test: what it draws for a view model of `changeVm`, and what
// it posts when a button is clicked.

import assert from "node:assert/strict";
import { test } from "node:test";

import { ChangeVm, READ_ONLY, changeVm } from "../src/changeRows";
import { PROBLEM_WORDS, changeProblem } from "../src/problemRows";
import { FakeElement, fakePage } from "./fakePage";
import { chain, comment, shown, state } from "./fixtures";

/** The Change view's page, drawn for `view`: its root, what it posted, and how to send it a message. */
const changePage = (view: ChangeVm) => fakePage("change", view);

const byClass = (cls: string) => (element: FakeElement) => element.className.split(" ").includes(cls);
const button = (box: FakeElement, text: string) => box.all((element) => element.tagName === "BUTTON" && element.textContent === text)[0];

const buttons = (row: FakeElement) => row.all((element) => element.tagName === "BUTTON" && !element.className.includes("twist")).map((element) => element.title);

test("the comment list is an index: a chain in a file has no reply box or answer buttons, a click goes to its editor thread", () => {
  const inFile = { path: "src/parser.py", line: 12 };
  const page = changePage(changeVm(state({ chains: [chain("there", [comment("Rename this.", inFile)], inFile)] })));
  const [row] = page.root.all(byClass("thread"));
  assert.deepEqual(buttons(row), ["Keep for Me (Not for the Agent)"], "answering is the editor thread's: Reply, Done, Apply and Delete are there");
  row.click();
  assert.deepEqual(page.posted.at(-1), { type: "thread", id: "there", action: "open" });
  page.root.all(byClass("twist"))[0].click();
  assert.equal(page.root.all(byClass("reply-box")).length, 0, "opened, it shows the text whole, and no box to answer in");
});

test("a chain about the whole change, which no file shows, is answered from its row: Reply, Done, Delete, and no box in the side bar", () => {
  const whole = { path: "/PATCHSET_LEVEL", line: null };
  const comments = [comment("Looks good overall.", whole), comment("Added a test.", { ...whole, state: "draft", comment_id: "d1", author: undefined })];
  const page = changePage(changeVm(state({ chains: [chain("whole", comments, whole)] })));
  page.root.all(byClass("twist"))[0].click();
  assert.equal(page.root.all(byClass("reply-box")).length, 0);
  const [row] = page.root.all(byClass("thread"));
  assert.deepEqual(buttons(row), ["Reply as Draft…", "Reply Done and Resolve as Draft", "Keep for Me (Not for the Agent)", "Delete Your Draft Reply…"]);
  row.all((element) => element.title === "Reply as Draft…")[0].click();
  assert.deepEqual(page.posted.at(-1), { type: "thread", id: "whole", action: "reply" }, "the reply box opens at the top of the window");
});

test("Next is one primary button with what it does on it, and runs ger.next", () => {
  const view = { ...changeVm(state({})), next: { label: "Vote and publish", title: "Gerrit waits for your review of it (Alt+Enter)" } };
  const page = changePage(view);
  const next = button(page.root, "Vote and publish");
  assert.ok(next, "the button names what it does");
  assert.equal(next.className, "button", "the primary button");
  next.click();
  assert.deepEqual(page.posted.at(-1), { type: "command", command: "ger.next", args: [] });
});

test("stopped by a rebase on a conflict, the Change view's buttons run the rebase's commands and a conflicted file opens to resolve", () => {
  const { root, posted } = changePage(changeVm(state({ stopped: { stop: "conflict", unmerged: ["src/d.txt"], actions: ["abort", "skip"] } })));
  button(root, "Abort").click();
  assert.deepEqual(posted.at(-1), { type: "command", command: "ger.rebase.abort", args: [] });
  button(root, "Skip").click();
  assert.deepEqual(posted.at(-1), { type: "command", command: "ger.rebase.skip", args: [] });
  const file = root.all(byClass("conflict"))[0];
  assert.equal(file.textContent, "src/d.txtconflicted");
  file.click();
  assert.deepEqual(posted.at(-1), { type: "conflict", path: "src/d.txt" }, "as the banner's row posts it: the extension opens the merge editor");
});

test("your commit's message is a box without the Change-Id, which shows read-only under it; Apply posts the text as typed", () => {
  const { root, posted } = changePage(changeVm(state()));
  const box = root.all((element) => element.tagName === "TEXTAREA" && element.className === "message")[0];
  assert.equal(box.value, "Extract routing", "a one-line subject is one line");
  const changeId = root.all(byClass("change-id"))[0];
  assert.deepEqual([changeId.textContent, changeId.hidden], ["Change-Id: I1", false]);
  box.value = "Extract the routing";
  box.dispatch("input");
  button(root, "Apply").click();
  assert.deepEqual(posted.at(-1), { type: "message", text: "Extract the routing" }, "the extension puts the Change-Id back on");
});

test("a message that cannot be reworded reads at the view's width: paragraphs and list items reflowed, indented blocks and trailers as they are", () => {
  const text = [
    "Fix the thing",
    "",
    "The first paragraph, hard-wrapped",
    "at 72 as people write them.",
    "",
    "- one item, also",
    "  wrapped",
    "- two",
    "",
    "    code(stays)",
    "    as it is",
    "",
    "```",
    "fenced",
    "",
    "block",
    "```",
    "",
    "Bug: 12",
    "Signed-off-by: A <a@example.org>",
  ].join("\n");
  const vm = changeVm(state({ local: false, readOnly: READ_ONLY.remote }));
  const { root } = changePage({ ...vm, message: { ...vm.message!, text } });
  const parts = root.all(byClass("message-text"))[0].children.filter((part): part is FakeElement => typeof part !== "string" && part.className !== "message-gap");
  assert.deepEqual(
    parts.map((part) => [part.className, part.textContent]),
    [
      ["message-subject", "Fix the thing"],
      ["message-para", "The first paragraph, hard-wrapped at 72 as people write them."],
      ["message-item", "- one item, also wrapped"],
      ["message-item", "- two"],
      ["message-pre", "    code(stays)\n    as it is"],
      ["message-pre", "```\nfenced\n\nblock\n```"],
      ["message-trailers", "Bug: 12\nSigned-off-by: A <a@example.org>"],
    ],
  );
});

test("a message that cannot be reworded is plain text: no box, no note saying so, its Change-Id still under it", () => {
  for (const readOnly of [READ_ONLY.remote, READ_ONLY.patchset, READ_ONLY.review]) {
    const { root } = changePage(changeVm(state({ local: false, readOnly })));
    const block = root.all(byClass("message-block"))[0];
    assert.equal(block.all((element) => element.tagName === "TEXTAREA").length, 0, readOnly);
    assert.equal(block.all(byClass("message-text"))[0].textContent, "Extract routing");
    assert.equal(block.all(byClass("change-id"))[0].textContent, "Change-Id: I1");
    assert.ok(!block.textContent.includes(readOnly), "no note");
  }
});

test("Alt+F9 in the Change view walks the comments: VS Code's key binding does not reach a webview view", () => {
  const inFile = { path: "src/parser.py", line: 12 };
  const { root, posted } = changePage(changeVm(state({ chains: [chain("there", [comment("Rename this.", inFile)], inFile)] })));
  const [text] = root.all(byClass("thread"));
  const next = text.dispatch("keydown", { key: "F9", altKey: true });
  assert.ok(next.defaultPrevented);
  assert.deepEqual(posted.at(-1), { type: "command", command: "ger.nextComment" }, "from a row of the index, where Work on Next puts the keyboard");
  text.dispatch("keydown", { key: "F9", altKey: true, shiftKey: true });
  assert.deepEqual(posted.at(-1), { type: "command", command: "ger.prevComment" });
  const count = posted.length;
  text.dispatch("keydown", { key: "F9" });
  assert.equal(posted.length, count, "F9 alone is not the walk");
});

test("what says the state, or what went wrong, is drawn whole (wrapping); a section's counts stay beside a long title", () => {
  const stopped = { stop: "conflict" as const, unmerged: ["src/a.ts", "src/b.ts"], actions: ["abort" as const, "skip" as const] };
  const failing = { ci_failures: ["Verified"], verified: -1, attention_reasons: ["ci-failed"] };
  const wraps = (root: FakeElement, text: string) => root.all(byClass("wraps")).some((element) => element.textContent.includes(text));
  const conflicted = changePage(changeVm(state({ stopped })));
  assert.ok(wraps(conflicted.root, "Conflicts in 2 files — resolve them, then Continue"), "the head");
  assert.ok(!wraps(conflicted.root, "src/a.ts"), "a conflicted file is a row like a file's");
  const unreachable = changePage(changeVm(state({ shown: shown({ ...failing }), ci: { state: "error", error: "Gerrit unreachable: connection refused (gerrit.example.org:443)" } })));
  assert.ok(wraps(unreachable.root, "Gerrit unreachable: connection refused (gerrit.example.org:443)"));
  const noLink = changePage(changeVm(state({ shown: shown({ ...failing }), ci: { state: "loaded", contents: [], introduced: null } })));
  assert.ok(wraps(noLink.root, "No CI link was found for the failed build"));
  const files = changePage(changeVm(state({})));
  const head = files.root.all(byClass("section-head"))[0];
  assert.equal(head.all(byClass("section-summary"))[0].all(byClass("counts")).length, 1, "the counts sit in the summary, which wraps under the title as a whole");
});

test("a change that could not be shown says the state in ger's words, git's words under it, and offers the ways on", () => {
  const { root, posted } = changePage({ problem: changeProblem("fatal: bad object 3f9a2c1", true) });
  assert.ok(root.textContent.includes(PROBLEM_WORDS.change));
  assert.equal(root.all(byClass("raw"))[0].textContent, "fatal: bad object 3f9a2c1");
  const labels = root.all((element) => element.tagName === "BUTTON").map((element) => element.textContent);
  assert.deepEqual(labels, ["Show Again", "Show Local Stack", "Show Log"]);
  button(root, "Show Again").click();
  assert.deepEqual(posted.at(-1), { type: "command", command: "ger.change.reload", args: [] });
  const theirs = changePage({ problem: changeProblem("Gerrit unreachable", false) });
  assert.ok(button(theirs.root, "Show Review Chains"), "somebody's change is picked again from Review chains");
});

test("CI that could not be fetched offers Try Again; a failed build without a log, Open in Gerrit", () => {
  const failing = { ci_failures: ["Verified"], verified: -1, attention_reasons: ["ci-failed"] };
  const unreachable = changePage(changeVm(state({ shown: shown({ ...failing }), ci: { state: "error", error: "Gerrit unreachable" } })));
  button(unreachable.root, "Try Again").click();
  assert.deepEqual(unreachable.posted.at(-1), { type: "ci" });
  const noLink = changePage(changeVm(state({ shown: shown({ ...failing }), ci: { state: "loaded", contents: [], introduced: null } })));
  button(noLink.root, "Open in Gerrit").click();
  assert.deepEqual(noLink.posted.at(-1), { type: "command", command: "ger.openInGerrit" });
});

test("in a task, From and To at their default are one line that says what is compared; Change opens the pickers", () => {
  const task = { mode: "rework", heading: "Reworking your chain", title: "t", position: "change 1 of 1", left: "", steps: [], actions: [], views: { label: "All chains", detail: "", command: "ger.showAllChains" } } as const;
  const view = { ...changeVm(state({})), task: { ...task, steps: [], actions: [] } };
  const { root } = changePage(view);
  assert.equal(root.all((element) => element.tagName === "SELECT").length, 0, "folded");
  const line = root.all(byClass("compare-line"))[0];
  assert.match(line.textContent, /→/);
  button(root, "Change").click();
  assert.equal(root.all((element) => element.tagName === "SELECT").length, 2, "opened");
  const byHand = changePage({ ...view, compare: { ...view.compare!, mode: { ...view.compare!.mode, active: false } } });
  assert.equal(byHand.root.all((element) => element.tagName === "SELECT").length, 2, "picked by hand: they stay open");
  assert.equal(changePage(changeVm(state({}))).root.all((element) => element.tagName === "SELECT").length, 2, "outside a task: as before");
});
