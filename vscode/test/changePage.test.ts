// The Change view's page (media/change.js) run for real, as the gallery runs it, but on a small
// stand-in for the DOM, so its buttons can be pressed in a unit test: what it draws for a view
// model of `changeVm`, and what it posts when a button is clicked.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import * as vm from "node:vm";

import { ChangeVm, changeVm } from "../src/changeRows";
import { chain, comment, state } from "./fixtures";

const media = path.join(__dirname, "..", "..", "media");

interface Listener {
  (event: FakeEvent): void;
}

interface FakeEvent {
  type: string;
  target: FakeElement;
  stopped: boolean;
  stopPropagation(): void;
  preventDefault(): void;
}

/** Enough of an element for the pages: attributes, children, text, listeners and clicks that bubble. */
class FakeElement {
  readonly children: (FakeElement | string)[] = [];
  readonly attributes = new Map<string, string>();
  readonly listeners = new Map<string, Listener[]>();
  parent: FakeElement | null = null;
  className = "";
  hidden = false;
  title = "";
  value = "";
  checked = false;

  constructor(readonly tagName: string) {}

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
    if (name === "title") {
      this.title = value;
    }
  }

  getAttribute(name: string): string | null {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  append(...nodes: (FakeElement | string)[]): void {
    for (const node of nodes) {
      if (node instanceof FakeElement) {
        node.parent = this;
      }
      this.children.push(node);
    }
  }

  replaceChildren(...nodes: (FakeElement | string)[]): void {
    this.children.length = 0;
    this.append(...nodes);
  }

  get textContent(): string {
    return this.children.map((child) => (typeof child === "string" ? child : child.textContent)).join("");
  }

  set textContent(text: string) {
    this.replaceChildren(...(text ? [text] : []));
  }

  get isConnected(): boolean {
    return true;
  }

  click(): void {
    const event: FakeEvent = { type: "click", target: this, stopped: false, stopPropagation: () => (event.stopped = true), preventDefault: () => undefined };
    for (let element: FakeElement | null = this; element && !event.stopped; element = element.parent) {
      (element.listeners.get("click") ?? []).forEach((listener) => listener(event));
    }
  }

  /** Every element below this one that `match` accepts, in document order. */
  all(match: (element: FakeElement) => boolean): FakeElement[] {
    return this.children.flatMap((child) => (typeof child === "string" ? [] : [...(match(child) ? [child] : []), ...child.all(match)]));
  }
}

/** The Change view's page, drawn for `view`: its root, what it posted, and how to send it a message. */
function changePage(view: ChangeVm) {
  const root = new FakeElement("div");
  const body = new FakeElement("body");
  const posted: Record<string, unknown>[] = [];
  const windowListeners: Listener[] = [];
  const document = {
    body,
    scrollingElement: undefined,
    createElement: (tag: string) => new FakeElement(tag.toUpperCase()),
    getElementById: (id: string) => (id === "root" ? root : null),
    addEventListener: () => undefined,
  };
  const window = {
    addEventListener: (type: string, listener: Listener) => (type === "message" ? windowListeners.push(listener) : undefined),
  } as Record<string, unknown>;
  const context = vm.createContext({
    window,
    document,
    IntersectionObserver: class {
      observe(): void {}
      disconnect(): void {}
    },
    // A message is cloned on its way out, as VS Code does.
    acquireVsCodeApi: () => ({ postMessage: (message: unknown) => posted.push(JSON.parse(JSON.stringify(message))), getState: () => undefined, setState: () => undefined }),
  });
  for (const file of ["common.js", "change.js"]) {
    vm.runInContext(fs.readFileSync(path.join(media, file), "utf8"), context, { filename: file });
  }
  const send = (data: unknown) => windowListeners.forEach((listener) => listener({ data } as unknown as FakeEvent));
  send({ type: "render", vm: view });
  return { root, posted, send };
}

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

test("a confirmed Discard empties the reply box; a dismissed one keeps what you typed", () => {
  const { box, send } = wholeChange(true);
  const text = box.all((element) => element.tagName === "TEXTAREA")[0];
  text.value = "And the docs.";
  send({ type: "discarded", id: "whole", deleted: false });
  assert.equal(text.value, "And the docs.");
  send({ type: "discarded", id: "whole", deleted: true });
  assert.equal(text.value, "");
});
