// Getting a change in front of you: someone's change for review (`ger review`), a change to
// work on (`ger checkout`), or your own commit to edit (`ger edit`).

import * as path from "node:path";
import * as vscode from "vscode";

import { GerApiError, ReviewState } from "./api";
import { runInTerminal } from "./launch";
import { reviewOpened } from "./format";
import { ChangeTarget, Model, describeError } from "./model";
import { feedback, ui } from "./ui";

/** Your own stack commit: `ger edit` stops an interactive rebase on it, here, to amend it in place. */
export function editCommit(model: Model, target: ChangeTarget | undefined): void {
  if (!target) {
    return;
  }
  runInTerminal(model.repo, ["edit", target.rev]);
  // It runs in the terminal: say what to do once it stopped, not that it already has.
  feedback(`ger edit runs in the ger terminal: once the rebase stops at "${target.title}", amend it, then ger rebase --continue`, { icon: "terminal", milliseconds: 12000 });
}

/**
 * A change that exists only on Gerrit, to work on: `ger checkout` puts it on a branch here that
 * tracks its target branch, so it is your local stack. It may ask (update to the newest patchset?),
 * so it runs in a terminal; the views follow once it moves HEAD (repoWatcher.ts), as the terminal does not say when it ends.
 */
export function checkout(model: Model, target: ChangeTarget | undefined): void {
  if (!target) {
    return;
  }
  if (target.local) {
    editCommit(model, target); // already in your stack: nothing to fetch, edit it where it is
    return;
  }
  runInTerminal(model.repo, ["checkout", target.rev]);
  feedback(`ger checkout ${target.rev} runs in the ger terminal: it may ask before it changes anything`, { icon: "terminal", milliseconds: 12000 });
}

/** Someone's change on Gerrit: `ger review` checks it out for review: in the review worktree, or here when `ger.reviewCheckout` says `branch`. */
export async function review(model: Model, target: ChangeTarget | undefined, walk?: ReviewWalk): Promise<void> {
  if (!target) {
    return;
  }
  try {
    const client = await model.client();
    const opened = await ui().progress(vscode.ProgressLocation.Notification, `Checking out ${target.title}`, () =>
      client.reviewOpen(target.number ?? target.rev),
    );
    const where = vscode.Uri.file(opened.worktree);
    if (path.resolve(opened.worktree) === path.resolve(model.repo)) {
      void ui().message("info", reviewOpened(opened, true), {}, []);
      if (opened.staged && walk) {
        // Checked out here: the change under review is shown with all its files, as in a review window.
        await walk.leave();
        await model.refresh();
        await walk.show();
      } else {
        void model.refresh(); // a review is open now: the Change view offers to leave it
      }
      return;
    }
    const picked = await ui().message("info", reviewOpened(opened, false), {}, ["Open Review Worktree"]);
    void model.refresh(); // a review is open now: the Change view offers to leave it
    if (picked) {
      await ui().openFolder(where);
    }
  } catch (error) {
    void ui().message("warning", `Not checked out for review: ${describeError(error)}`, {}, []);
    void model.refresh(); // a checkout that failed half-way may have left a review, or none: the menus follow
  }
}

/** `ger review --done`: leave the review checkout, when there is one. */
export async function leaveReview(model: Model): Promise<void> {
  try {
    const client = await model.client();
    if (!(await client.review())) {
      void ui().message("info", "No review checkout is open here.", {}, []);
      void model.refresh(); // what offered it was out of date: the menus follow what is there now
      return;
    }
    const done = await client.reviewDone();
    void ui().message("info", `Left the review of change ${done.done}.`, {}, []);
    void model.refresh();
  } catch (error) {
    void ui().message("warning", describeError(error), {}, []);
  }
}

/** What moving a staged review along its chain needs from the rest of the extension. */
export interface ReviewWalk {
  /** Show the change the staged review holds now, with all its files. */
  show(): Promise<void>;
  /** Close what showed the change it left (its combined diffs: their right side is your file). */
  leave(): Promise<void>;
  /** Vote and Publish on every open change of the chain of `number`. */
  voteChain(number: number): Promise<void>;
}

const VOTE_CHAIN = "Vote on the Chain…";
const LEAVE = "Leave Review";

/**
 * Review Next (`step` 1) or Previous (-1) Change in Chain: `ger review --next` / `--prev` moves the
 * staged review here to the change above or below, and it is shown with all its files. At the top
 * of the chain it says so and offers what `ger review`'s own `next:` line does there: a vote on
 * the chain, and leaving the review.
 */
export async function stepReview(model: Model, step: 1 | -1, walk: ReviewWalk): Promise<void> {
  const here = model.stack?.staged_review;
  if (!here) {
    void ui().message("info", "No staged review here: Review Change on a change of Review chains starts one.", {}, []);
    return;
  }
  let moved: ReviewState;
  try {
    const client = await model.client();
    const which = step > 0 ? "next" : "previous";
    moved = await ui().progress(vscode.ProgressLocation.Notification, `Checking out the ${which} change of ${here.number}'s chain`, () => client.reviewStep(step));
  } catch (error) {
    if (error instanceof GerApiError && error.reason === "chain-end") {
      const buttons = step > 0 ? [VOTE_CHAIN, LEAVE] : [];
      const picked = await ui().message("info", `${capitalized(error.message)}.`, {}, buttons);
      if (picked === VOTE_CHAIN) {
        await walk.voteChain(here.number);
      } else if (picked === LEAVE) {
        await leaveReview(model);
      }
      return;
    }
    void ui().message("warning", `The review did not move on: ${describeError(error)}`, {}, []);
    return;
  }
  await walk.leave();
  await model.refresh();
  feedback(`Reviewing ${moved.number}: ${moved.subject} (patchset ${moved.patch_set})`, { icon: "eye" });
  await walk.show();
}

const capitalized = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * Update to patchset N: the change a staged review holds here, behind Gerrit, is checked out
 * again at its newest patchset (`ger review CHANGE`, in the review's own mode), then shown from
 * what is new since your review, with all its files.
 */
export async function updateReview(model: Model, walk: ReviewWalk): Promise<void> {
  const here = model.stack?.staged_review;
  if (!here) {
    void ui().message("info", "No staged review here to update.", {}, []);
    return;
  }
  let opened: ReviewState;
  try {
    const client = await model.client();
    opened = await ui().progress(vscode.ProgressLocation.Notification, `Checking out the newest patchset of ${here.number}`, () => client.reviewOpen(here.number, here.mode));
  } catch (error) {
    void ui().message("warning", `Not updated: ${describeError(error)}`, {}, []);
    return;
  }
  await walk.leave();
  await model.refresh();
  feedback(`Reviewing ${opened.number}: patchset ${opened.patch_set}, staged`, { icon: "eye" });
  await walk.show();
}
