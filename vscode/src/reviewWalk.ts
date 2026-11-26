// The review walk as the views do it: the change a staged review holds shown with all its files,
// the next one asked ahead while you read (reviewAhead.ts) so Review Next Change draws it at once
// and reads its diff from git while the checkout runs, then your files once it is checked out.
// Review Previous and a click on a neighbour in the task header take the same way: what was not
// asked ahead is asked at the press, one request before the checkout.

import * as vscode from "vscode";

import { Member } from "./api";
import { memberTarget } from "./changeLoad";
import { ChangeView } from "./changeView";
import { ChangeTarget, Model } from "./model";
import { ReviewWalk, review, stepReview } from "./review";
import { ReviewAhead } from "./reviewAhead";
import { reviewFocus } from "./reviewFocus";
import { commitTarget } from "./stackView";
import { Task } from "./task";

/** What the walk needs of the extension. */
export interface WalkViews {
  model: Model;
  changeView: ChangeView;
  task: Task;
  /** Show a change in the Change view, the keyboard left where it is. */
  open(target: ChangeTarget): Promise<void>;
  /** Vote and Publish on every open change of the chain of `number`. */
  voteChain(number: number): Promise<void>;
}

/** The member `step` places from the change under review, as the inbox has the chain. */
function neighbour(model: Model, step: number): { member: Member; target: ChangeTarget } | undefined {
  const focus = reviewFocus(model.overview, model.stack);
  const at = focus ? focus.chain.members.findIndex((member) => member.number === focus.number) : -1;
  const member = focus && at >= 0 ? focus.chain.members[at + step] : undefined;
  return member && focus ? { member, target: memberTarget(member, focus.chain, model.stack) } : undefined;
}

export interface ReviewWalking {
  walk: ReviewWalk;
  /** Show the change under review; with `all`, all its files too. */
  show(all: boolean): Promise<void>;
  /** A click on a change of the chain in the task header. */
  go(number: number): Promise<void>;
  disposable: vscode.Disposable;
}

export function reviewWalk({ model, changeView, task, open, voteChain }: WalkViews): ReviewWalking {
  const ahead = new ReviewAhead(() => model.client());

  const busy = (text: string | undefined) => {
    changeView.busy(text);
    task.busy(text && text.split("…")[0] + "…");
  };

  /** The change a staged review holds here, shown; with `all`, all its files in one editor, your files on the right. */
  const show = async (all: boolean) => {
    const staged = model.stack?.staged_review;
    if (!staged) {
      busy(undefined);
      return;
    }
    const commit = model.stack?.commits.find((c) => c.sha === staged.commit);
    const target: ChangeTarget = commit
      ? commitTarget(commit)
      : { rev: staged.commit, title: staged.subject, number: staged.number, local: true, sha: staged.commit, changeId: changeView.shown?.change_id ?? null };
    // Drawn already from what was asked ahead: its files are the commit checked out now.
    const drawn = all && changeView.target?.number === staged.number && changeView.diff?.commit === staged.commit;
    // Drawn: the same change, now your commit; kept on screen while it reloads.
    const loading = drawn ? changeView.adopt(target) : open(target);
    if (drawn) {
      await changeView.openAll();
      await changeView.closeCombined("ahead");
      busy(undefined);
    }
    await loading;
    await changeView.idle();
    busy(undefined);
    // Reviewed at an earlier patchset: what is new since is what to read, in the view and the combined diff.
    const from = changeView.from;
    await changeView.preferReviewDelta();
    await changeView.idle();
    if (all && (!drawn || changeView.from !== from)) {
      if (drawn) {
        await changeView.closeCombined();
      }
      await changeView.openAll();
    }
    await changeView.closeCombined("ahead");
  };

  const walk: ReviewWalk = {
    show: () => show(true),
    leave: () => changeView.closeCombined(),
    voteChain,
    ahead: async (step) => {
      const next = neighbour(model, step);
      if (!next) {
        return undefined;
      }
      // Not asked ahead (the change below, or the one above before it was asked): asked now, one
      // request before the checkout, so it is drawn in ~0.2 s instead of after it.
      if (!ahead.has(next.member)) {
        ahead.fetch(next.member);
      }
      const got = await ahead.take(next.member);
      if (!got) {
        return undefined;
      }
      await changeView.closeCombined();
      // Somebody's change on Gerrit until it is checked out: its diff is read from git.
      changeView.prefill({ ...next.target, rev: `change:${next.member.number}`, local: false }, got.shown, got.files);
      await changeView.openAll(undefined, "checking out…");
      return next.member.number;
    },
    busy,
  };

  /** Ask ahead for the change above the one reviewed, once it is shown. */
  const prefetch = changeView.onDidLoad(() => {
    const staged = model.stack?.staged_review;
    if (!staged) {
      ahead.forget();
      return;
    }
    const next = changeView.target?.local && changeView.target.number === staged.number ? neighbour(model, 1) : undefined;
    if (next) {
      ahead.fetch(next.member);
    }
  });

  /** A click on a change of the chain in the task header: the one reviewed is shown; a neighbour is the next step; another is reviewed. */
  const go = async (number: number) => {
    const staged = model.stack?.staged_review;
    if (!staged || number === staged.number) {
      return show(false);
    }
    const step = [1, -1].find((s) => neighbour(model, s)?.member.number === number) as 1 | -1 | undefined;
    if (step) {
      return stepReview(model, step, walk);
    }
    const target = neighbourTarget(model, number);
    return review(model, target, walk);
  };

  return { walk, show, go, disposable: prefetch };
}

/** Any member of the chain under review, by number, as a view opens it. */
function neighbourTarget(model: Model, number: number): ChangeTarget {
  const focus = reviewFocus(model.overview, model.stack);
  const member = focus?.chain.members.find((m) => m.number === number);
  return member && focus ? memberTarget(member, focus.chain, model.stack) : { rev: `change:${number}`, title: String(number), number, local: false };
}
