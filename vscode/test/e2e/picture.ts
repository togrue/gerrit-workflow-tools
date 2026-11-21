// Pictures of the VS Code window under test, for GER_E2E_PNG (see run.ts). Taken through the
// window's DevTools protocol, which Electron serves on the port it writes to `DevToolsActivePort`
// in the profile: no dependency, and it works on a window that has no display (Linux, headless).
// The picture includes the webview views, which run in frames of their own.

import * as fs from "node:fs";

/** The window size of every picture, in CSS pixels: a laptop screen, so layouts look as users see them. */
const WIDTH = 1400;
const HEIGHT = 900;

/** The part of the WebSocket of Node 22 (and of the VS Code extension host) used here. */
interface Socket {
  onopen: (() => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  send(data: string): void;
  close(): void;
}
type SocketClass = new (url: string) => Socket;

interface Reply {
  id: number;
  result?: { data?: string };
  error?: { message: string };
}

/** How long one DevTools call may take before the run is told, instead of hanging until Code is killed. */
const CALL_TIMEOUT_MS = 10_000;

/** One connection to the workbench page, kept for every picture of a run. */
export class Camera {
  private next = 0;
  private readonly waiting = new Map<number, (reply: Reply | Error) => void>();
  /** Why the connection is gone, once it is: every call then fails with it. */
  private lost: Error | undefined;

  private constructor(private readonly socket: Socket) {
    socket.onmessage = (event) => {
      const reply = JSON.parse(String(event.data)) as Reply;
      this.waiting.get(reply.id)?.(reply);
      this.waiting.delete(reply.id);
    };
    const drop = (why: string) => {
      this.lost ??= new Error(`GER_E2E_PNG: the DevTools connection ${why}`);
      this.waiting.forEach((settle) => settle(this.lost as Error));
      this.waiting.clear();
    };
    socket.onclose = () => drop("closed");
    socket.onerror = () => drop("failed");
  }

  /** Connect to the window whose profile wrote `activePortFile`. */
  static async open(activePortFile: string): Promise<Camera> {
    const WebSocket = (globalThis as { WebSocket?: SocketClass }).WebSocket;
    if (!WebSocket) {
      throw new Error("GER_E2E_PNG: this VS Code's extension host has no WebSocket (needs Node 22, VS Code 1.90 or later)");
    }
    let port: string;
    try {
      port = fs.readFileSync(activePortFile, "utf8").split("\n")[0].trim();
    } catch (error) {
      throw new Error(`GER_E2E_PNG: VS Code wrote no DevTools port to ${activePortFile || "(GER_E2E_DEVTOOLS is not set)"}: ${String(error)}`);
    }
    const pages = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()) as { type: string; url: string; webSocketDebuggerUrl: string }[];
    const workbench = pages.find((page) => page.type === "page" && page.url.includes("workbench"));
    if (!workbench) {
      throw new Error(`GER_E2E_PNG: no workbench page among ${pages.map((page) => page.url).join(", ")}`);
    }
    const socket = new WebSocket(workbench.webSocketDebuggerUrl);
    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error(`GER_E2E_PNG: could not connect to ${workbench.webSocketDebuggerUrl}`));
    });
    const camera = new Camera(socket);
    await camera.call("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });
    return camera;
  }

  private call(method: string, params: object = {}): Promise<Reply> {
    const id = ++this.next;
    if (this.lost) {
      return Promise.reject(this.lost);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.waiting.delete(id);
        reject(new Error(`GER_E2E_PNG: ${method} got no answer in ${CALL_TIMEOUT_MS / 1000} s`));
      }, CALL_TIMEOUT_MS);
      this.waiting.set(id, (reply) => {
        clearTimeout(timer);
        if (reply instanceof Error) {
          reject(reply);
        } else if (reply.error) {
          reject(new Error(`${method}: ${reply.error.message}`));
        } else {
          resolve(reply);
        }
      });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  /** Drag the side bar's right edge until the side bar is `width` wide. */
  async widenSideBar(width: number): Promise<void> {
    const reply = (await this.call("Runtime.evaluate", {
      expression: "JSON.stringify(document.querySelector('.part.sidebar')?.getBoundingClientRect() ?? null)",
      returnByValue: true,
    })) as Reply & { result?: { result?: { value?: string } } };
    const box = JSON.parse(reply.result?.result?.value ?? "null") as { left: number; right: number; top: number; height: number } | null;
    if (!box) {
      throw new Error("GER_E2E_PNG: the side bar is not shown");
    }
    const [x, y, to] = [box.right, box.top + box.height / 2, box.left + width];
    const mouse = (type: string, at: number, buttons: number) =>
      this.call("Input.dispatchMouseEvent", { type, x: at, y, button: "left", buttons, clickCount: 1 });
    await mouse("mouseMoved", x, 0);
    await mouse("mousePressed", x, 1);
    for (let step = 1; step <= 10; step++) {
      await mouse("mouseMoved", x + ((to - x) * step) / 10, 1);
    }
    await mouse("mouseReleased", to, 0);
    await mouse("mouseMoved", to + 300, 0); // off the edge, which is highlighted while the mouse is on it
  }

  /** The window as it is now, as a PNG into `file`. */
  async take(file: string): Promise<void> {
    const reply = await this.call("Page.captureScreenshot", { format: "png" });
    if (!reply.result?.data) {
      throw new Error(`GER_E2E_PNG: Page.captureScreenshot returned no picture for ${file}`);
    }
    fs.writeFileSync(file, Buffer.from(reply.result.data, "base64"));
  }

  close(): void {
    this.socket.close();
  }
}

/** A file name for a step: `07-local-stack-move-up.png`. */
export function pictureName(step: number, label: string): string {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
  return `${String(step).padStart(2, "0")}-${slug || "step"}.png`;
}
