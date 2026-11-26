// What the "Local stack" view shows, as plain data: one row per commit, newest first, with how it
// relates to Gerrit, where a rebase underway stands, and its votes as badges. The webview draws
// it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import { BOUNDARY_WORDS, boundaryUnder, stackSummary } from "./stackSummary";
import { AbortDiscards, CodeChange, CommitStatus, MoveConflict, Overview, Rebase, RebaseAction, Reviewer, Stack, StackCommit, UpstreamChoice, UpstreamStop } from "./api";
import { CommentShape, CommentTone, commentState } from "./commentState";
import { blockedMove, plural, reasonWords, sentence, wouldConflict } from "./format";
import { PROBLEM_ACTIONS, PROBLEM_WORDS, gerritProblem, stackProblem } from "./problemRows";
import { Waiting } from "./state";
import type { Outcome } from "./taskRows";


/** How a commit relates to what Gerrit has: its icon and its colour. */
export type Sync = "same" | "rebased" | "code-changed" | "ahead" | "outdated" | "diverged" | "not-pushed" | "merged" | "unknown";

export const SYNC_WORDS: Record<Sync, string> = {
  same: "the same as on Gerrit",
  rebased: "only rebased since the last push: the same code, by patch-id (whitespace and the message aside), so Gerrit usually keeps the votes when you push",
  "code-changed": "its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push",
  ahead: "changed here since the last push: whether its code changed cannot be told here, and Gerrit may not keep the votes when you push",
  outdated: "outdated: Gerrit has a newer patchset",
  diverged: "diverged: changed here, and Gerrit has a patchset from elsewhere",
  "not-pushed": "not on Gerrit",
  merged: "merged on Gerrit",
  unknown: "Gerrit could not be asked",
};

/**
 * The codicon for each: a commit is no cloud. A full circle is in step with Gerrit, an empty one
 * is not there yet; an arrow points to the side that is newer (up: here, to push; down: Gerrit).
 * New code since the push is a diff: the votes are about other code.
 */
export const SYNC_ICONS: Record<Sync, string> = {
  same: "circle-filled",
  rebased: "arrow-circle-up",
  "code-changed": "diff-modified",
  ahead: "arrow-up",
  outdated: "arrow-circle-down",
  diverged: "arrow-swap",
  "not-pushed": "circle-outline",
  merged: "pass-filled",
  unknown: "question",
};

/** A commit ahead of Gerrit, by core's verdict on its code. */
const NEWER_SYNC: Record<CodeChange, Sync> = { rebased: "rebased", changed: "code-changed", unknown: "ahead" };

export function sync(commit: CommitStatus & { diverged?: boolean }): Sync {
  switch (commit.patchset_status) {
    case "active":
      return "same";
    case "newer":
      return commit.diverged ? "diverged" : NEWER_SYNC[commit.code_change ?? "unknown"];
    case "outdated":
      return "outdated";
    case "absent":
      return "not-pushed";
    case "unknown":
      return "unknown";
    default:
      return "merged";
  }
}

/** `info`: blue, for drafts of yours not published yet. */
export type Tone = "good" | "bad" | "warn" | "info" | "plain";

/** One icon on the right of a row. `stale`: it is about the last pushed patchset, not this code. */
export interface Badge {
  kind: "comments" | "review" | "ci";
  icon: string;
  tone: Tone;
  text: string;
  title: string;
  stale: boolean;
  /** Comments only: the open share and the state's shape, which a webview draws as a ring instead of the icon (commentState.ts). */
  ring?: { share: number; shape: CommentShape };
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value}`;
}

/** `Reviewers: alice, bob (cc: carol)`, or "" when nobody is on the change (or the source does not say). */
export function reviewerWords(reviewers: readonly Reviewer[] | undefined): string {
  const named = (cc: boolean) => (reviewers ?? []).filter((r) => r.cc === cc).map((r) => r.name).join(", ");
  const [assigned, copied] = [named(false), named(true)];
  if (!assigned && !copied) {
    return "";
  }
  return `Reviewers: ${[assigned || "none", copied ? `(cc: ${copied})` : ""].filter(Boolean).join(" ")}`;
}

/** The badge colour of each comment state (commentState.ts). */
const COMMENT_TONES: Record<CommentTone, Tone> = { draft: "info", open: "warn", waiting: "plain", resolved: "plain" };

/**
 * Comments, review vote, CI vote, in that order; a slot with nothing to say is null. The Change view shows them too.
 * The review slot without a vote says when nobody is assigned to review (core's `no-reviewers`).
 * `drafts`: unpublished comments of yours on it beside core's counts (the Change view knows them for the change shown).
 */
export function badges(commit: CommitStatus, drafts = 0): (Badge | null)[] {
  const last = commit.last_pushed_votes;
  const stale = Boolean(last && (last.verified !== null || last.code_review !== null));
  const note = stale ? " on the last pushed patchset; Gerrit may not keep it for this code" : "";
  const review = stale ? (last?.code_review ?? null) : commit.code_review;
  const verified = stale ? (last?.verified ?? null) : commit.verified;
  const failed = commit.ci_failures.length > 0 || (verified !== null && verified < 0);
  // Every build that failed only marked itself unstable (core's verdict per CI link): a warning, still CI to look at.
  const verdicts = commit.ci_links.map((link) => link.verdict).filter(Boolean);
  const unstable = verdicts.length > 0 && verdicts.every((verdict) => verdict === "unstable");
  const comments = commentState(commit, drafts);
  const who = reviewerWords(commit.reviewers);
  return [
    comments
      ? {
          kind: "comments",
          // Blue while drafts of yours wait to be published, yellow while a comment waits for your answer, else plain.
          icon: comments.tone === "open" ? "comment-unresolved" : "comment",
          tone: COMMENT_TONES[comments.tone],
          text: comments.text,
          title: sentence(comments.words),
          stale: false,
          ring: { share: comments.share, shape: comments.shape },
        }
      : null,
    review
      ? {
          kind: "review",
          icon: review > 0 ? "thumbsup" : "thumbsdown",
          tone: review > 0 ? "good" : "bad",
          // Of the last pushed patchset: "+2?", not only greyed (Q4).
          text: `${signed(review)}${stale ? "?" : ""}`,
          title: [`Code-Review ${signed(review)}${note}`, who].filter(Boolean).join("\n"),
          stale,
        }
      : commit.attention_reasons.includes("no-reviewers")
        ? {
            kind: "review",
            icon: "person-add",
            tone: "warn",
            text: "",
            title: "No reviewers assigned: nobody is asked to review it",
            stale: false,
          }
        : null,
    failed
      ? {
          kind: "ci",
          icon: unstable ? "warning" : "error",
          tone: unstable ? "warn" : "bad",
          text: stale ? "?" : "",
          title: `${unstable ? "CI unstable" : "CI failed"}${commit.ci_failures.length ? `: ${commit.ci_failures.join(", ")}` : ""}${note}`,
          stale,
        }
      : verified
        ? { kind: "ci", icon: "pass", tone: "good", text: stale ? "?" : "", title: `Verified ${signed(verified)}${note}`, stale }
        : null,
  ];
}

export interface StackRow {
  /** Survives a rewrite, which gives the commit a new id: the Change-Id when there is one. */
  key: string;
  sha: string;
  shortSha: string;
  summary: string;
  changeId: string | null;
  url: string | null;
  sync: Sync;
  /** The codicon that shows `sync`. */
  syncIcon: string;
  syncTitle: string;
  /** The rebase underway stands here: stopped with the commit applied (`rebase`), or on a conflict (drawn red). */
  marker: "rebase" | "conflict" | null;
  /** Not there yet: still to be replayed by the rebase. The commit it stopped at on a conflict is not: it is what wants you. */
  dim: boolean;
  /** A merge commit: one row, whatever it brings in. */
  merge: boolean;
  /** The newest merge or below it: git rebase cannot replay a merge, so it stays where it is. */
  pinned: boolean;
  /** Words after the summary: `HEAD`, the short id, `code changed`, `held back`. */
  notes: string[];
  badges: (Badge | null)[];
  tooltip: string;
  /** There is a commit below to squash into (not a pinned one). */
  squash: boolean;
}

/** A button on an info row: it runs `command`. `primary`: the one to press now, drawn prominently. */
export interface StackAction {
  label: string;
  title: string;
  command: { id: string; args: unknown[] };
  primary?: boolean;
}

export interface StackInfo {
  text: string;
  detail?: string;
  icon?: string;
  tone?: Tone;
  tooltip?: string;
  /** Clicking the row runs this. */
  command?: { id: string; args: unknown[] };
  /** A path still unmerged (relative to the repository): clicking the row opens it to resolve. */
  conflict?: string;
  /** The words of git or Gerrit that `text` puts in ger's words, as they came: drawn under it. */
  raw?: string;
  actions?: StackAction[];
}

export interface StackVm {
  waiting?: Waiting;
  info: StackInfo[];
  rows: StackRow[];
  /** Why commits cannot be moved right now; null when they can. */
  frozen: string | null;
  /** How many rows, from the top, can move: a drop lands no lower than under the last of them. */
  movable: number;
  /** A staged review: the rows are somebody else's commits, and offer review actions only. */
  review?: boolean;
  /** How much of the stack is where, in `ger log`'s summary words (stackSummary.ts): above the rows. */
  summary?: string;
  /** The ready boundary: drawn under the row keyed `under` (the oldest commit held back), with its words. */
  boundary?: { under: string; text: string; tooltip: string };
}

/** Where a rebase stopped, in the row's tooltip: why, by core's verdict. */
export const MARKER_WORDS = {
  rebase: "The rebase stopped here, with this commit applied.",
  conflict: "The rebase stopped here on a conflict: this commit is not applied yet.",
  resolved: "The rebase stopped here on a conflict, now resolved: Continue commits it.",
  stopped: "The rebase stopped here: HEAD has moved on from where it stopped, and there is no telling how this commit stands.",
};

/** The banner above a stack a rebase is underway on. */
export const REBASE_WORDS = {
  progress: "A rebase is in progress",
  conflict: "A rebase stopped on a conflict",
  resolved: "A rebase stopped with its conflicts resolved",
  split: "A rebase stopped while you split a commit",
  ready: "ready to continue",
  onTop: "Continue replays the rest on top of HEAD",
  asBefore: "this is the branch as it was before",
  tooltip: "The marker shows where the rebase stands; dimmed commits are not replayed yet and are shown as they were. Continue goes on, Abort goes back to the branch as it was before.",
  conflicted: "conflicted",
  resolve: "Open it, with its conflict markers, to resolve it (Accept Current / Incoming, or Resolve in Merge Editor), then stage it (git add).",
  /** Why the palette's Continue, Abort or Skip does not run: there is no rebase. Where there is one, core says why (`rebase.refused`). */
  noRebase: "There is no rebase in progress.",
  /** Why Rebase onto the upstream does not run: nothing new on it, or a rebase to finish first. */
  upToDate: "The upstream has no commits the branch does not have: there is nothing to rebase onto.",
  finishFirst: "A rebase is in progress: finish it or abort it before you rebase onto the upstream.",
  /** What Skip throws away, said before it does: the commit and the resolution so far; Abort brings back the branch, not the resolution. */
  skipLoses: "The rebase goes on without it: the commit and the conflict resolution done so far, staged or not, are thrown away, and once the rebase is finished the branch no longer has this commit. Abort instead brings back the branch as it was before the rebase, but not the resolution.",
  skipGerrit: "Its change stays open on Gerrit until you abandon it there.",
};


/** The banner's buttons: the commands they run, and what they do. */
export const REBASE_ACTIONS: Record<RebaseAction, { label: string; title: string; command: string }> = {
  continue: { label: "Continue", title: "Go on with the rebase (ger rebase --continue, in the ger terminal)", command: "ger.rebase.continue" },
  abort: { label: "Abort", title: "Stop the rebase: the branch goes back to where it was before it (ger rebase --abort)", command: "ger.rebase.abort" },
  skip: { label: "Skip", title: "Leave out the commit it stopped at, and go on without it (ger rebase --skip)", command: "ger.rebase.skip" },
};

/**
 * The buttons to go on with a stopped rebase: the ways on core allows from there (`rebase.actions`);
 * Continue, wherever core allows it, is the one to press (the screen's one primary button: at an
 * edit stop once the commit is as it should be, at a conflict once resolved). Local stack's banner
 * and the Change view of the commit it stopped at both draw them with this, so they cannot disagree.
 */
export function rebaseButtons(rebase: Pick<Rebase, "stop" | "actions"> | null | undefined): StackAction[] {
  return (rebase?.actions ?? []).map((action) => ({
    label: REBASE_ACTIONS[action].label,
    title: REBASE_ACTIONS[action].title,
    command: { id: REBASE_ACTIONS[action].command, args: [] },
    ...(action === "continue" ? { primary: true } : {}),
  }));
}

/** What Work on Next says: the attention walk (`ger edit -n`) from the extension. */
export const WALK_WORDS = {
  title: "Work on Next",
  continues: (next: string) => `ger edit -n runs in the ger terminal: it continues the rebase and stops at "${next}", the next commit that needs edits`,
  last: "ger edit -n runs in the ger terminal: it continues the rebase; no commit after this one needs edits",
  starts: "ger edit runs in the ger terminal: it stops at the commit the attention walk is at, or the first that needs edits",
  starting: "Starting the rework in the ger terminal…",
  notContinued: "Work on Next did not continue the rebase",
  nothing: "No commit of your stack needs edits: no comments to answer, failed builds or negative votes.",
  nothingToPush: "Nothing to push either: Gerrit has every commit as it is.",
  finished: "The rebase is finished, and no commit after it needs edits.",
  finishedMore: (count: number) => `The rebase is finished. ${count === 1 ? "1 commit of your stack still needs" : `${count} commits of your stack still need`} edits.`,
  review: "Work on Next walks your own stack: here a staged review holds somebody else's change.",
};

/**
 * What Work on Next does from `stack`, by core's verdicts (`needs_edit`, the rebase's stop): go on
 * with the rebase underway (`ger edit -n`), naming the next commit tip-ward of where it stopped that
 * needs edits, if any; start the walk (`ger edit`) when one needs edits; else nothing is left.
 */
export function workOnNext(stack: Stack): { kind: "continue"; next: StackCommit | undefined } | { kind: "start" } | { kind: "nothing" } | { kind: "review" } {
  if (stack.staged_review) {
    return { kind: "review" };
  }
  if (stack.rebase_in_progress) {
    // Oldest first, as GET /stack sends them: what is above the stop (or still to come, between commits) comes after it.
    const at = stack.commits.findIndex((commit) => commit.rebase === "current");
    const above = at >= 0 ? stack.commits.slice(at + 1) : stack.commits.filter((commit) => commit.rebase === "todo");
    return { kind: "continue", next: above.find((commit) => commit.needs_edit) };
  }
  return stack.summary.needs_edit ? { kind: "start" } : { kind: "nothing" };
}

/**
 * Which of Local stack's title commands would do something now (P6: one that would refuse is not
 * offered): Work on Next while a commit needs edits and the rebase underway can go on, Push while a
 * push would send something (`pushable`, nothing blocking it). Neither without a stack to show.
 */
export function titleOffers(local: Overview["local"] | undefined, pushable: (stack: Stack) => boolean): { workOnNext: boolean; push: boolean } {
  if (!local || "error" in local) {
    return { workOnNext: false, push: false };
  }
  const plan = workOnNext(local);
  const held = Boolean(local.rebase?.unmerged.length || local.rebase?.uncommitted?.length);
  return { workOnNext: plan.kind === "start" || (plan.kind === "continue" && !held), push: !local.push_blocked && pushable(local) };
}

/** Why `ger rebase --<action>` is not to run now, in core's words (`rebase.refused`); undefined when it may, or core has said nothing yet (the CLI decides). */
export function rebaseNotNow(stack: Stack | undefined, action: RebaseAction): string | undefined {
  if (!stack) {
    return undefined;
  }
  if (!stack.rebase) {
    return REBASE_WORDS.noRebase;
  }
  if (stack.rebase.actions.includes(action)) {
    return undefined;
  }
  const refused = stack.rebase.refused[action];
  return refused ? `${sentence(refused)}.` : undefined;
}

/** What {@link rebaseNotNowFresh} looks at: the model's last stack, and a way to load it again. */
export interface StackSource {
  readonly stack: Stack | undefined;
  refresh(): Promise<void>;
}

/**
 * {@link rebaseNotNow}, said only after one more look: the stack shown may predate a `git add` of
 * the resolved files in the terminal (the index watch waits for a quiet moment, then a refresh), and
 * a stale verdict must not turn Continue away.
 */
export async function rebaseNotNowFresh(source: StackSource, action: RebaseAction): Promise<string | undefined> {
  if (!rebaseNotNow(source.stack, action)) {
    return undefined;
  }
  await source.refresh();
  return rebaseNotNow(source.stack, action);
}

/** Skip's question: what is left out, and what that throws away. */
export function skipQuestion(stack: Stack | undefined): { message: string; detail: string } {
  const stopped = stack?.commits.find((commit) => commit.rebase === "current");
  const what = stopped ? `"${stopped.summary}"` : "the commit the rebase stopped at";
  return { message: `Skip ${what}?`, detail: stopped?.pushed ? `${REBASE_WORDS.skipLoses} ${REBASE_WORDS.skipGerrit}` : REBASE_WORDS.skipLoses };
}

/**
 * Abort's question (core's `discards`, asked only when `asks`): the commits made in this rebase that
 * go with it, named, and a resolution; what a stash keeps is said too, so nothing is a surprise after.
 */
export function abortQuestion(discards: AbortDiscards): { message: string; detail: string; show?: { label: string; shas: string[] } } {
  const gone = discards.rewritten.map((commit) => `${commit.short_sha} "${commit.summary}"`);
  const lines = [
    "The branch goes back to where it was before the rebase.",
    gone.length ? `Gone from the branch with it, made in this rebase: ${gone.join(", ")}. ${ABORT_SHOW} opens ${gone.length === 1 ? "its changes" : "their changes"} first; nothing is aborted then.` : "",
    discards.resolution ? "The conflict resolution of the commit it stopped at goes too." : "",
    discards.uncommitted.length ? `${discards.uncommitted.join(", ")}, not committed, ${discards.uncommitted.length === 1 ? "is" : "are"} kept in a stash: git stash apply brings ${discards.uncommitted.length === 1 ? "it" : "them"} back.` : "",
  ];
  return {
    message: gone.length ? `Abort the rebase, and with it ${plural(gone.length, "rewritten commit")}?` : "Abort the rebase, and the resolution?",
    detail: lines.filter(Boolean).join("\n\n"),
    ...(gone.length ? { show: { label: ABORT_SHOW, shas: discards.rewritten.map((commit) => commit.sha) } } : {}),
  };
}

/** The button beside Abort in its question: the commits that would go, opened before anything goes. */
export const ABORT_SHOW = "Show What Would Go";

/** What Push offers instead while a rebase blocks it: the ways on core allows, but Skip (finishing is the point). */
export function pushWaysOn(stack: Stack | undefined): RebaseAction[] {
  return (stack?.rebase?.actions ?? ["continue", "abort"]).filter((action) => action !== "skip");
}

/** How the rebase underway stands at the commit it stopped at (`stoppedAt`). */
export type StoppedAt = "conflict" | "resolved" | "applied" | "stopped";

/**
 * Where the rebase underway stopped at `commit`, by core's verdict: the commit's `rebase` step says
 * it stopped there, the stack's `stop` whether on a conflict still open or resolved, else its
 * `applied` whether the commit is in whole; `stopped` where core cannot tell (a `null` stop, the
 * commit not applied: a resolution committed by hand as another commit), and null when it did not
 * stop at this commit. Local stack marks the row with it; the Change view of that commit says it.
 */
export function stoppedAt(commit: Pick<StackCommit, "rebase">, stack: Pick<Stack, "rebase">): StoppedAt | null {
  const rebase = stack.rebase;
  if (commit.rebase !== "current" || !rebase) {
    return null;
  }
  if (rebase.stop === "conflict" || rebase.stop === "resolved") {
    return rebase.stop;
  }
  return rebase.applied ? "applied" : "stopped";
}

/** A file still conflicted, under the banner and in the Change view's Conflicts: a click opens it to resolve. */
export function conflictRow(file: string): StackInfo {
  return { text: file, detail: REBASE_WORDS.conflicted, icon: "warning", tone: "bad", tooltip: REBASE_WORDS.resolve, conflict: file };
}

function rebaseBanner(rebase: Rebase | null | undefined): StackInfo[] {
  const stop = rebase?.stop ?? null;
  const said: Partial<Record<string, string>> = { conflict: REBASE_WORDS.conflict, resolved: REBASE_WORDS.resolved, split: REBASE_WORDS.split };
  const text = said[stop ?? ""] ?? REBASE_WORDS.progress;
  // git rerere put a resolution recorded earlier in: nobody has looked at it yet (core's `reused`).
  const reused = rebase?.reused?.length ? `git reused a recorded resolution for ${rebase.reused.join(", ")}: check it before you continue` : undefined;
  const detail = reused ?? (stop === "resolved" ? REBASE_WORDS.ready : stop === "split" ? REBASE_WORDS.onTop : rebase?.as_before ? REBASE_WORDS.asBefore : undefined);
  const banner: StackInfo = { text, detail, icon: "warning", tone: "warn", tooltip: REBASE_WORDS.tooltip, actions: rebaseButtons(rebase) };
  return [banner, ...(rebase?.unmerged ?? []).map(conflictRow)];
}

/** The upstream row's words: what it says, and what the rebase does where the preview says it stops. */
export const UPSTREAM_WORDS = {
  clean: "no conflicts",
  stops: "the rebase stops there for you to resolve it, or to abort it",
  stopsEmpty: "the rebase stops there for you to drop it (Skip), or to abort it",
};

/** Why the preview could not tell, in core's words (`upstream.reason`). */
export function upstreamUnknown(reason: string | null): string {
  return `Whether it would conflict cannot be told here${reason ? ` (${reason})` : ""}: the rebase finds out.`;
}

/** `"Refactor parser init" would conflict in a.txt`, or `… would become empty: its change is already upstream`. */
export function upstreamStop(stop: UpstreamStop): string {
  return stop.kind === "empty" ? `"${stop.summary}" would become empty: its change is already upstream` : wouldConflict(stop);
}

/**
 * The row that says the upstream moved on (core's `upstream`), with the rebase onto it and what
 * that would meet; none while it has not, and none during a rebase (core says nothing then: the
 * banner leads).
 */
export function upstreamRow(stack: Stack): StackInfo | undefined {
  const up = stack.upstream;
  // A staged review is somebody else's chain: rebasing it is not yours to do (core refuses it there).
  if (!up?.behind || stack.staged_review) {
    return undefined;
  }
  const what = up.conflict ? upstreamStop(up.conflict) : undefined;
  const stops = up.conflict?.kind === "empty" ? UPSTREAM_WORDS.stopsEmpty : UPSTREAM_WORDS.stops;
  const n = up.dropped.length;
  const dropped = n ? ` ${n} commit${n === 1 ? " is" : "s are"} already upstream and will be dropped.` : "";
  const preview = what ? `Rebasing onto it, ${what}: ${stops}.` : up.preview ? "Every commit applies on it as it is now." : upstreamUnknown(up.reason);
  const command = `ger ${up.argv.join(" ")}`;
  return {
    text: `${up.name} has ${up.behind} new commit${up.behind === 1 ? "" : "s"}`,
    detail: what ?? (up.preview && stack.commits.length ? UPSTREAM_WORDS.clean : undefined),
    icon: what ? "warning" : "arrow-down",
    tone: what ? "warn" : "plain",
    tooltip: `${up.name} has commits ${stack.branch ?? "the branch"} does not have yet. ${stack.commits.length ? preview + dropped : ""}`.trim(),
    actions: [
      {
        label: `Rebase onto ${up.name}`,
        title: `${command}, in the ger terminal: it shows the todo, then replays the stack onto ${up.name}`,
        command: { id: "ger.rebase.upstream", args: [] },
      },
    ],
  };
}

/** The view's header: the branch, or the one being rebased, and where its stack starts. */
export function stackWhere(stack: Stack): string {
  const review = stack.staged_review;
  if (review) {
    // Somebody else's chain, as `ger review` prints it: base first, the change under review in brackets.
    const numbers = stack.commits.map((commit) => (commit.number === review.number ? `[${commit.number}]` : String(commit.number ?? commit.short_sha)));
    return `reviewing ${numbers.length ? numbers.join(" ← ") : review.number}`;
  }
  const branch = stack.rebase ? `${stack.rebase.branch ?? "detached"} (rebasing)` : (stack.branch ?? "detached");
  return stack.stack.target_branch ? `${branch} → ${stack.stack.target_branch}` : branch;
}

export const MERGE_WORDS = "A merge commit: the commits it brings in are not part of the stack, and are not listed.";

/** How many commits, oldest first, stay where they are: the ones `ger serve` says are pinned. */
export function pinned(stack: Stack): number {
  return stack.commits.filter((commit) => commit.pinned).length;
}

/** Why commits at or below the newest merge cannot move, fold or go: for the message when one is asked to. */
export function pinnedWhy(stack: Stack): string {
  const merge = stack.commits[pinned(stack) - 1];
  return `"${merge?.summary ?? "The merge"}" is a merge commit, which git rebase cannot replay: it and the commits below it stay where they are. Rewrite them by hand with ger rebase.`;
}

function row(commit: StackCommit, stack: Stack, index: number, pinnedBelow: number, conflicts: Conflicts, drafts: (sha: string) => number = () => 0): StackRow {
  const [below, above] = [stack.commits[index - 1], stack.commits[index + 1]];
  const isPinned = index < pinnedBelow;
  const state = sync(commit);
  const stopped = stoppedAt(commit, stack);
  const marker = stopped ? (stopped === "conflict" ? "conflict" : "rebase") : null;
  const up = above && conflicts(commit.sha, above.sha);
  const down = below && conflicts(below.sha, commit.sha);
  // In a staged review HEAD sits at the change's parent only to stage it: the change is what is looked at.
  const review = stack.staged_review;
  const where = review ? (commit.sha === review.commit ? "under review" : "") : commit.is_head ? "HEAD" : "";
  // A fixup and the commit it folds into, by core's pairing (`fixup_of`): each row names the other.
  const target = commit.fixup_of ? stack.commits.find((c) => c.sha === commit.fixup_of) : undefined;
  const pending = stack.commits.filter((c) => c.fixup_of === commit.sha).length;
  const fixes = target ? FIXUP_WORDS.of(target.number ? String(target.number) : target.short_sha) : pending ? FIXUP_WORDS.pending(pending) : "";
  // `ger push` gives it a Change-Id and sends it (core's `adds_change_id`): said as the push will do it, not as held back.
  const addsId = commit.adds_change_id ? "push adds a Change-Id" : "";
  const notes = [where, commit.short_sha, fixes, commit.merge ? "merge" : "", state === "code-changed" ? "code changed" : "", commit.held_back ? "held back" : addsId, commit.abandoned ? "abandoned" : ""];
  const tooltip = [
    commit.summary,
    `${commit.number ? `Change ${commit.number}: ` : ""}${SYNC_WORDS[state]}`,
    reasonWords(commit.attention_reasons) || "no open issues",
    stopped ? MARKER_WORDS[stopped === "applied" ? "rebase" : stopped] : commit.rebase === "todo" ? "Still to be replayed by the rebase." : "",
    target ? FIXUP_WORDS.ofTooltip(target.summary) : pending ? FIXUP_WORDS.pendingTooltip : "",
    commit.held_back ? "Held back by the ready boundary: ger push will not send it." : "",
    commit.adds_change_id ? `It has no valid Change-Id: ger push gives it one before its preview and sends it as a new change, so this local commit changes (taken back if the push is not sent).` : "",
    commit.merge ? MERGE_WORDS : "",
    isPinned ? "It stays where it is: git rebase cannot replay a merge, so the newest merge and what is below it are not moved, folded or dropped." : "",
    reviewerWords(commit.reviewers),
    up ? blockedMove(commit.summary, above.summary, up) : "",
    down ? blockedMove(commit.summary, below.summary, down) : "",
  ];
  return {
    key: commit.change_id ?? commit.sha,
    sha: commit.sha,
    shortSha: commit.short_sha,
    summary: commit.summary,
    changeId: commit.change_id,
    url: commit.gerrit_url,
    sync: state,
    syncIcon: SYNC_ICONS[state],
    syncTitle: SYNC_WORDS[state],
    marker,
    dim: commit.rebase === "todo",
    merge: Boolean(commit.merge),
    pinned: isPinned,
    notes: notes.filter(Boolean),
    badges: badges(commit, drafts(commit.sha)),
    tooltip: tooltip.filter(Boolean).join("\n"),
    squash: !review && below !== undefined && index - 1 >= pinnedBelow,
  };
}

/** A fixup row names the commit it folds into; that commit, how many wait to be folded into it. */
export const FIXUP_WORDS = {
  of: (target: string) => `→ ${target}`,
  ofTooltip: (summary: string) => `A fixup of "${summary}": ger rebase --autosquash folds it in there. ger push stops at it until then.`,
  pending: (count: number) => `${count} ${count === 1 ? "fixup" : "fixups"} pending`,
  pendingTooltip: "Fixups above wait to be folded into this commit: ger rebase --autosquash does it.",
};

type Conflicts = (lower: string, upper: string) => MoveConflict | undefined;

/** Why the stack cannot be rewritten as it is; null when it can. */
/** Why the commits of a staged review are not to be moved, as core refuses rewrites there. */
export const REVIEW_FROZEN = "These are somebody else's commits, checked out for review: nothing moves here. ger review --unstage to work on them, or Leave Review Checkout.";

export function frozen(stack: Stack): string | null {
  if (stack.staged_review) {
    return REVIEW_FROZEN;
  }
  if (stack.rebase_in_progress) {
    return "A rebase is in progress: finish it (Continue) or abort it (Abort) before you move commits.";
  }
  return null;
}

/** The view for `local` (the overview's local stack, or why there is none). */
/** `headerShown`: the task header (taskRows.ts) says what the rebase banner would, with its buttons: the banner is left out. */
/** The last step's outcome as Local stack's first row, while no task header says it (the rebase over, a push). */
export function outcomeRow(outcome: Outcome): StackInfo {
  const upstream = outcome.more.some((line) => line.includes("--set-upstream-to"));
  return {
    text: outcome.text,
    icon: outcome.ok ? "check" : "warning",
    tone: outcome.ok ? "good" : "warn",
    tooltip: [outcome.text, ...outcome.more].join("\n"),
    ...(outcome.more.length ? { detail: outcome.more.filter((line) => !line.startsWith("next:")).join(" · ") || undefined } : {}),
    ...(upstream ? { actions: [{ label: "Choose Upstream…", title: "Make another remote branch the upstream: the push goes to its branch on Gerrit", command: { id: "ger.setUpstream", args: [] } }] } : {}),
  };
}

/** Said on a detached HEAD: what a commit made there becomes, and the way out. */
export const DETACHED_WORDS = {
  text: "Detached HEAD: commits made here belong to no branch",
  tooltip: "HEAD is not on a branch. A commit made now is left behind once you check out a branch: create one here to keep working on it (Git: Create Branch…).",
  create: "Create Branch Here…",
};

export function stackVm(
  local: Overview["local"] | undefined,
  choice: UpstreamChoice | undefined,
  conflicts: Conflicts,
  drafts: (sha: string) => number = () => 0,
  headerShown = false,
  outcome?: Outcome,
): StackVm {
  if (!local) {
    return { info: [], rows: [], frozen: null, movable: 0 };
  }
  const said = outcome && !headerShown ? [outcomeRow(outcome)] : [];
  if ("error" in local) {
    if (!local.needs_upstream) {
      const problem = stackProblem(local.error.message);
      return { info: [...said, { text: problem.text, raw: problem.raw, icon: "warning", tone: "warn", actions: problem.actions }], rows: [], frozen: null, movable: 0 };
    }
    // Configured and gone (deleted on the remote, or pruned) is not "none": say which.
    const gone = choice?.gone;
    const info: StackInfo[] = [
      ...said,
      {
        text: gone
          ? `The upstream ${gone} of ${choice?.branch ?? "this branch"} is gone, so ger cannot tell where your stack starts`
          : `${choice?.branch ?? "This branch"} has no upstream, so ger cannot tell where your stack starts`,
        tooltip: `${local.error.message}\n\nger checkout (Check Out to Work On) sets the upstream when it creates the branch.`,
        icon: "warning",
        tone: "warn",
      },
    ];
    if (choice?.suggested) {
      info.push({
        text: `Track ${choice.suggested}`,
        detail: choice.why ?? undefined,
        tooltip: `Make ${choice.suggested} the upstream of ${choice.branch}: the stack is the commits above it, and push goes to its branch on Gerrit.`,
        icon: "git-branch",
        command: { id: "ger.setUpstream", args: [choice.suggested] },
      });
    }
    info.push({
      text: choice?.suggested ? "Choose another upstream branch…" : "Choose an upstream branch…",
      icon: "list-selection",
      command: { id: "ger.setUpstream", args: [] },
    });
    return { info, rows: [], frozen: null, movable: 0 };
  }
  const info: StackInfo[] = [...said];
  if (!local.branch && !local.rebase_in_progress && !local.staged_review) {
    info.push({ text: DETACHED_WORDS.text, icon: "git-commit", tone: "warn", tooltip: DETACHED_WORDS.tooltip, actions: [{ label: DETACHED_WORDS.create, title: DETACHED_WORDS.tooltip, command: { id: "git.branch", args: [] } }] });
  }
  if (local.rebase_in_progress) {
    // The task header says what the banner does, and more: only the conflicted files stay under it.
    info.push(...(headerShown ? (local.rebase?.unmerged ?? []).map(conflictRow) : rebaseBanner(local.rebase)));
  }
  const gerrit = gerritRow(local);
  if (gerrit) {
    info.push(gerrit);
  }
  const upstream = upstreamRow(local);
  if (upstream) {
    info.push(upstream);
  }
  if (!local.commits.length) {
    // The title says the branch and its upstream already ("main → origin/main"): the row only says what that means.
    const behind = local.upstream?.behind ? `${local.branch ?? "HEAD"} is behind ${local.upstream.name}` : undefined;
    info.push({ text: "No local commits", detail: behind, tooltip: `${local.branch ?? "HEAD"} is at ${local.stack.target_branch}: nothing to push, nothing to rework.` });
  }
  const commits = local.commits;
  const fixed = pinned(local);
  const rows = commits.map((commit, index) => row(commit, local, index, fixed, conflicts, drafts)).reverse();
  const review = Boolean(local.staged_review);
  // Somebody else's commits under review: neither a summary of your stack nor a boundary of your push.
  const glance = review || !commits.length ? {} : { summary: stackSummary(commits), ...boundaryOf(boundaryUnder(commits, rows.map((r) => r.key))) };
  return { info, rows, frozen: frozen(local), movable: review ? 0 : commits.length - fixed, review, ...glance };
}

const boundaryOf = (under: string | undefined) => (under ? { boundary: { under, ...BOUNDARY_WORDS } } : {});

/** What Local stack says about Gerrit when it could not ask it, or has not yet: once, above the commits. */
export const STACK_GERRIT_WORDS = {
  asking: "Asking Gerrit: what it says of each commit shows once it answers",
  unknown: "Gerrit state unknown",
};

/** The line above the commits while their Gerrit state is unknown: why, and Retry (Review chains has the other ways on). */
function gerritRow(local: Stack): StackInfo | undefined {
  if (local.local_only) {
    return { text: STACK_GERRIT_WORDS.asking, icon: "sync" };
  }
  const away = local.gerrit_unavailable;
  if (!away) {
    return undefined;
  }
  const problem = gerritProblem(away);
  return { text: STACK_GERRIT_WORDS.unknown, raw: away.message, tooltip: `${problem.text}\n\n${PROBLEM_WORDS.localGoesOn}`, icon: "warning", tone: "warn", actions: [PROBLEM_ACTIONS.retry] };
}

/** Buttons as the transcript shows them: `[Continue] [Abort]`, the one to press now in bold. */
export function actionsText(actions: StackAction[] | undefined): string {
  return (actions ?? []).map((a) => (a.primary ? `[**${a.label}**]` : `[${a.label}]`)).join(" ");
}

/** The view as text, one line per row: what the end-to-end transcript shows of it. */
export function stackLines(vm: StackVm): string[] {
  if (vm.waiting) {
    return [vm.waiting.text];
  }
  const said = (badge: Badge | null) => (badge ? `${badge.stale ? "last pushed " : ""}${badge.kind}${badge.text && badge.text !== "?" ? ` ${badge.text}` : ` ${badge.tone}${badge.text}`}` : "");
  return [
    ...vm.info.map((info) => {
      const buttons = actionsText(info.actions);
      const raw = info.raw ? `  (${info.raw.split("\n").join(" / ")})` : "";
      return `${info.conflict ? "  ⚠ " : ""}${info.text}${info.detail ? ` — ${info.detail}` : ""}${raw}${buttons ? `  ${buttons}` : ""}`;
    }),
    ...(vm.summary ? [`summary: ${vm.summary}`] : []),
    ...vm.rows.flatMap((r) => {
      // The conflict is drawn as a red warning; the commit it stopped at otherwise as a marker.
      const marker = r.marker ? (r.marker === "conflict" ? "⚠ " : "▶ ") : "";
      const right = r.badges.map(said).filter(Boolean).join(", ");
      const line = `${marker}${r.summary} — ${[...r.notes, r.syncTitle.split(":")[0]].join(" · ")}${right ? `  [${right}]` : ""}${r.dim ? "  (dim)" : ""}`;
      return r.key === vm.boundary?.under ? [line, `---- ${vm.boundary.text} ----`] : [line];
    }),
  ];
}
