// The Change view follows what it is to show (shownChange.ts decides it): the change a review here
// holds, however the review moved (the views, a shell, a restart); the commit a rebase stopped at;
// and your selection, through rewrites and amends, said when it left the stack and shown again
// when it came back. This applies the decision on every change of the model.

import * as vscode from "vscode";

import { StackCommit } from "./api";
import { ChangeView } from "./changeView";
import { Model } from "./model";
import { Followed, ShownWhy, follow } from "./shownChange";

/** What following needs of the rest of the extension. */
export interface Shows {
  /** Show the commit a rebase stopped at, the keyboard left where it is. */
  stop(commit: StackCommit): Promise<unknown>;
  /** Show the change the review here holds now (reviewWalk.ts). */
  review(): Promise<unknown>;
}

export class ShownCommitFollower implements vscode.Disposable {
  private followed: Followed = {};
  /** Why the Change view shows what it shows; undefined while it shows nothing it was told to. */
  why: ShownWhy | undefined;
  /** Review steps started from the views that run now: each shows its own result. */
  private holds = 0;
  private readonly subscription: vscode.Disposable;

  constructor(
    private readonly model: Model,
    private readonly changeView: ChangeView,
    private readonly shows: Shows,
  ) {
    this.subscription = model.onDidChange(() => this.follow());
  }

  /** You chose what to look at: stop waiting for a lost commit to come back. */
  forget(): void {
    this.followed = { ...this.followed, lost: undefined };
    this.why = "selection";
  }

  /** Run a review step started from the views: it shows its own result, and the review it leaves is not followed again after it. */
  async hold<T>(run: () => Promise<T>): Promise<T> {
    this.holds++;
    try {
      return await run();
    } finally {
      this.holds--;
      this.why = this.model.stack?.staged_review ? "review" : this.why;
    }
  }

  private follow(): void {
    const local = this.model.local;
    const { decision, after } = follow(this.followed, {
      target: this.changeView.target,
      shownChangeId: this.changeView.shown?.change_id,
      stack: this.model.stack,
      stackError: Boolean(local && "error" in local),
      holding: this.holds > 0,
    });
    this.followed = after;
    switch (decision.kind) {
      case "review":
        this.why = "review";
        void this.shows.review();
        return;
      case "stop":
        this.why = "stop";
        void this.shows.stop(decision.commit);
        return;
      case "open":
        this.why = decision.why;
        void this.changeView.open(decision.target, decision.target.local);
        return;
      case "close":
        this.why = undefined;
        this.changeView.close(decision.why);
        return;
      case "local":
        this.changeView.setLocal(decision.local);
        return;
      default:
        return;
    }
  }

  dispose(): void {
    this.subscription.dispose();
  }
}
