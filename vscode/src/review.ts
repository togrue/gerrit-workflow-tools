// Checking a change out: someone's change for review (`ger review`), or your own commit to edit (`ger edit`).

import * as path from "node:path";
import * as vscode from "vscode";

import { runInTerminal } from "./launch";
import { ChangeTarget, Model, describeError } from "./model";
import { ui } from "./ui";

/** Your own stack commit: `ger edit` stops an interactive rebase on it, here, to amend it in place. */
export function editCommit(model: Model, target: ChangeTarget | undefined): void {
  if (!target) {
    return;
  }
  runInTerminal(model.repo, `edit ${target.rev}`);
  // It runs in the terminal: say what to do once it stopped, not that it already has.
  void ui().message("info", `Started \`ger edit\` in the terminal. Once the rebase stops at "${target.title}", amend it, then run \`git rebase --continue\`.`, {}, []);
}

/** Someone's change on Gerrit: `ger review` checks it out for review (the review worktree by default). */
export async function checkout(model: Model, target: ChangeTarget | undefined): Promise<void> {
  if (!target) {
    return;
  }
  if (target.local) {
    editCommit(model, target); // already in your stack: nothing to fetch, edit it where it is
    return;
  }
  try {
    const client = await model.client();
    const review = await ui().progress(vscode.ProgressLocation.Notification, `Checking out ${target.title}`, () =>
      client.reviewOpen(target.number ?? target.rev),
    );
    const where = vscode.Uri.file(review.worktree);
    const inPlace = path.resolve(review.worktree) === path.resolve(model.repo);
    const open = inPlace ? undefined : "Open Review Worktree";
    const picked = await ui().message(
      "info",
      `Change ${review.number} (patchset ${review.patch_set}) is checked out${review.staged ? ", staged," : ""} on ${review.branch}${inPlace ? "" : ` in ${review.worktree}`}.`,
      {},
      open ? [open] : [],
    );
    void model.refresh(); // a review is open now: the Change view offers to leave it
    if (picked) {
      await ui().openFolder(where);
    }
  } catch (error) {
    void ui().message("warning", `Not checked out: ${describeError(error)}`, {}, []);
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
