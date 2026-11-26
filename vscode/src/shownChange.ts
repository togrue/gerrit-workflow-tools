// Which change the Change view shows, and why: the one owner of that choice. Three reasons decide
// it, in this order: the review going on here (its change, wherever the review was moved from: the
// views, a shell, a restart), the commit a rebase stopped at, and your selection. Your selection is
// followed through what git does to it: a rewrite or an amend gives your commit a new id, found
// again by its Change-Id; a commit that left the stack is said to have left it, and shown again
// when it comes back. Pure: no `vscode`. follow.ts applies what this decides.

import type { ReviewState, Stack, StackCommit } from "./api";
import type { ChangeTarget } from "./model";

/** Why the Change view shows what it shows. */
export type ShownWhy = "review" | "stop" | "selection";

/** What the Change view says once the commit it showed is in no local stack here any more. */
export const LEFT_THE_STACK = (title: string) => `"${title}" is not in the local stack any more (a checkout, a drop or a squash).`;

/** What it says while the local stack cannot be read (no upstream, a gone one): the commit shown is in none now. */
export const STACK_UNREADABLE = (title: string) => `"${title}" shows here again once the local stack can be read: Local stack says why it cannot.`;

/** The change a staged review holds, as the Change view opens it: your files hold it, so it is local. */
export function reviewTarget(review: ReviewState, stack: Stack | undefined): ChangeTarget {
  const commit = stack?.commits.find((c) => c.sha === review.commit);
  return {
    rev: review.commit,
    title: commit?.summary ?? review.subject,
    number: review.number,
    url: commit?.gerrit_url,
    local: true,
    // The review's own Change-Id: never the one of the change shown before (state 11 showed the abandoned change on).
    changeId: commit?.change_id ?? review.change_id ?? null,
    sha: review.commit,
    ...(commit ? { known: commit } : {}),
  };
}

/** A change no longer checked out for review: its page on Gerrit, as Review chains opens it. */
export function gerritTarget(target: ChangeTarget): ChangeTarget {
  const number = target.number;
  return { rev: number ? `change:${number}` : target.rev, title: target.title, number, url: target.url, local: false, changeId: target.changeId, sha: target.sha };
}

/** What the owner remembers between two looks. */
export interface Followed {
  /** The review going on when last looked: number, patchset and commit. */
  review?: Pick<ReviewState, "number" | "patch_set" | "commit">;
  /** The commit the rebase underway stopped at, by Change-Id (an amend there keeps it). */
  stop?: string;
  /** Your selection, gone from the stack: shown again when a commit with its Change-Id comes back. */
  lost?: { target: ChangeTarget; changeId: string };
}

/** What there is to look at. */
export interface Seen {
  target: ChangeTarget | undefined;
  /** The Change-Id `/show` answered for `target`, when it has. */
  shownChangeId: string | null | undefined;
  stack: Stack | undefined;
  /** The local stack could not be read (no upstream, a gone one): an answer, not a stack. */
  stackError: boolean;
  /** A review step started from the views runs: it shows its own result. */
  holding: boolean;
}

export type Decision =
  | { kind: "none" }
  | { kind: "review" }
  | { kind: "open"; target: ChangeTarget; why: ShownWhy }
  | { kind: "stop"; commit: StackCommit }
  | { kind: "close"; why: string }
  | { kind: "local"; local: boolean };

const reviewKey = (review: Followed["review"] | null | undefined) => (review ? `${review.number}/${review.patch_set}/${review.commit}` : "");

/** What the Change view is to show now, from what was followed before and what is seen now; and what to remember. */
export function follow(before: Followed, now: Seen): { decision: Decision; after: Followed } {
  const { target, stack } = now;
  const review = stack?.staged_review ?? undefined;
  const after: Followed = { ...before, review: review ? { number: review.number, patch_set: review.patch_set, commit: review.commit } : before.review };
  const none = { decision: { kind: "none" } as Decision, after };
  // 1. The review moved: started, stepped, updated or ended, from wherever.
  if (stack && reviewKey(review) !== reviewKey(before.review)) {
    after.review = review ? after.review : undefined;
    if (now.holding) {
      return none; // the step shows its own result
    }
    if (review) {
      const shows = target?.local && target.number === review.number && target.rev === review.commit;
      return shows ? none : { decision: { kind: "review" }, after };
    }
    const left = before.review;
    if (left && target?.local && target.number === left.number) {
      // You left its review: the change is no commit of your stack, and never was. Its Gerrit page.
      return { decision: { kind: "open", target: gerritTarget(target), why: "selection" }, after };
    }
  }
  if (review && target?.number === review.number) {
    return none; // the review's change: nothing of your stack to follow
  }
  if (!stack) {
    if (now.stackError && target?.local) {
      const changeId = target.changeId ?? now.shownChangeId;
      return { decision: { kind: "close", why: STACK_UNREADABLE(target.title) }, after: { ...after, lost: changeId ? { target, changeId } : undefined } };
    }
    return none;
  }
  // 2. The rebase stopped at another commit than when last looked (or it has just stopped).
  const current = stack.rebase?.current;
  const stopped = current ? stack.commits.find((c) => c.sha === current) : undefined;
  after.stop = stopped ? (stopped.change_id ?? stopped.sha) : undefined;
  if (stopped && after.stop !== before.stop) {
    return { decision: { kind: "stop", commit: stopped }, after };
  }
  // 3. Your selection, followed through what git did to it.
  if (!target && before.lost) {
    const lost = before.lost;
    const back = stack.commits.find((c) => c.change_id === lost.changeId);
    return back ? { decision: { kind: "open", target: { ...lost.target, rev: back.sha, sha: back.sha, title: back.summary, known: back }, why: "selection" }, after: { ...after, lost: undefined } } : none;
  }
  const changeId = target?.changeId ?? now.shownChangeId ?? undefined;
  if (!target?.local || stack.commits.some((c) => c.sha === target.rev)) {
    // After a checkout, a commit or a rebase here the change shown may have become one of your stack's: the title buttons follow.
    const local = Boolean(target?.local) || (changeId !== undefined && stack.commits.some((c) => c.change_id === changeId));
    return target && local !== target.local ? { decision: { kind: "local", local }, after } : none;
  }
  // A commit with no Change-Id (never meant for Gerrit yet) is found again by its subject, if no other has it.
  const same = stack.commits.filter((c) => !c.change_id && c.summary === target.title);
  const moved = changeId ? stack.commits.find((c) => c.change_id === changeId) : same.length === 1 ? same[0] : undefined;
  if (moved) {
    return { decision: { kind: "open", target: { ...target, rev: moved.sha, sha: moved.sha, title: moved.summary, known: moved }, why: "selection" }, after };
  }
  return { decision: { kind: "close", why: LEFT_THE_STACK(target.title) }, after: { ...after, lost: changeId ? { target, changeId } : undefined } };
}
