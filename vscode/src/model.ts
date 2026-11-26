// What every view reads: the last overview, which chains moved since you looked, and the client.

import * as path from "node:path";
import * as vscode from "vscode";

import { ApiError, GerApiError, GerClient, Headline, MoveConflict, Overview, ReviewState, Stack, UpstreamChoice, isStack } from "./api";
import { Seen, chainsWithActivity, currentHeadline, headlineNews, rememberSeen } from "./format";
import { MovePreview } from "./movePreview";
import type { RowStatus } from "./changeLoad";
import { IncompatibleServe } from "./compat";
import { GerProcess, ServeFailed } from "./server";
import { GerState, failureState, staleReason } from "./state";
import { gerritAway } from "./problemRows";

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
  changeId?: string | null;
  /** The commit: the local one for a commit of the local stack, else the current patch set's. */
  sha?: string | null;
  /** The chain this change sits in, base first, when it was opened from one. */
  chainRevs?: string[];
  chainKey?: string;
  /** What the clicked row says about the change: the Change view draws it until `/show` answers. */
  known?: RowStatus;
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
  /** Why `ger serve` was refused, while the state is `incompatible`: the views say which side to update. */
  incompatible: IncompatibleServe | undefined;
  /**
   * While the state is `setup`: the setting ger names as the cause, as core words it (`no-web-url`,
   * `insecure-http`); empty when it names none. The welcome text follows it as `ger.setupReason`.
   */
  setupReason = "";
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
  /** Local stack's conflict preview: which neighbours cannot swap places. */
  private readonly moves = new MovePreview(
    () => this.client(),
    () => this.stack,
    () => this.changed.fire(),
  );
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
  ) {
    // ger serve died under the views: start it again now (quietly, as any background refresh). A
    // restart that fails leaves the last answer marked out of date, with the reason and Restart.
    process.onDied = () => void this.refresh({ background: true });
  }

  client(): Promise<GerClient> {
    if (!vscode.workspace.isTrusted) {
      return Promise.reject(new Error("ger starts once you trust this folder."));
    }
    return this.process.client();
  }

  /**
   * The local stack read from git alone before the first overview came (`GET /stack?local=1`):
   * Local stack draws it while Gerrit is being asked, which may take as long as Gerrit's timeout.
   * Dropped once an overview arrives.
   */
  early: Stack | undefined;
  /**
   * The review open for this checkout (`GET /review`): here, or in the review worktree, whose own
   * window then holds it. Undefined without one, and while it is not known.
   */
  review: ReviewState | undefined;

  /** The review worktree holding a review of change `number` in another window: its path, when it does. */
  reviewElsewhere(number: number | undefined): string | undefined {
    const review = this.review;
    return review && number !== undefined && review.number === number && !this.stack?.staged_review && path.resolve(review.worktree) !== path.resolve(this.repo) ? review.worktree : undefined;
  }

  /** The chain of the staged review here at its patchset, base first (`/review?chain=1`); undefined without one. */
  reviewChain: number[] | undefined;
  /** Every change of that chain, closed ones too with Gerrit's status (`/review?chain=1`'s `members`, API 1.10). */
  reviewMembers: ReviewState["members"];
  /** Subjects of the changes the inbox listed this session: the task header names a change it no longer lists. */
  readonly subjects = new Map<number, string>();
  /** When the last overview that Gerrit answered came; undefined before the first. */
  loadedAt: Date | undefined;
  /** Since when Gerrit could not be asked (unreachable, no answer, refused credentials); undefined while it can. */
  awaySince: Date | undefined;
  /** Seconds Gerrit is given to answer, as the running ger serve said (`/health`'s `gerrit_wait`). */
  gerritWait: number | undefined;

  /** The local stack as the views draw it: the overview's, else the one git alone gave meanwhile. */
  get local(): Overview["local"] | undefined {
    return this.overview?.local ?? this.early;
  }

  get stack(): Stack | undefined {
    const local = this.local;
    return isStack(local) ? local : undefined;
  }

  /** The headline the views show: a rebase underway as the stack says it, else the overview's (`currentHeadline`). */
  get headline(): Headline | undefined {
    return currentHeadline(this.overview, this.stack);
  }

  /** Why Gerrit could not be asked in the last overview (the inbox's error, else the stack's), when it could not. */
  gerritAway(): ApiError | undefined {
    const inbox = this.overview?.error;
    return inbox && gerritAway(inbox) ? inbox : this.stack?.gerrit_unavailable;
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

  /**
   * Reload only the local stack (`/stack`): after a checkout here, which moves nothing on Gerrit
   * and nothing in the inbox. Counts as a load for {@link loadedSince}, so the repository watcher
   * does not reload everything after it.
   */
  async refreshLocal(): Promise<void> {
    if (this.refreshing) {
      await this.refreshing;
    }
    this.loadStartedAt = Date.now();
    try {
      const stack = await (await this.client()).stack();
      // A rebase headline that ended with this stack hid the inbox's: that one is asked again.
      const ended = this.overview?.headline?.section === "local" && stack.headline === null;
      this.setStack(stack);
      this.stackContext();
      if (ended) {
        void this.refresh();
      }
    } catch {
      await this.refresh(); // a whole load says what went wrong, as any refresh does
    }
  }

  /**
   * A review step here has checked out `review` (POST /review/open answered): the stack says so at
   * once, before `/stack` confirms the rest. Local state the checkout has already made true.
   */
  adoptReview(review: ReviewState): void {
    // The review moved here: it is the review now, with the chain the step walked (served with it). A
    // refresh Gerrit then refuses keeps that chain, as it is the same review (F4: "change 1 of 1").
    if (review.chain?.length) {
      this.reviewChain = review.chain;
    }
    this.review = review;
    const stack = this.stack;
    if (stack?.staged_review && this.overview) {
      this.overview = { ...this.overview, local: { ...stack, staged_review: { ...stack.staged_review, ...review } } };
      this.changed.fire();
    }
  }

  /** The context keys the stack decides. */
  private stackContext(): void {
    // Review Next / Previous Change in Chain, while a staged review sits here (core's fact).
    void vscode.commands.executeCommand("setContext", "ger.stagedReview", Boolean(this.stack?.staged_review));
    // Continue, Abort and Skip are in the palette only while there is a rebase to go on with.
    void vscode.commands.executeCommand("setContext", "ger.rebasing", Boolean(this.stack?.rebase_in_progress));
    // Skip only where core allows it: at any other stop git would only throw away what is not committed.
    void vscode.commands.executeCommand("setContext", "ger.rebaseSkippable", Boolean(this.stack?.rebase?.actions.includes("skip")));
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
      if (first && this.state !== "asking") {
        // ger serve runs; its first answer asks Gerrit, which may not answer for a while. What git
        // alone knows is drawn first: one quick request, served before the overview.
        this.gerritWait = client.served?.gerrit_wait;
        await this.setState("asking");
        this.early = await client.stack(true).catch(() => undefined);
        this.changed.fire();
      }
      this.overview = await client.overview(fromGerrit);
      this.early = undefined;
      for (const member of (this.overview.sections ?? []).flatMap((section) => section.chains.flatMap((chain) => chain.members))) {
        this.subjects.set(member.number, member.subject);
      }
      this.failure = undefined;
      this.incompatible = undefined;
      this.setupReason = "";
      this.stale = undefined;
      // Leave Review Checkout is offered only while there is one to leave. While one is open, its
      // chain at the reviewed patchset too (one Gerrit request): the task header keeps the members
      // the inbox no longer lists.
      // Gerrit away: the chain cannot be asked, but the review is still git's to say; the chain last
      // known for the same review stays, so the header does not lose it.
      const staged = this.stack?.staged_review;
      const review = await client.review(Boolean(staged)).catch(() => client.review(false)).catch(() => null);
      const same = review && this.review?.number === review.number && this.review.patch_set === review.patch_set;
      // `[]` is core's "Gerrit cannot say": not known, so not a chain of none.
      this.reviewChain = review?.chain?.length ? review.chain : same ? this.reviewChain : undefined;
      this.reviewMembers = review?.members?.length ? review.members : same ? this.reviewMembers : undefined;
      this.review = review ?? undefined;
      void vscode.commands.executeCommand("setContext", "ger.reviewOpen", Boolean(review));
      this.stackContext();
      const headline = this.overview.headline ?? undefined;
      // Remembered either way: what you caused yourself does not come back as news later.
      const { news, last } = headlineNews(this.lastHeadline, headline, background && !first);
      if (news && headline) {
        this.news.fire(headline);
      }
      this.lastHeadline = last;
      const seen = rememberSeen(this.overview, this.memento.get<Seen>(SEEN_KEY, {}));
      this.active = chainsWithActivity(this.overview, seen);
      await this.memento.update(SEEN_KEY, seen);
      // When the views last heard from Gerrit: said while they cannot follow it live. Not when Gerrit could not be asked;
      // then since when it could not (the first refusal after the last answer).
      const away = this.gerritAway();
      this.loadedAt = away ? this.loadedAt : new Date();
      this.awaySince = away ? (this.awaySince ?? new Date()) : undefined;
      await this.setState("ready");
    } catch (error) {
      const state = failureState(error);
      if (state === "failed" && this.overview) {
        // A hiccup, whoever asked: what was shown is still the best there is. Say it is old; the next refresh that works clears it.
        this.stale = staleReason(error);
        // Not known now whether a review checkout is open: no Leave offered on an old answer.
        this.review = undefined;
        await vscode.commands.executeCommand("setContext", "ger.reviewOpen", false);
      } else {
        // What was shown may be wrong now: the views say what went wrong and what to do instead.
        this.failure = describeError(error);
        this.incompatible = error instanceof IncompatibleServe ? error : undefined;
        this.setupReason = state === "setup" && (error instanceof GerApiError || error instanceof ServeFailed) ? (error.reason ?? "") : "";
        await vscode.commands.executeCommand("setContext", "ger.setupReason", this.setupReason);
        this.stale = undefined;
        this.overview = undefined;
        this.early = undefined;
        await this.setState(state);
      }
    }
    this.changed.fire();
    await this.loadUpstreamChoice();
    await this.moves.load();
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

  /** Why the stack commits `lower` and `upper` (neighbours) cannot swap places, if they cannot. */
  swapConflict(lower: string | null, upper: string | null): MoveConflict | undefined {
    return this.moves.conflict(lower, upper);
  }

  /** The local stack changed here (a reorder): show it without waiting for Gerrit. */
  setStack(stack: Stack): void {
    if (this.overview) {
      this.overview = { ...this.overview, local: stack };
      this.changed.fire();
      void this.moves.load();
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
      await vscode.commands.executeCommand("setContext", "ger.stagedReview", false);
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
