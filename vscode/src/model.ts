// What every view reads: the last overview, which chains moved since you looked, and the client.

import * as vscode from "vscode";

import { GerApiError, GerClient, Headline, MoveConflict, Overview, Stack, UpstreamChoice, isStack } from "./api";
import { Seen, blockedSwaps, chainsWithActivity, rememberSeen, swapKey } from "./format";
import { GerProcess } from "./server";
import { GerState, failureState } from "./state";

const SEEN_KEY = "ger.seen";

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
  /** What the views can show; their welcome texts follow it as the `ger.state` context key. */
  state: GerState = "starting";
  /** Keys of chains with activity since they were last seen. */
  active = new Set<string>();

  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changed.event;
  private readonly news = new vscode.EventEmitter<Headline>();
  /** Fires when a refresh brings a headline that was not the headline before. Not on the first load. */
  readonly onDidGetNewHeadline = this.news.event;
  private lastHeadline: string | undefined;
  /** Neighbours of the local stack that cannot swap places, for the stack whose head is `movesHead`. */
  private blocked = new Map<string, MoveConflict>();
  movesHead: string | null | undefined;
  /** What to offer as upstream while the branch has none (and so no stack to show). */
  upstreamChoice: UpstreamChoice | undefined;
  private refreshing: Promise<void> | undefined;
  private again: Promise<void> | undefined;
  private againFromGerrit = false;

  constructor(
    private readonly process: GerProcess,
    private readonly memento: vscode.Memento,
    readonly repo: string,
  ) {}

  client(): Promise<GerClient> {
    return this.process.client();
  }

  get stack(): Stack | undefined {
    return isStack(this.overview?.local) ? this.overview?.local : undefined;
  }

  /** Reload the overview. `fromGerrit` bypasses the trust window, like `ger --refresh`. */
  refresh(fromGerrit = false): Promise<void> {
    if (!this.refreshing) {
      this.refreshing = this.load(fromGerrit).finally(() => {
        this.refreshing = undefined;
      });
      return this.refreshing;
    }
    // The load under way may have started before what this refresh is for: one more after it.
    this.againFromGerrit ||= fromGerrit;
    this.again ??= this.refreshing.then(() => {
      const fresh = this.againFromGerrit;
      [this.again, this.againFromGerrit] = [undefined, false];
      return this.refresh(fresh);
    });
    return this.again;
  }

  private async load(fromGerrit: boolean): Promise<void> {
    try {
      const client = await this.client();
      const first = this.overview === undefined;
      this.overview = await client.overview(fromGerrit);
      this.failure = undefined;
      // Leave Review Checkout is offered only while there is one to leave.
      const review = await client.review().catch(() => null);
      void vscode.commands.executeCommand("setContext", "ger.reviewOpen", Boolean(review));
      const headline = this.overview.headline ?? undefined;
      const key = headline ? `${headline.reason}:${headline.number}` : undefined;
      if (!first && headline && key !== this.lastHeadline) {
        this.news.fire(headline);
      }
      this.lastHeadline = key;
      const seen = rememberSeen(this.overview, this.memento.get<Seen>(SEEN_KEY, {}));
      this.active = chainsWithActivity(this.overview, seen);
      await this.memento.update(SEEN_KEY, seen);
      await this.setState("ready");
    } catch (error) {
      // What was shown may be stale now: the views say what went wrong and what to do instead.
      this.failure = describeError(error);
      this.overview = undefined;
      await this.setState(failureState(error));
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
