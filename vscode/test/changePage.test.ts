// The Change view's page (media/change.js) run on the stand-in DOM of fakePage.ts, so its
// buttons can be pressed in a unit test: what it draws for a view model of `changeVm`, and what
// it posts when a button is clicked.

import assert from "node:assert/strict";
import { test } from "node:test";

import { ChangeVm, READ_ONLY, changeVm } from "../src/changeRows";
import { FakeElement, fakePage } from "./fakePage";
import { chain, comment, state } from "./fixtures";

/** The Change view's page, drawn for `view`: its root, what it posted, and how to send it a message. */
const changePage = (view: ChangeVm) => fakePage("change", view);

const byClass = (cls: string) => (element: FakeElement) => element.className.split(" ").includes(cls);
const button = (box: FakeElement, text: string) => box.all((element) => element.tagName === "BUTTON" && element.textContent === text)[0];

/** A chain about the whole change, opened in the view so its reply box shows; `draft`: with your draft reply in it. */
function wholeChange(draft: boolean) {
  const whole = { path: "/PATCHSET_LEVEL", line: null };
  const comments = [comment("Looks good overall.", whole), ...(draft ? [comment("Added a test.", { ...whole, state: "draft", comment_id: "d1", author: undefined })] : [])];
  const page = changePage(changeVm(state({ chains: [chain("whole", comments, whole)] })));
  page.root.all(byClass("twist"))[0].click();
  const box = page.root.all(byClass("reply-box"))[0];
  assert.ok(box, "the chain shows its reply box once opened");
  return { ...page, box };
}

test("the Change view's reply box is, as on Gerrit, Resolved on the left and Discard, Cancel, Save bottom right", () => {
  const { box } = wholeChange(true);
  const bar = box.all(byClass("message-bar"))[0];
  const order = bar.children.map((child) => (typeof child === "string" ? child : child.tagName === "BUTTON" ? child.textContent : child.className));
  assert.deepEqual(order, ["resolved", "detail", "Discard", "Cancel", "Save"]);
});

test("Discard shows in the Change view's reply box only while your draft reply is there, and asks through the row's trash", () => {
  const without = wholeChange(false);
  assert.equal(button(without.box, "Discard").hidden, true, "no draft of yours: nothing to discard, Cancel covers it");

  const { box, posted } = wholeChange(true);
  const discard = button(box, "Discard");
  assert.equal(discard.hidden, false);
  assert.equal(discard.title, "Delete Your Draft Reply…", "named as the row's trash");
  discard.click();
  assert.deepEqual(posted.at(-1), { type: "thread", id: "whole", action: "discard" });
  assert.ok(!posted.some((message) => message.action === "chain.delete"), "the extension runs the trash's command, not the page");
});

test("a confirmed Discard empties the reply box and resets Resolved; a dismissed one keeps both", () => {
  const { box, send } = wholeChange(true);
  const text = box.all((element) => element.tagName === "TEXTAREA")[0];
  const resolved = box.all((element) => element.tagName === "INPUT")[0];
  const standing = resolved.checked;
  text.value = "And the docs.";
  // The deleted draft's choice, kept while text is typed: a fresh Save must not resolve with it.
  resolved.checked = !standing;
  send({ type: "discarded", id: "whole", deleted: false });
  assert.equal(text.value, "And the docs.");
  assert.equal(resolved.checked, !standing);
  send({ type: "discarded", id: "whole", deleted: true });
  assert.equal(text.value, "");
  assert.equal(resolved.checked, standing, "back where the comment stands, as Cancel leaves it");
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

test("a message that cannot be reworded is plain text: no box, no note saying so, its Change-Id still under it", () => {
  for (const readOnly of [READ_ONLY.remote, READ_ONLY.patchset]) {
    const { root } = changePage(changeVm(state({ local: false, readOnly })));
    const block = root.all(byClass("message-block"))[0];
    assert.equal(block.all((element) => element.tagName === "TEXTAREA").length, 0, readOnly);
    assert.equal(block.all(byClass("message-text"))[0].textContent, "Extract routing");
    assert.equal(block.all(byClass("change-id"))[0].textContent, "Change-Id: I1");
    assert.ok(!block.textContent.includes(readOnly), "no note");
  }
});
