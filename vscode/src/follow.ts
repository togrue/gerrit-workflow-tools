// The Change view follows the commit it shows. A rewrite or an amend gives your commits new
// ids: find the shown one again by its Change-Id. When it goes (folded or dropped), remember
// it: an Undo brings it back, and the view with it. A rebase that stops somewhere new (an edit
// stop, a conflict) is followed there: the commit it stopped at is what wants you.

import * as vscode from "vscode";

import { StackCommit } from "./api";
import { ChangeView } from "./changeView";
import { ChangeTarget, Model } from "./model";

export class ShownCommitFollower implements vscode.Disposable {
  private lost: { target: ChangeTarget; changeId: string } | undefined;
  /** The commit the rebase underway stopped at, by Change-Id (an amend there keeps it), when last looked; undefined without one. */
  private stop: string | undefined;
  private readonly subscription: vscode.Disposable;

  /** `showStop` shows the commit a rebase stopped at, the keyboard left where it is. */
  constructor(
    private readonly model: Model,
    private readonly changeView: ChangeView,
    private readonly showStop: (commit: StackCommit) => Promise<unknown>,
  ) {
    this.subscription = model.onDidChange(() => this.follow());
  }

  /**
   * The rebase stopped at another commit than when last looked (or it has just stopped): show
   * that one, whatever was clicked before. Staying at one stop (an amend there, a conflict
   * resolved) leaves the view to you.
   */
  private followStop(): boolean {
    const stack = this.model.stack;
    const current = stack?.rebase?.current;
    const commit = current ? stack?.commits.find((c) => c.sha === current) : undefined;
    const stop = commit ? (commit.change_id ?? commit.sha) : undefined;
    const moved = stop !== undefined && stop !== this.stop;
    this.stop = stop;
    if (moved && commit) {
      void this.showStop(commit);
    }
    return moved;
  }

  /** You chose what to look at: stop waiting for a lost commit to come back. */
  forget(): void {
    this.lost = undefined;
  }

  private follow(): void {
    const target = this.changeView.target;
    const stack = this.model.stack;
    if (!stack || this.followStop()) {
      return;
    }
    if (!target && this.lost) {
      const lost = this.lost;
      const back = stack.commits.find((c) => c.change_id === lost.changeId);
      if (back) {
        this.lost = undefined;
        void this.changeView.open({ ...lost.target, rev: back.sha, title: back.summary, known: back }, true);
      }
      return;
    }
    if (!target?.local || stack.commits.some((c) => c.sha === target.rev)) {
      // After a checkout, a commit or a rebase here the shown change may have become one of your stack's: the title buttons follow.
      const changeId = this.changeView.shown?.change_id;
      this.changeView.setLocal(Boolean(target?.local) || (changeId !== undefined && stack.commits.some((c) => c.change_id === changeId)));
      return;
    }
    const changeId = this.changeView.shown?.change_id;
    // A commit with no Change-Id (never meant for Gerrit yet) is found again by its subject, if no other has it.
    const same = stack.commits.filter((c) => !c.change_id && c.summary === target.title);
    const moved = changeId ? stack.commits.find((c) => c.change_id === changeId) : same.length === 1 ? same[0] : undefined;
    if (moved) {
      void this.changeView.open({ ...target, rev: moved.sha, title: moved.summary, known: moved }, true);
    } else {
      this.lost = changeId ? { target, changeId } : undefined;
      this.changeView.close();
    }
  }

  dispose(): void {
    this.subscription.dispose();
  }
}
