// Real input for the walk: mouse clicks and keys through the window's DevTools protocol, as a user
// gives them, into the workbench (tree rows, view title buttons, dialogs, notifications, the
// editor, the quick input) and into the pages of the webview views (their frames are targets of
// their own: an element's place there plus the frame's place in the window is where to click).

import * as fs from "node:fs";

interface Socket {
  onopen: (() => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  send(data: string): void;
  close(): void;
}
type SocketClass = new (url: string) => Socket;

interface Target {
  type: string;
  url: string;
  webSocketDebuggerUrl: string;
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

class Connection {
  private next = 0;
  private readonly waiting = new Map<number, (reply: { result?: unknown; error?: { message: string } }) => void>();

  private constructor(private readonly socket: Socket) {
    socket.onmessage = (event) => {
      const reply = JSON.parse(String(event.data)) as { id?: number; result?: unknown; error?: { message: string } };
      if (reply.id !== undefined) {
        this.waiting.get(reply.id)?.(reply);
        this.waiting.delete(reply.id);
      }
    };
  }

  static async open(url: string): Promise<Connection> {
    const WebSocket = (globalThis as { WebSocket?: SocketClass }).WebSocket;
    if (!WebSocket) {
      throw new Error("walk: this extension host has no WebSocket");
    }
    const socket = new WebSocket(url);
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`walk: no connection to ${url} in 5 s`)), 5000);
      socket.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new Error(`walk: could not connect to ${url}`));
      };
    });
    return new Connection(socket);
  }

  call<T = unknown>(method: string, params: object = {}): Promise<T> {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`walk: ${method} got no answer`)), 10_000);
      this.waiting.set(id, (reply) => {
        clearTimeout(timer);
        if (reply.error) {
          reject(new Error(`${method}: ${reply.error.message}`));
        } else {
          resolve(reply.result as T);
        }
      });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate<T>(expression: string): Promise<T> {
    const reply = await this.call<{ result?: { value?: string }; exceptionDetails?: { text: string } }>("Runtime.evaluate", {
      expression: `JSON.stringify((() => { ${expression} })() ?? null)`,
      returnByValue: true,
    });
    if (reply.exceptionDetails) {
      throw new Error(`walk: ${reply.exceptionDetails.text} in ${expression.slice(0, 200)}`);
    }
    return JSON.parse(reply.result?.value ?? "null") as T;
  }

  close(): void {
    this.socket.close();
  }
}

const MODIFIERS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
const KEYS: Record<string, { code: string; key: string; keyCode: number; text?: string }> = {
  Enter: { code: "Enter", key: "Enter", keyCode: 13, text: "\r" },
  Escape: { code: "Escape", key: "Escape", keyCode: 27 },
  Tab: { code: "Tab", key: "Tab", keyCode: 9 },
  Down: { code: "ArrowDown", key: "ArrowDown", keyCode: 40 },
  Up: { code: "ArrowUp", key: "ArrowUp", keyCode: 38 },
  F9: { code: "F9", key: "F9", keyCode: 120 },
  PageDown: { code: "PageDown", key: "PageDown", keyCode: 34 },
  PageUp: { code: "PageUp", key: "PageUp", keyCode: 33 },
  "`": { code: "Backquote", key: "`", keyCode: 192, text: "`" },
};

/** A key chord as VS Code writes it: `alt+f9`, `ctrl+enter`, `escape`, `ctrl+c`, `ctrl+s`. */
function chord(spec: string): { modifiers: number; code: string; key: string; keyCode: number; text?: string } {
  const parts = spec.split("+");
  const name = parts.pop() ?? "";
  const modifiers = parts.reduce((sum, part) => sum + (MODIFIERS[part.toLowerCase() as keyof typeof MODIFIERS] ?? 0), 0);
  const known = Object.entries(KEYS).find(([label]) => label.toLowerCase() === name.toLowerCase())?.[1];
  if (known) {
    return { modifiers, ...known, text: modifiers & (MODIFIERS.ctrl | MODIFIERS.alt | MODIFIERS.meta) ? undefined : known.text };
  }
  const letter = name.toUpperCase();
  return { modifiers, code: `Key${letter}`, key: modifiers ? name.toLowerCase() : name, keyCode: letter.charCodeAt(0), text: modifiers ? undefined : name };
}

/** The VS Code window: its workbench page, and the frames of the webview views. */
export class Window {
  private constructor(
    private readonly port: string,
    private readonly workbench: Connection,
  ) {}

  static async open(activePortFile: string): Promise<Window> {
    const port = fs.readFileSync(activePortFile, "utf8").split("\n")[0].trim();
    const targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()) as Target[];
    const page = targets.find((target) => target.type === "page" && target.url.includes("workbench"));
    if (!page) {
      throw new Error("walk: no workbench page");
    }
    return new Window(port, await Connection.open(page.webSocketDebuggerUrl));
  }

  /** Run `body` (a function body returning a value) in the workbench page. */
  evaluate<T>(body: string): Promise<T> {
    return this.workbench.evaluate<T>(body);
  }

  async click(at: { x: number; y: number }, count = 1): Promise<void> {
    await this.workbench.call("Input.dispatchMouseEvent", { type: "mouseMoved", x: at.x, y: at.y });
    for (let n = 1; n <= count; n++) {
      await this.workbench.call("Input.dispatchMouseEvent", { type: "mousePressed", x: at.x, y: at.y, button: "left", buttons: 1, clickCount: n });
      await this.workbench.call("Input.dispatchMouseEvent", { type: "mouseReleased", x: at.x, y: at.y, button: "left", buttons: 0, clickCount: n });
    }
  }

  /** Press at `from`, move to `to` in steps, release: a sash dragged. */
  async drag(from: { x: number; y: number }, to: { x: number; y: number }): Promise<void> {
    const mouse = (type: string, at: { x: number; y: number }, buttons: number) =>
      this.workbench.call("Input.dispatchMouseEvent", { type, x: at.x, y: at.y, button: "left", buttons, clickCount: 1 });
    await mouse("mouseMoved", from, 0);
    await mouse("mousePressed", from, 1);
    for (let step = 1; step <= 10; step++) {
      await mouse("mouseMoved", { x: from.x + ((to.x - from.x) * step) / 10, y: from.y + ((to.y - from.y) * step) / 10 }, 1);
    }
    await mouse("mouseReleased", to, 0);
  }

  hover(at: { x: number; y: number }): Promise<unknown> {
    return this.workbench.call("Input.dispatchMouseEvent", { type: "mouseMoved", x: at.x, y: at.y });
  }

  /** Press a key chord (`alt+f9`) where the keyboard is. */
  async key(spec: string): Promise<void> {
    const { modifiers, code, key, keyCode, text } = chord(spec);
    const base = { modifiers, code, key, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode };
    await this.workbench.call("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...base, ...(text ? { text, unmodifiedText: text } : {}) });
    await this.workbench.call("Input.dispatchKeyEvent", { type: "keyUp", ...base });
  }

  /** Type `text` where the keyboard is, as one input (an IME commit). */
  type(text: string): Promise<unknown> {
    return this.workbench.call("Input.insertText", { text });
  }

  /** The middle of the first visible element `find` (a function body over `document`, returning an Element) names in the workbench. */
  async boxOf(find: string): Promise<Box | null> {
    return this.workbench.evaluate<Box | null>(`const e = (() => { ${find} })(); if (!e) return null; e.scrollIntoView?.({ block: "nearest" }); const r = e.getBoundingClientRect(); return r.width && r.height ? { x: r.left, y: r.top, width: r.width, height: r.height } : null;`);
  }

  /** The middle of the first visible element `find` names in the page of the webview view `view` (`stack`, `change`), in window coordinates. */
  async boxInView(view: string, find: string): Promise<Box | null> {
    const frame = await this.frame(view);
    if (!frame) {
      return null;
    }
    const inner = await frame.connection.evaluate<Box | null>(
      `const f = document.querySelector("iframe#active-frame") || document.querySelector("iframe"); const document_ = f ? f.contentDocument : document; const e = ((document) => { ${find} })(document_); if (!e) return null; e.scrollIntoView?.({ block: "nearest" }); const r = e.getBoundingClientRect(); const o = f ? f.getBoundingClientRect() : { left: 0, top: 0 }; return r.width && r.height ? { x: r.left + o.left, y: r.top + o.top, width: r.width, height: r.height } : null;`,
    );
    frame.connection.close();
    if (!inner) {
      return null;
    }
    const outer = await this.workbench.evaluate<Box | null>(
      `const f = [...document.querySelectorAll("iframe")].find((f) => f.src.includes(${JSON.stringify(frame.id)})); if (!f) return null; const r = f.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height };`,
    );
    return outer ? { x: outer.x + inner.x, y: outer.y + inner.y, width: inner.width, height: inner.height } : null;
  }

  /** Run `body` (over `document`) in the page of the webview view `view`. */
  async inView<T>(view: string, body: string): Promise<T | null> {
    const frame = await this.frame(view);
    if (!frame) {
      return null;
    }
    try {
      return await frame.connection.evaluate<T>(`const f = document.querySelector("iframe#active-frame") || document.querySelector("iframe"); return ((document) => { ${body} })(f ? f.contentDocument : document);`);
    } finally {
      frame.connection.close();
    }
  }

  private async frame(view: string): Promise<{ id: string; connection: Connection } | undefined> {
    const targets = (await (await fetch(`http://127.0.0.1:${this.port}/json/list`)).json()) as Target[];
    for (const target of targets.filter((t) => t.type === "iframe" && t.url.includes("index.html?id="))) {
      const connection = await Connection.open(target.webSocketDebuggerUrl);
      const name = await connection
        .evaluate<string>(`const f = document.querySelector("iframe#active-frame") || document.querySelector("iframe"); return f && f.contentDocument ? f.contentDocument.body.className : "";`)
        .catch(() => "");
      if (name.split(/\s+/).includes(view)) {
        return { id: new URL(target.url).searchParams.get("id") ?? "", connection };
      }
      connection.close();
    }
    return undefined;
  }

  close(): void {
    this.workbench.close();
  }
}

export const middle = (box: Box): { x: number; y: number } => ({ x: box.x + box.width / 2, y: box.y + box.height / 2 });
