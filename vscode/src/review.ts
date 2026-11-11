// Getting a change in front of you: someone's change for review (`ger review`), a change to
// work on (`ger checkout`), or your own commit to edit (`ger edit`).

import * as path from "node:path";
import * as vscode from "vscode";

import { runInTerminal } from "./launch";
import { reviewOpened } from "./format";
import { ChangeTarget, Model, describeError } from "./model";
import { ui } from "./ui";

/** Your own stack commit: `ger edit` stops an interactive rebase on it, here, to amend it in place. */
export function editCommit(model: Model, target: ChangeTarget | undefined): void {
  if (!target) {
    return;
  }
  runInTerminal(model.repo, ["edit", target.rev]);
  // It runs in the terminal: say what to do once it stopped, not that it already has.
  ui().status(`ger edit runs in the terminal: once the rebase stops at "${target.title}", amend it, then git rebase --continue`, 12000);
}

/**
 * A change that exists only on Gerrit, to work on: `ger checkout` puts it on a branch here that
 * tracks its target branch, so it is your local stack. It may ask (update to the newest patchset?),
 * so it runs in a terminal; the views catch up at the next refresh (the terminal does not say when it ends).
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
  ui().status(`ger checkout ${target.rev} runs in the terminal: it may ask before it changes anything`, 12000);
}

/** Someone's change on Gerrit: `ger review` checks it out for review: in the review worktree, or here when `ger.reviewCheckout` says `branch`. */
export async function review(model: Model, target: ChangeTarget | undefined): Promise<void> {
  if (!target) {
    return;
  }
  try {
    const client = await model.client();
    const opened = await ui().progress(vscode.ProgressLocation.Notification, `Checking out ${target.title}`, () =>
      client.reviewOpen(target.number ?? target.rev),
    );
    const where = vscode.Uri.file(opened.worktree);
    const inPlace = path.resolve(opened.worktree) === path.resolve(model.repo);
    const open = inPlace ? undefined : "Open Review Worktree";
    const picked = await ui().message(
      "info",
      reviewOpened(opened, inPlace),
      {},
      open ? [open] : [],
    );
    void model.refresh(); // a review is open now: the Change view offers to leave it
    if (picked) {
      await ui().openFolder(where);
    }
  } catch (error) {
    void ui().message("warning", `Not checked out for review: ${describeError(error)}`, {}, []);
  }
}

/** `ger review --done`: leave the review checkout, when there is one. */
export async function leaveReview(model: Model): Promise<void> {
  try {
    const client = await model.client();
    if (!(await client.review())) {
      void ui().message("info", "No review checkout is open here.", {}, []);
      return;
    }
    const done = await client.reviewDone();
    void ui().message("info", `Left the review of change ${done.done}.`, {}, []);
    void model.refresh();
  } catch (error) {
    void ui().message("warning", describeError(error), {}, []);
  }
}
