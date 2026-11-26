// A page of a webview view (media/<view>.js) run for real, as the gallery runs it, but on a small
// stand-in for the DOM, so a unit test can click, press keys and read what the page posted.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vm from "node:vm";

const media = path.join(__dirname, "..", "..", "media");

export interface Listener {
  (event: FakeEvent): void;
}

export interface FakeEvent {
  type: string;
  target: FakeElement;
  stopped: boolean;
  defaultPrevented: boolean;
  key?: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  stopPropagation(): void;
  preventDefault(): void;
}

/** What a dispatched event carries besides its type: the keys held, the key pressed. */
export type EventInit = Pick<FakeEvent, "key" | "shiftKey" | "ctrlKey" | "metaKey">;

/** `.class`, `tag`, or `.class[data-name="value"]`: the selectors the pages use. */
function matches(element: FakeElement, selector: string): boolean {
  const found = /^(\.[\w-]+)?([a-z]+)?(?:\[([\w-]+)="(.*)"\])?$/.exec(selector);
  if (!found) {
    throw new Error(`the fake DOM does not know the selector ${selector}`);
  }
  const [, cls, tag, attribute, value] = found;
  return (
    (!cls || element.classList.contains(cls.slice(1))) &&
    (!tag || element.tagName === tag.toUpperCase()) &&
    (!attribute || element.getAttribute(attribute) === value)
  );
}

/** Enough of an element for the pages: attributes, children, text, classes, and events that bubble. */
export class FakeElement {
  readonly children: (FakeElement | string)[] = [];
  readonly attributes = new Map<string, string>();
  readonly listeners = new Map<string, Listener[]>();
  parent: FakeElement | null = null;
  className = "";
  hidden = false;
  title = "";
  value = "";
  checked = false;
  scrolledIntoView = false;

  constructor(
    readonly tagName: string,
    private readonly page?: { activeElement: FakeElement | null },
  ) {}

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
    if (name === "title") {
      this.title = value;
    }
  }

  getAttribute(name: string): string | null {
    return this.attributes.get(name) ?? null;
  }

  /** The `data-` attributes, by their camel-cased name, as the browser gives them. */
  get dataset(): Record<string, string> {
    const entries = [...this.attributes].filter(([name]) => name.startsWith("data-"));
    return Object.fromEntries(entries.map(([name, value]) => [name.slice(5).replace(/-(\w)/g, (_, c: string) => c.toUpperCase()), value]));
  }

  get classList() {
    const names = () => this.className.split(" ").filter(Boolean);
    const set = (on: string[]) => (this.className = on.join(" "));
    return {
      contains: (name: string) => names().includes(name),
      add: (name: string) => set([...names().filter((n) => n !== name), name]),
      remove: (name: string) => set(names().filter((n) => n !== name)),
      toggle: (name: string, on: boolean) => set(on ? [...names().filter((n) => n !== name), name] : names().filter((n) => n !== name)),
    };
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

  contains(other: FakeElement | null): boolean {
    for (let element = other; element; element = element.parent) {
      if (element === this) {
        return true;
      }
    }
    return false;
  }

  closest(selector: string): FakeElement | null {
    for (let element: FakeElement | null = this; element; element = element.parent) {
      if (matches(element, selector)) {
        return element;
      }
    }
    return null;
  }

  querySelectorAll(selector: string): FakeElement[] {
    return this.all((element) => matches(element, selector));
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  focus(): void {
    if (this.page) {
      this.page.activeElement = this;
    }
  }

  scrollIntoView(): void {
    this.scrolledIntoView = true;
  }

  /** Fire `type` here and let it bubble up, as the browser does; returns the event. */
  dispatch(type: string, init: EventInit = {}): FakeEvent {
    const event: FakeEvent = {
      type,
      target: this,
      stopped: false,
      defaultPrevented: false,
      ...init,
      stopPropagation: () => (event.stopped = true),
      preventDefault: () => (event.defaultPrevented = true),
    };
    for (let element: FakeElement | null = this; element && !event.stopped; element = element.parent) {
      (element.listeners.get(type) ?? []).forEach((listener) => listener(event));
    }
    return event;
  }

  click(init: EventInit = {}): void {
    this.dispatch("click", init);
  }

  /** Every element below this one that `match` accepts, in document order. */
  all(match: (element: FakeElement) => boolean): FakeElement[] {
    return this.children.flatMap((child) => (typeof child === "string" ? [] : [...(match(child) ? [child] : []), ...child.all(match)]));
  }
}

/**
 * The page `view` (media/<view>.js after common.js), drawn for `model`: its root, what it posted,
 * how to send it a message, and `flush` to let what the page put off (`setTimeout`) happen.
 */
export function fakePage(view: string, model: unknown) {
  const state = { activeElement: null as FakeElement | null };
  const root = new FakeElement("div", state);
  const body = new FakeElement("body", state);
  body.append(root);
  const posted: Record<string, unknown>[] = [];
  const windowListeners: Listener[] = [];
  const timers = new Map<number, () => void>();
  let nextTimer = 1;
  const flush = () => {
    const due = [...timers.values()];
    timers.clear();
    due.forEach((run) => run());
  };
  const document = {
    body,
    scrollingElement: undefined,
    get activeElement() {
      return state.activeElement;
    },
    querySelectorAll: (selector: string) => body.querySelectorAll(selector),
    querySelector: (selector: string) => body.querySelector(selector),
    createElement: (tag: string) => new FakeElement(tag.toUpperCase(), state),
    createElementNS: (_ns: string, tag: string) => new FakeElement(tag, state),
    getElementById: (id: string) => (id === "root" ? root : null),
    addEventListener: (type: string, listener: Listener) => body.addEventListener(type, listener),
  };
  const window = {
    addEventListener: (type: string, listener: Listener) => (type === "message" ? windowListeners.push(listener) : undefined),
  } as Record<string, unknown>;
  const context = vm.createContext({
    window,
    document,
    CSS: { escape: (text: string) => text },
    // Nothing runs on its own: `flush` is the pause, whatever its length.
    setTimeout: (run: () => void) => {
      timers.set(nextTimer, run);
      return nextTimer++;
    },
    clearTimeout: (id: number) => timers.delete(id),
    IntersectionObserver: class {
      observe(): void {}
      disconnect(): void {}
    },
    // A message is cloned on its way out, as VS Code does.
    acquireVsCodeApi: () => ({ postMessage: (message: unknown) => posted.push(JSON.parse(JSON.stringify(message))), getState: () => undefined, setState: () => undefined }),
  });
  for (const file of ["common.js", `${view}.js`]) {
    vm.runInContext(fs.readFileSync(path.join(media, file), "utf8"), context, { filename: file });
  }
  const send = (data: unknown) => windowListeners.forEach((listener) => listener({ data } as unknown as FakeEvent));
  send({ type: "render", vm: model });
  return { root, posted, send, flush };
}
