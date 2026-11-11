// What every view reads: the last overview, which chains moved since you looked, and the client.

import * as vscode from "vscode";

import { GerApiError, GerClient, Headline, MoveConflict, Overview, Stack, UpstreamChoice, isStack } from "./api";
import { Seen, blockedSwaps, chainsWithActivity, rememberSeen, swapKey } from "./format";
import { GerProcess } from "./server";
import { GerState, failureState, staleReason } from "./state";

const SEEN_KEY = "ger.seen";

export interface RefreshOptions {
  /** Bypass the trust window, like `ger --refresh`. */
  fromGerrit?: boolean;
  /**
   * Nobody asked for it (a timer, the window regaining focus, Gerrit's events): a new headline is
   * news worth a notification. After your own action, or a refresh you asked for, the status bar is enough.
   */
  background?: boolean;
}

/** One change a view can open: a local stack commit or a change on Gerrit. */
export interface ChangeTarget {
  /** What `ger show` takes: a commit id for a local commit, `change:<n>` otherwise. */
  rev: string;
  title: string;
  number?: number;
  url?: string | null;
  /** A commit of the local stack: its files are in this worktree. */
  local: boolean;
  /** The chain this change sits in, base first, when it was opened from one. */
  chainRevs?: string[];
  chainKey?: string;
}

export function describeError(error: unknown): string {
  if (error instanceof GerApiError) {
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export class Model implements vscode.Disposable {
  overview: Overview | undefined;
  /** Why there is no overview: the service did not start, or did not answer. */
  failure: string | undefined;
  /**
   * Why the overview shown is out of date: a refresh failed (whoever asked for it), and the last
   * overview stays rather than blanking the views. Cleared by the next refresh that works.
   * Permanent states (no program, no setup, untrusted, no repository) do clear the overview, and so
   * does a failure when there is no overview yet.
   */
  stale: string | undefined;
  /** What the views can show; their welcome texts follow it as the `ger.state` context key. */
  state: GerState = "starting";
  /** Keys of chains with activity since they were last seen. */
  active = new Set<string>();

  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changed.event;
  private readonly news = new vscode.EventEmitter<Headline>();
  /** Fires when a background refresh brings a headline that was not the headline before. Not on the first load. */
  readonly onDidGetNewHeadline = this.news.event;
  private lastHeadline: string | undefined;
  /** Neighbours of the local stack that cannot swap places, for the stack whose head is `movesHead`. */
  private blocked = new Map<string, MoveConflict>();
  movesHead: string | null | undefined;
  /** What to offer as upstream while the branch has none (and so no stack to show). */
  upstreamChoice: UpstreamChoice | undefined;
  private refreshing: Promise<void> | undefined;
  private loadStartedAt = 0;
  private again: Promise<void> | undefined;
  private againOptions: Required<RefreshOptions> | undefined;

  constructor(
    private readonly process: GerProcess,
    private readonly memento: vscode.Memento,
    readonly repo: string,
  ) {}

  client(): Promise<GerClient> {
    if (!vscode.workspace.isTrusted) {
      return Promise.reject(new Error("ger starts once you trust this folder."));
    }
    return this.process.client();
  }

  get stack(): Stack | undefined {
    return isStack(this.overview?.local) ? this.overview?.local : undefined;
  }

  /** Reload the overview. */
  refresh(options: RefreshOptions = {}): Promise<void> {
    const wanted = { fromGerrit: options.fromGerrit ?? false, background: options.background ?? false };
    if (!this.refreshing) {
      this.refreshing = this.load(wanted).finally(() => {
        this.refreshing = undefined;
      });
      return this.refreshing;
    }
    // The load under way may have started before what this refresh is for: one more after it.
    // It reads Gerrit if any of the merged requests asked to, and is news only if all were background.
    const merged = this.againOptions;
    this.againOptions = merged ? { fromGerrit: merged.fromGerrit || wanted.fromGerrit, background: merged.background && wanted.background } : wanted;
    this.again ??= this.refreshing.then(() => {
      const next = this.againOptions;
      [this.again, this.againOptions] = [undefined, undefined];
      return this.refresh(next);
    });
    return this.again;
  }

  /** Whether a load started at or after `time` (ms since the epoch): it saw whatever happened before. */
  loadedSince(time: number): boolean {
    return this.loadStartedAt >= time;
  }

  private async load({ fromGerrit, background }: Required<RefreshOptions>): Promise<void> {
    this.loadStartedAt = Date.now();
    // A repository can make git run its own code (hooks, fsmonitor, its .vscode settings): not before you trust it.
    if (!vscode.workspace.isTrusted) {
      await this.setState("untrusted");
      this.changed.fire();
      return;
    }
    try {
      const client = await this.client();
      const first = this.overview === undefined;
      this.overview = await client.overview(fromGerrit);
      this.failure = undefined;
      this.stale = undefined;
      // Leave Review Checkout is offered only while there is one to leave.
      const review = await client.review().catch(() => null);
      void vscode.commands.executeCommand("setContext", "ger.reviewOpen", Boolean(review));
      const headline = this.overview.headline ?? undefined;
      const key = headline ? `${headline.reason}:${headline.number}` : undefined;
      // Remembered either way: what you caused yourself does not come back as news later.
      if (background && !first && headline && key !== this.lastHeadline) {
        this.news.fire(headline);
      }
      this.lastHeadline = key;
      const seen = rememberSeen(this.overview, this.memento.get<Seen>(SEEN_KEY, {}));
      this.active = chainsWithActivity(this.overview, seen);
      await this.memento.update(SEEN_KEY, seen);
      await this.setState("ready");
    } catch (error) {
      const state = failureState(error);
      if (state === "failed" && this.overview) {
        // A hiccup, whoever asked: what was shown is still the best there is. Say it is old; the next refresh that works clears it.
        this.stale = staleReason(error);
      } else {
        // What was shown may be wrong now: the views say what went wrong and what to do instead.
        this.failure = describeError(error);
        this.stale = undefined;
        this.overview = undefined;
        await this.setState(state);
      }
    }
    this.changed.fire();
    await this.loadUpstreamChoice();
    await this.loadMoves();
  }

  /** Without an upstream there is no stack to show: fetch what to suggest, so the view can offer it in one click. */
  private async loadUpstreamChoice(): Promise<void> {
    const local = this.overview?.local;
    const needed = local !== undefined && "error" in local && local.needs_upstream === true;
    if (!needed) {
      this.upstreamChoice = undefined;
      return;
    }
    try {
      this.upstreamChoice = await (await this.client()).upstream();
    } catch {
      this.upstreamChoice = undefined;
    }
    this.changed.fire();
  }

  /** The conflict preview for the stack as it is now. Best effort: without it every move is offered. */
  private async loadMoves(): Promise<void> {
    const head = this.stack?.head;
    if (head === this.movesHead) {
      return;
    }
    try {
      const moves = head ? await (await this.client()).moves() : undefined;
      if (this.stack?.head !== head) {
        return; // the stack moved on meanwhile: the next load asks again
      }
      this.blocked = blockedSwaps(moves);
      this.movesHead = head;
    } catch {
      this.blocked = new Map();
    }
    this.changed.fire();
  }

  /** Why the stack commits `lower` and `upper` (neighbours) cannot swap places, if they cannot. */
  swapConflict(lower: string | null, upper: string | null): MoveConflict | undefined {
    return lower && upper && this.stack?.head === this.movesHead ? this.blocked.get(swapKey(lower, upper)) : undefined;
  }

  /** The local stack changed here (a reorder): show it without waiting for Gerrit. */
  setStack(stack: Stack): void {
    if (this.overview) {
      this.overview = { ...this.overview, local: stack };
      this.changed.fire();
      void this.loadMoves();
    }
  }

  /** Forget the activity marker of `keys`, or of every chain. */
  async markSeen(keys?: Iterable<string>): Promise<void> {
    const mark = keys ?? this.active;
    const seen = rememberSeen(this.overview, this.memento.get<Seen>(SEEN_KEY, {}), mark);
    await this.memento.update(SEEN_KEY, seen);
    this.active = chainsWithActivity(this.overview, seen);
    this.changed.fire();
  }

  private async setState(state: GerState): Promise<void> {
    this.state = state;
    await vscode.commands.executeCommand("setContext", "ger.state", state);
    await vscode.commands.executeCommand("setContext", "ger.ready", state === "ready");
    if (state !== "ready") {
      // Derived from the service: with no service there is nothing to leave.
      await vscode.commands.executeCommand("setContext", "ger.reviewOpen", false);
    }
  }

  /** Stop `ger serve`; the next refresh starts it again. */
  restart(): void {
    this.process.stop();
    if (this.state !== "ready") {
      void this.setState("starting");
      this.changed.fire();
    }
  }

  dispose(): void {
    this.changed.dispose();
    this.news.dispose();
    this.process.stop();
  }
}
