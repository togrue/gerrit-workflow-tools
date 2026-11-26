// Getting a change in front of you: someone's change for review (`ger review`), a change to
// work on (`ger checkout`), or your own commit to edit (`ger edit`).

import * as path from "node:path";
import * as vscode from "vscode";

import { GerApiError, ReviewState } from "./api";
import { runInTerminal } from "./launch";
import { leftReview, reviewOpened } from "./format";
import { ChangeTarget, Model, describeError } from "./model";
import { failureWays } from "./problemRows";
import { feedback, offerWaysOn, ui } from "./ui";

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
export async function review(model: Model, target: ChangeTarget | undefined, walk?: ReviewWalk, mode?: ReviewState["mode"]): Promise<void> {
  if (!target) {
    return;
  }
  try {
    const client = await model.client();
    const opened = await ui().progress(vscode.ProgressLocation.Notification, `Checking out ${target.title}`, () =>
      client.reviewOpen(target.number ?? target.rev, mode),
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
    void model.refresh(); // a checkout that failed half-way may have left a review, or none: the menus follow
    if (error instanceof GerApiError && error.reason === "uncommitted-changes" && mode !== "worktree") {
      // Your edits are in the way of a checkout here: the review worktree is not in their way.
      const files = model.stack?.review_blocked?.files ?? [];
      const what = files.length ? `${files.join(", ")} ${files.length === 1 ? "has" : "have"} uncommitted changes` : "your working tree has uncommitted changes";
      const picked = await ui().message("warning", `Not checked out for review here: ${what}. Nothing was changed.`, {}, [OVER_EDITS.worktree, OVER_EDITS.scm]);
      if (picked === OVER_EDITS.worktree) {
        await review(model, target, walk, "worktree");
      } else if (picked === OVER_EDITS.scm) {
        await vscode.commands.executeCommand("workbench.view.scm");
      }
      return;
    }
    void offerWaysOn("warning", `Not checked out for review: ${describeError(error)}`, failureWays(error));
  }
}

/** What a review over uncommitted changes here offers: the review worktree, or the changes to commit or stash. */
export const OVER_EDITS = { worktree: "Review in a Worktree Instead", scm: "Commit or Stash Them…" };

/**
 * `ger review --done`: leave the review checkout, when there is one. What belonged to the review
 * closes (its diff tabs: their right side was the review's files), and the answer says where the
 * checkout is now and what is next: the headline, once the views have caught up.
 */
export async function leaveReview(model: Model, walk?: Pick<ReviewWalk, "leave">): Promise<void> {
  try {
    const client = await model.client();
    if (!(await client.review())) {
      void ui().message("info", "No review checkout is open here.", {}, []);
      void model.refresh(); // what offered it was out of date: the menus follow what is there now
      return;
    }
    const done = await ui().progress(vscode.ProgressLocation.Window, "Leaving the review", () => client.reviewDone());
    await walk?.leave();
    await model.refresh();
    const headline = model.overview?.headline;
    const text = `${leftReview(done, model.repo)} ${headline ? `Next: ${headline.text}.` : "Nothing else waits on you now."}`;
    void ui().message("info", text, {}, headline ? [OPEN_NEXT] : []).then((picked) => (picked ? vscode.commands.executeCommand("ger.openHeadline") : undefined));
  } catch (error) {
    void ui().message("warning", `Still in the review: ${describeError(error)}`, {}, []);
  }
}

const OPEN_NEXT = "Open It";


/** What moving a staged review along its chain needs from the rest of the extension. */
export interface ReviewWalk {
  /** Show the change the staged review holds now, with all its files; says when it is done (`busy`). */
  show(): Promise<void>;
  /** Close what showed the change it left (its combined diffs: their right side is your file). */
  leave(): Promise<void>;
  /** Vote and Publish on every open change of the chain of `number`. */
  voteChain(number: number): Promise<void>;
  /**
   * Before the checkout: leave the change, and show the one `step` goes to from what is in hand
   * (asked ahead: reviewAhead.ts), its diff read from git. Its number when it could; else nothing
   * is shown and the checkout is waited for, as before.
   */
  ahead?(step: 1 | -1): Promise<number | undefined>;
  /** Say what is under way while the checkout runs; `undefined` once the change is ready to work in. */
  busy?(text: string | undefined): void;
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
  let early: number | undefined;
  const which = step > 0 ? "next" : "previous";
  try {
    const client = await model.client();
    // Drawn now from what was asked ahead; the checkout runs behind it.
    early = await walk.ahead?.(step);
    const checkout = () => client.reviewStep(step);
    if (early) {
      walk.busy?.(`Checking out ${early}… Go to Definition and edits in your files once it is checked out`);
      moved = await checkout();
    } else {
      moved = await ui().progress(vscode.ProgressLocation.Notification, `Checking out the ${which} change of ${here.number}'s chain`, checkout);
    }
  } catch (error) {
    walk.busy?.(undefined);
    if (early) {
      await walk.show(); // the review still holds the change it was on: back to it
    }
    if (error instanceof GerApiError && error.reason === "chain-end") {
      const buttons = step > 0 ? [VOTE_CHAIN, LEAVE] : [];
      const picked = await ui().message("info", `${capitalized(error.message)}.`, {}, buttons);
      if (picked === VOTE_CHAIN) {
        await walk.voteChain(here.number);
      } else if (picked === LEAVE) {
        await leaveReview(model, walk);
      }
      return;
    }
    void ui().message("warning", `The review did not move on: ${describeError(error)}`, {}, []);
    return;
  }
  if (!early) {
    await walk.leave();
  }
  // The checkout is done: the stack says so at once; /stack confirms the rest behind it. Nothing
  // moved on Gerrit, so the inbox is not asked again.
  model.adoptReview(moved);
  feedback(`Reviewing ${moved.number}: ${moved.subject} (patchset ${moved.patch_set})`, { icon: "eye" });
  const local = model.refreshLocal();
  await walk.show();
  await local;
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
