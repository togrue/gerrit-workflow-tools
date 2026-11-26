// Inspection points: how fast the extension answers a user action. Off unless `ger.trace` (or
// GER_TRACE) names a file; then each user action (a command, a click or key in a webview) is
// one JSON line there: when it began, when something first showed in answer (a webview painted
// the new state, an editor opened, a picker or a status message showed), when it settled (no
// request in flight, nothing waiting to be painted, for QUIET_MS), and the requests to
// `ger serve` it caused, joined by the action's id to what `ger serve` said each one cost
// (its `X-Ger-Trace` header: git subprocesses, Gerrit requests). Format: vscode/README.md#tracing.
// No `vscode` import: see api.ts.

import { AsyncLocalStorage } from "node:async_hooks";
import * as fs from "node:fs";

export type TraceRecord = { kind: string } & Record<string, unknown>;
export type Sink = (record: TraceRecord) => void;

/** Milliseconds since the epoch, finer than `Date.now()`; a webview's page uses the same clock. */
export const now = (): number => performance.timeOrigin + performance.now();

/** Quiet this long, an action counts as settled at its last activity. */
export const QUIET_MS = 250;
/** An action still busy after this long is written as it is, marked `timeout`. */
export const MAX_MS = 60_000;
/** A render the page does not say it painted within this long (a hidden view paints nothing) stops counting. */
export const PAINT_WAIT_MS = 2_000;

/** What `ger serve` said a request cost (`X-Ger-Trace`): docu/spec/commands/serve.md#tracing-a-request. */
export interface ServeTrace {
  action: string;
  ms: number;
  queued_ms: number;
  git: { count: number; ms: number };
  gerrit: { count: number; ms: number };
  calls: [string, string, number][];
  calls_left_out: number;
}

class Action {
  /** The commands it ran on the way (a webview button runs one). */
  readonly inner: string[] = [];
  first: { at: number; what: string } | undefined;
  last: number;
  /** Requests in flight, renders not yet painted, and the action's own run while it lasts. */
  pending = 0;
  done = false;
  timer: ReturnType<typeof setTimeout> | undefined;
  readonly totals = { requests: 0, request_ms: 0, serve_ms: 0, git: 0, git_ms: 0, gerrit: 0, gerrit_ms: 0, paints: 0 };
  readonly deadline: ReturnType<typeof setTimeout>;

  constructor(
    readonly id: string,
    readonly name: string,
    readonly via: string,
    readonly start: number,
    expire: () => void,
  ) {
    this.last = start;
    this.deadline = setTimeout(expire, MAX_MS);
    this.deadline.unref?.();
  }
}

/** The action a piece of work belongs to; a prompt answered moves it on to the action that follows. */
interface Holder {
  action: Action;
}

export interface RequestSpan {
  headers: Record<string, string>;
  end(status: number, serveTrace: string | null): void;
}

export class Tracer {
  private readonly context = new AsyncLocalStorage<Holder>();
  private readonly open = new Set<Action>();
  private latest: Action | undefined;
  private readonly renders = new Map<number, { action: Action; view: string; timer: ReturnType<typeof setTimeout> }>();
  private actions = 0;
  private requests = 0;
  private seq = 0;
  private idlers: (() => void)[] = [];

  constructor(
    private readonly sink: Sink,
    private readonly clock: () => number = now,
  ) {}

  /** Write a record of the caller's own (the walk's step marks). */
  write(record: TraceRecord): void {
    this.sink({ t: round(this.clock()), ...record });
  }

  /**
   * Run `run` as the user action `name`, begun at `at` (when the page saw the click) or now.
   * Inside another action it is part of that one.
   */
  action<T>(name: string, via: string, run: () => T, at?: number): T {
    const outer = this.context.getStore();
    if (outer && !outer.action.done) {
      outer.action.inner.push(name);
      return run();
    }
    const holder: Holder = { action: this.begin(name, via, at ?? this.clock()) };
    holder.action.pending += 1;
    const release = () => this.release(holder.action);
    let result: T;
    try {
      result = this.context.run(holder, run);
    } catch (error) {
      release();
      throw error;
    }
    if (result && typeof (result as { then?: unknown }).then === "function") {
      (result as unknown as PromiseLike<unknown>).then(release, release);
    } else {
      release();
    }
    return result;
  }

  /**
   * The action waits on the user (a picker, a dialog): it settles meanwhile. The answer is a user
   * action of its own, `answered` names it; the work after it belongs to that one.
   */
  waitForUser(): (answered: string) => void {
    const holder = this.context.getStore();
    if (!holder || holder.action.done) {
      return () => undefined;
    }
    const waiting = holder.action;
    this.release(waiting);
    return (answered) => {
      holder.action = this.begin(`${waiting.name} › ${answered}`, "answer", this.clock());
      holder.action.pending += 1;
    };
  }

  /** A request to `ger serve` starts: the headers to send, and what to call when it is answered. */
  request(method: string, path: string): RequestSpan {
    const action = this.current();
    const rid = ++this.requests;
    const start = this.clock();
    if (action) {
      action.pending += 1;
      this.touch(action, start);
    }
    let ended = false;
    return {
      headers: { "X-Ger-Action": action?.id ?? "background" },
      end: (status, header) => {
        if (ended) {
          return;
        }
        ended = true;
        const at = this.clock();
        const serve = parseServeTrace(header);
        this.sink({ kind: "request", t: round(start), action: action?.id ?? null, rid, method, path, status, ms: round(at - start) });
        if (serve) {
          this.sink({ kind: "serve", t: round(start), rid, ...serve, action: action?.id ?? null });
        }
        if (action) {
          const totals = action.totals;
          totals.requests += 1;
          totals.request_ms += at - start;
          totals.serve_ms += serve?.ms ?? 0;
          totals.git += serve?.git.count ?? 0;
          totals.git_ms += serve?.git.ms ?? 0;
          totals.gerrit += serve?.gerrit.count ?? 0;
          totals.gerrit_ms += serve?.gerrit.ms ?? 0;
          this.touch(action, at);
          this.release(action);
        }
      },
    };
  }

  /** A render is posted to the page `view`: its number, which the page hands back once painted; 0 outside an action. */
  render(view: string): number {
    const action = this.context.getStore()?.action;
    if (!action || action.done) {
      return 0;
    }
    const seq = ++this.seq;
    action.pending += 1;
    this.touch(action, this.clock());
    const timer = setTimeout(() => {
      if (this.renders.delete(seq)) {
        this.release(action);
      }
    }, PAINT_WAIT_MS);
    this.renders.set(seq, { action, view, timer });
    return seq;
  }

  /** The page painted render `seq` at `at`. */
  painted(seq: number, at: number | undefined): void {
    const render = this.renders.get(seq);
    if (!render) {
      return;
    }
    this.renders.delete(seq);
    clearTimeout(render.timer);
    const when = typeof at === "number" ? at : this.clock();
    render.action.totals.paints += 1;
    this.shown(render.action, `paint:${render.view}`, when);
    this.release(render.action);
  }

  /**
   * Something showed (an editor, a picker, a status message). It answers the action it ran in;
   * shown by VS Code outside any (an editor opened), the latest one still open.
   */
  visible(what: string): void {
    const action = this.current() ?? (this.latest && !this.latest.done ? this.latest : undefined);
    if (action) {
      this.shown(action, what, this.clock());
    }
  }

  /** Resolves once no action is open. */
  idle(): Promise<void> {
    return this.open.size ? new Promise((resolve) => this.idlers.push(resolve)) : Promise.resolve();
  }

  private current(): Action | undefined {
    const action = this.context.getStore()?.action;
    return action && !action.done ? action : undefined;
  }

  private begin(name: string, via: string, at: number): Action {
    const action: Action = new Action(`a${++this.actions}`, name, via, at, () => this.finish(action, true));
    this.open.add(action);
    this.latest = action;
    return action;
  }

  private shown(action: Action, what: string, at: number): void {
    if (!action.first || at < action.first.at) {
      action.first = { at, what };
    }
    this.touch(action, at);
  }

  private touch(action: Action, at: number): void {
    action.last = Math.max(action.last, at);
    if (action.timer) {
      clearTimeout(action.timer);
      action.timer = undefined;
      if (action.pending === 0) {
        this.settleLater(action);
      }
    }
  }

  private release(action: Action): void {
    action.pending = Math.max(0, action.pending - 1);
    if (action.pending === 0 && !action.done) {
      this.settleLater(action);
    }
  }

  private settleLater(action: Action): void {
    clearTimeout(action.timer);
    action.timer = setTimeout(() => {
      if (action.pending === 0) {
        this.finish(action, false);
      }
    }, QUIET_MS);
  }

  private finish(action: Action, timeout: boolean): void {
    if (action.done) {
      return;
    }
    action.done = true;
    clearTimeout(action.timer);
    clearTimeout(action.deadline);
    this.open.delete(action);
    const t = action.totals;
    this.sink({
      kind: "action",
      t: round(action.start),
      id: action.id,
      name: action.name,
      via: action.via,
      ...(action.inner.length ? { inner: action.inner } : {}),
      first_ms: action.first ? round(action.first.at - action.start) : null,
      first: action.first?.what ?? null,
      settled_ms: round(action.last - action.start),
      requests: t.requests,
      request_ms: round(t.request_ms),
      serve_ms: round(t.serve_ms),
      git: t.git,
      git_ms: round(t.git_ms),
      gerrit: t.gerrit,
      gerrit_ms: round(t.gerrit_ms),
      paints: t.paints,
      ...(timeout ? { timeout: true } : {}),
    });
    if (!this.open.size) {
      const idlers = this.idlers;
      this.idlers = [];
      idlers.forEach((resolve) => resolve());
    }
  }
}

function round(ms: number): number {
  return Math.round(ms * 10) / 10;
}

function parseServeTrace(header: string | null): Omit<ServeTrace, "action"> | undefined {
  if (!header) {
    return undefined;
  }
  try {
    const { action: _action, ...rest } = JSON.parse(header) as ServeTrace;
    return rest;
  } catch {
    return undefined;
  }
}

/** Appends each record as one JSON line to `file`, in order. */
export function fileSink(file: string): Sink & { close(): void } {
  const stream = fs.createWriteStream(file, { flags: "a" });
  stream.on("error", () => undefined);
  const sink = (record: TraceRecord) => void stream.write(`${JSON.stringify(record)}\n`);
  return Object.assign(sink, { close: () => stream.end() });
}

let active: Tracer | undefined;

/** The tracer while tracing is on. */
export function activeTracer(): Tracer | undefined {
  return active;
}

export function useTracer(tracer: Tracer | undefined): void {
  active = tracer;
}

/** Run `run` as the user action `name` while tracing is on; else just run it. */
export function traced<T>(name: string, via: string, run: () => T, at?: number): T {
  return active ? active.action(name, via, run, at) : run();
}
