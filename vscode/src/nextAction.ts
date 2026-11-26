// Next: the one thing to do now on the change shown, named on the Change view's primary button and
// done by one key (`ger.next`). Derived from what ger serve sent and what was already looked at
// here; the other actions stay where they were. Pure: no `vscode`, no I/O. The rules, as a table:
// vscode/README.md, "Next: what it names, and when"; test/next.test.ts checks each row.

import { CommentChain, ShownCommit, Stack } from "./api";
import { draftsIn, toAnswer } from "./changeRows";
import { WALK_WORDS, workOnNext } from "./stackRows";

/** What Next does; extension.ts maps each to its command. */
export type NextKind = "comment" | "file" | "seen" | "vote" | "nextChange" | "leave" | "update" | "conflict" | "continue" | "workOnNext" | "push" | "review" | "gerrit" | "reviewWindow";

export interface NextAction {
  kind: NextKind;
  /** The button's text: what it will do. */
  label: string;
  /** Its tooltip: why this is next. */
  title: string;
}

export interface NextState {
  stack: Stack | undefined;
  /** `/show`'s answer for the change shown; Next waits for it. */
  shown: ShownCommit | undefined;
  chains: readonly CommentChain[];
  /** A commit of your local stack. */
  local: boolean;
  /** The change a staged review holds here (core's `staged_review`). */
  reviewing: boolean;
  /** The chains already gone to on this change. */
  visited: ReadonlySet<string>;
  /** The files of the comparison shown, and those seen (Gerrit's reviewed flag, or opened here). */
  files: readonly string[];
  seenFiles: ReadonlySet<string>;
  /** The combined diff of the comparison shown is the editor in front: its files are being read there. */
  combinedActive?: boolean;
  /** Under review: Gerrit's attention set holds you on it (core's `your_turn`); undefined when not known. */
  yourTurn?: boolean;
  /** Under review: the chain goes on above it; undefined when not known. */
  above?: boolean;
  /** Gerrit's current patchset of the change shown, once the patchsets are known. */
  newest?: number | null;
  /** The change shown is under review in the review worktree's own window: its path. */
  reviewElsewhere?: string;
}

const KEY = "Alt+Enter";

function action(kind: NextKind, label: string, why: string): NextAction {
  return { kind, label, title: `${why} (${KEY})` };
}

/** Changes a push would send or publish: new code, a commit not on Gerrit yet, drafts that go along. */
function pushable(stack: Stack): boolean {
  return !stack.push_blocked && stack.commits.some((c) => !c.held_back && (c.patchset_status === "newer" || c.patchset_status === "absent" || (c.comments_drafted ?? 0) > 0));
}

/**
 * Reviewing: the comments yours to answer you have not gone to (not those waiting on the author's
 * reply to you, nor those your draft answers), the files not seen (all at once while the combined
 * diff is in front, where they are read), then the vote (while it is your
 * turn, or drafts of yours wait), then the next change of the chain, at its top leaving the review.
 */
function reviewNext(state: NextState): NextAction {
  const closed = state.shown?.change_status;
  if (closed === "ABANDONED" || closed === "MERGED") {
    // Nothing can be voted on or published on a closed change: on to the next one, or out.
    const word = closed === "ABANDONED" ? "abandoned" : "merged";
    return state.above === false || state.above === undefined
      ? action("leave", "Leave review", `the change is ${word}: nothing can be voted on or published on it`)
      : action("nextChange", "Next change", `the change is ${word}: nothing can be voted on or published on it`);
  }
  if (state.shown?.patchset_status === "outdated") {
    // The author pushed since the checkout: what is read and voted on here is not what Gerrit has.
    return action("update", state.newest ? `Update to patchset ${state.newest}` : "Update to the newest patchset", "Gerrit has a newer patchset than the one checked out: check it out for review in its place");
  }
  const left = state.chains.filter(toAnswer);
  const unread = left.filter((chain) => !state.visited.has(chain.root_id)).length;
  if (unread) {
    // The Comments section's count, and how many of them are left to go to: one count, one word.
    return action("comment", "Next comment", `${unread} of ${left.length} to answer not gone to yet`);
  }
  const unseen = state.files.filter((file) => !state.seenFiles.has(file)).length;
  if (unseen && state.combinedActive) {
    // Read in the combined diff, which cannot tell what was scrolled through: one press says so.
    return action("seen", `Mark ${unseen === 1 ? "1 file" : `${unseen} files`} seen`, `you read them in the combined diff: ticks the ${unseen === 1 ? "file" : `${unseen} files`} not seen yet, on Gerrit too`);
  }
  if (unseen) {
    return action("file", "Next file", `${unseen} of ${state.files.length} files not seen yet`);
  }
  const drafts = draftsIn(state.chains);
  if (drafts || state.yourTurn !== false) {
    return action("vote", "Vote and publish", drafts ? `${drafts} draft${drafts === 1 ? "" : "s"} of yours wait to be published` : "Gerrit waits for your review of it");
  }
  return state.above === false
    ? action("leave", "Leave review", "the top of the chain: nothing more to review in it")
    : action("nextChange", "Next change", "your review of this change is published");
}

/**
 * Reworking your own commit: a conflict first, the first file still conflicted named (resolved and
 * staged, the next one); once none is left, Continue; then every comment left to answer (one you
 * went to and typed a reply into without saving it is still left), then the attention walk (Work
 * on Next), then the push once nothing needs edits.
 */
function reworkNext(state: NextState, stack: Stack): NextAction | undefined {
  const [first, ...more] = stack.rebase?.unmerged ?? [];
  if (first !== undefined) {
    const which = more.length ? `${more.length + 1} files are still conflicted` : "it is still conflicted";
    return action("conflict", `Resolve ${first}`, `${which}: open ${first} with its conflict markers, resolve them, then stage it (git add) so the rebase can go on`);
  }
  if (stack.rebase?.stop === "resolved" && stack.rebase.actions.includes("continue")) {
    return action("continue", "Continue", "the conflicts are resolved: commit this stop and go on with the rebase");
  }
  const left = state.chains.filter(toAnswer);
  if (left.length) {
    const unread = left.filter((chain) => !state.visited.has(chain.root_id)).length;
    const why = unread ? `${unread} not gone to yet` : "gone to, not answered yet";
    return action("comment", "Next comment", `${left.length} comment${left.length === 1 ? "" : "s"} left to answer here, ${why}`);
  }
  const plan = workOnNext(stack);
  if (plan.kind === "start" || (plan.kind === "continue" && plan.next)) {
    return action("workOnNext", "Work on next", plan.kind === "continue" && plan.next ? `go on to "${plan.next.summary}", the next commit that needs edits` : WALK_WORDS.title);
  }
  if (plan.kind === "continue") {
    return action("workOnNext", "Finish the rebase", "no commit after this one needs edits");
  }
  return pushable(stack) ? action("push", "Push", "nothing needs edits: ger push sends the stack and publishes your drafts") : undefined;
}

/** What Next does on the change shown; undefined when there is nothing it would do. */
export function nextAction(state: NextState): NextAction | undefined {
  const { shown, stack } = state;
  if (!shown || !stack) {
    return undefined;
  }
  const away = shown.gerrit_unavailable;
  if (away && (state.reviewing || !state.local)) {
    // What Gerrit says of the change is unknown: no vote, no comment walk on an empty list. The fix first.
    const credentials = away.reason === "credentials";
    return action("gerrit", credentials ? "Check credentials" : "Ask Gerrit again", `${credentials ? "Gerrit refuses your credentials" : "Gerrit cannot be asked"}: what it says of this change is unknown`);
  }
  if (state.reviewing) {
    return reviewNext(state);
  }
  if (state.local && shown.own !== false && !stack.staged_review) {
    return reworkNext(state, stack);
  }
  if (state.reviewElsewhere) {
    // Checked out for review in its own window already: that is where the review goes on.
    return action("reviewWindow", "Go to Review Window", `the review of ${shown.number} is open in ${state.reviewElsewhere}, in its own window`);
  }
  // Somebody's change you are only looking at: reviewing it is next. Said before the press when your edits are in the way here.
  const blocked = stack.review_blocked?.files;
  const why = blocked?.length ? `check it out for review: ${blocked.join(", ")} ${blocked.length === 1 ? "has" : "have"} uncommitted changes here, so it offers the review worktree instead` : "check it out for review, staged, with all its files";
  return !state.local && shown.own === false && shown.pushed ? action("review", "Review change", why) : undefined;
}
