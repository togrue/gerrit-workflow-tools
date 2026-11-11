// The Change view follows the commit it shows. A rewrite or an amend gives your commits new
// ids: find the shown one again by its Change-Id. When it goes (folded or dropped), remember
// it: an Undo brings it back, and the view with it.

import * as vscode from "vscode";

import { ChangeView } from "./changeView";
import { ChangeTarget, Model } from "./model";

export class ShownCommitFollower implements vscode.Disposable {
  private lost: { target: ChangeTarget; changeId: string } | undefined;
  private readonly subscription: vscode.Disposable;

  constructor(
    private readonly model: Model,
    private readonly changeView: ChangeView,
  ) {
    this.subscription = model.onDidChange(() => this.follow());
  }

  /** You chose what to look at: stop waiting for a lost commit to come back. */
  forget(): void {
    this.lost = undefined;
  }

  private follow(): void {
    const target = this.changeView.target;
    const stack = this.model.stack;
    if (!stack) {
      return;
    }
    if (!target && this.lost) {
      const lost = this.lost;
      const back = stack.commits.find((c) => c.change_id === lost.changeId);
      if (back) {
        this.lost = undefined;
        void this.changeView.open({ ...lost.target, rev: back.sha, title: back.summary });
      }
      return;
    }
    if (!target?.local || stack.commits.some((c) => c.sha === target.rev)) {
      return;
    }
    const changeId = this.changeView.shown?.change_id;
    const moved = changeId ? stack.commits.find((c) => c.change_id === changeId) : undefined;
    if (moved) {
      void this.changeView.open({ ...target, rev: moved.sha, title: moved.summary });
    } else {
      this.lost = changeId ? { target, changeId } : undefined;
      this.changeView.close();
    }
  }

  dispose(): void {
    this.subscription.dispose();
  }
}
