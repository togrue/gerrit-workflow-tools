// Work on Next: the attention walk of `ger edit -n`, from the views. It goes on with the rebase
// underway and stops at the next commit that needs edits, or starts the walk (`ger edit`), in the
// ger terminal as Edit Commit Here does; then the commit it stopped at is shown, the keyboard on its
// first comment to answer (or its first CI finding). What is not to run is said here, in core's
// words (`GET /rebase/refusal`), not only in the terminal. With nothing left, it offers Push.

import * as vscode from "vscode";

import { StackCommit } from "./api";
import { unsavedInRepo } from "./diffTabs";
import { runInTerminal } from "./launch";
import { Model } from "./model";
import { push, refusedNow } from "./rebase";
import { pushable } from "./nextAction";
import { WALK_WORDS, workOnNext } from "./stackRows";
import { saveBeforeContinue } from "./staleDiffs";
import { feedback, ui } from "./ui";

/** How long a step started in the terminal is followed: past it, whatever the stack does is not taken as its outcome. */
const FOLLOW_MS = 120_000;

const PUSH = "Push to Gerrit…";

interface Pending {
  /** The commit the rebase stood at when the step started; null when none was underway. */
  from: string | null;
  /** A commit after it needs edits: a rebase that finishes is only on its way there (`ger edit -n` starts the next). */
  next: boolean;
  /** Started by Continue, not by Work on Next: a stop is not walked to its first comment. */
  continued: boolean;
  until: number;
}

export class AttentionWalk implements vscode.Disposable {
  private pending: Pending | undefined;
  /** What the task header is told: a press (the last outcome is history), a refusal. Set by the extension. */
  hooks: { pressed?(): void; refused?(text: string): void } = {};
  private readonly subscription: vscode.Disposable;

  /** `arrive` shows a commit the walk stopped at, its first comment to answer (or CI finding) opened. */
  constructor(
    private readonly model: Model,
    private readonly arrive: (commit: StackCommit) => Promise<void>,
  ) {
    this.subscription = model.onDidChange(() => void this.follow());
  }

  /** Work on Next: go on to the next commit that needs edits, as `ger edit -n` does. */
  async next(): Promise<void> {
    const stack = this.model.stack;
    if (!stack) {
      return;
    }
    this.hooks.pressed?.();
    const plan = workOnNext(stack);
    if (plan.kind === "review") {
      await ui().message("info", WALK_WORDS.review, {}, []);
    } else if (plan.kind === "nothing") {
      await this.offerPush(WALK_WORDS.nothing, []);
    } else if (plan.kind === "start") {
      this.start(null, true, false);
      runInTerminal(this.model.repo, ["edit"]);
      feedback(WALK_WORDS.starts, { icon: "terminal", milliseconds: 8000 });
      // At once in the view too, until the rebase has stopped (or ended, or the walk gave up): not 2 s of nothing.
      void ui().progress({ viewId: "ger.stack" }, WALK_WORDS.starting, () => this.underway());
    } else if (await this.mayContinue()) {
      this.start(stack.rebase?.current ?? null, plan.next !== undefined, false);
      runInTerminal(this.model.repo, ["edit", "-n"]);
      feedback(plan.next ? WALK_WORDS.continues(plan.next.summary) : WALK_WORDS.last, { icon: "terminal", milliseconds: 8000 });
    }
  }

  /** Resolves once the walk just started has stopped somewhere, or is no longer followed. */
  private underway(): Promise<void> {
    return new Promise((resolve) => {
      const done = () => !this.pending || Boolean(this.model.stack?.rebase_in_progress) || Date.now() > this.pending.until;
      const listener = this.model.onDidChange(() => {
        if (done()) {
          listener.dispose();
          resolve();
        }
      });
      setTimeout(() => {
        listener.dispose();
        resolve();
      }, FOLLOW_MS);
    });
  }

  /** Continue ran in the terminal: once the rebase is finished, say so and offer the way on. */
  continued(): void {
    this.start(this.model.stack?.rebase?.current ?? null, false, true);
  }

  private start(from: string | null, next: boolean, continued: boolean): void {
    this.pending = { from, next, continued, until: Date.now() + FOLLOW_MS };
  }

  /**
   * Whether `ger edit -n` may continue the rebase: the files only an editor holds saved first
   * (asked, as Continue asks), then core's check, unstaged changes included, said here if it refuses.
   */
  private async mayContinue(): Promise<boolean> {
    return (await saveBeforeContinue(ui(), unsavedInRepo(this.model.repo))) && !(await refusedNow(this.model, "continue", WALK_WORDS.notContinued, this.hooks));
  }

  /** The stack moved: where the step started in the terminal has got to. */
  private async follow(): Promise<void> {
    const [pending, stack] = [this.pending, this.model.stack];
    if (!pending || !stack) {
      return;
    }
    if (Date.now() > pending.until) {
      this.pending = undefined;
      return;
    }
    const rebase = stack.rebase;
    if (rebase?.current && rebase.current !== pending.from) {
      // Stopped somewhere new. A conflict shows itself (the Change view follows the rebase); an edit stop of the walk opens its first comment.
      this.pending = undefined;
      const commit = stack.commits.find((c) => c.sha === rebase.current);
      if (commit && !pending.continued && rebase.stop === "edit") {
        await this.arrive(commit);
      }
      return;
    }
    if (stack.rebase_in_progress || pending.from === null || pending.next) {
      return; // still on its way: not started yet, or between one rebase and the next
    }
    this.pending = undefined;
    const left = stack.summary.needs_edit;
    await this.offerPush(left ? WALK_WORDS.finishedMore(left) : WALK_WORDS.finished, left ? [WALK_WORDS.title] : []);
  }

  /** Says `text`, offering Push only where a push would send something (P6: nothing offered that would refuse). */
  private async offerPush(text: string, more: string[]): Promise<void> {
    const stack = this.model.stack;
    const offer = stack && pushable(stack) ? [PUSH] : [];
    const picked = await ui().message("info", offer.length || more.length ? text : `${text} ${WALK_WORDS.nothingToPush}`, {}, [...more, ...offer]);
    if (picked === PUSH) {
      await push(this.model);
    } else if (picked === WALK_WORDS.title) {
      await this.next();
    }
  }

  dispose(): void {
    this.subscription.dispose();
  }
}
