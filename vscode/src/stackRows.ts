// What the "Local stack" view shows, as plain data: one row per commit, newest first, with how it
// relates to Gerrit, where a rebase underway stands, and its votes as badges. The webview draws
// it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import { CodeChange, CommitStatus, MoveConflict, Overview, Rebase, RebaseAction, Reviewer, Stack, StackCommit, UpstreamChoice, UpstreamStop } from "./api";
import { blockedMove, reasonWords, sentence, wouldConflict } from "./format";
import { Waiting } from "./state";

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

export type Tone = "good" | "bad" | "warn" | "plain";

/** One icon on the right of a row. `stale`: it is about the last pushed patchset, not this code. */
export interface Badge {
  kind: "comments" | "review" | "ci";
  icon: string;
  tone: Tone;
  text: string;
  title: string;
  stale: boolean;
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

/**
 * Comments, review vote, CI vote, in that order; a slot with nothing to say is null. The Change view shows them too.
 * The review slot without a vote says when nobody is assigned to review (core's `no-reviewers`).
 */
/** `2 unresolved comments`; `1 of 2 comments to answer`; `2 comments wait on a reply`. */
function commentsTitle(count: number, awaiting: number): string {
  const waits = `${awaiting === 1 ? "waits" : "wait"} on a reply`;
  if (!awaiting) {
    return `${count} unresolved comment${count === 1 ? "" : "s"}`;
  }
  return awaiting === count ? `${count} comment${count === 1 ? "" : "s"} ${waits}` : `${count - awaiting} of ${count} comments to answer`;
}

export function badges(commit: CommitStatus): (Badge | null)[] {
  const last = commit.last_pushed_votes;
  const stale = Boolean(last && (last.verified !== null || last.code_review !== null));
  const note = stale ? " on the last pushed patchset; Gerrit may not keep it for this code" : "";
  const review = stale ? (last?.code_review ?? null) : commit.code_review;
  const verified = stale ? (last?.verified ?? null) : commit.verified;
  const failed = commit.ci_failures.length > 0 || (verified !== null && verified < 0);
  // Every build that failed only marked itself unstable (core's verdict per CI link): a warning, still CI to look at.
  const verdicts = commit.ci_links.map((link) => link.verdict).filter(Boolean);
  const unstable = verdicts.length > 0 && verdicts.every((verdict) => verdict === "unstable");
  const count = commit.comments_unresolved;
  const awaiting = Math.min(commit.comments_awaiting_reply ?? 0, count);
  const who = reviewerWords(commit.reviewers);
  return [
    count > 0
      ? {
          kind: "comments",
          // Every one waits on the other side (core's `awaiting-reply`): a plain bubble, nothing for you to do.
          icon: awaiting < count ? "comment-unresolved" : "comment",
          tone: awaiting < count ? "warn" : "plain",
          text: String(count),
          title: commentsTitle(count, awaiting),
          stale: false,
        }
      : null,
    review
      ? {
          kind: "review",
          icon: review > 0 ? "thumbsup" : "thumbsdown",
          tone: review > 0 ? "good" : "bad",
          text: signed(review),
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
          text: "",
          title: `${unstable ? "CI unstable" : "CI failed"}${commit.ci_failures.length ? `: ${commit.ci_failures.join(", ")}` : ""}${note}`,
          stale,
        }
      : verified
        ? { kind: "ci", icon: "pass", tone: "good", text: "", title: `Verified ${signed(verified)}${note}`, stale }
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
  resolve: "Open it in the merge editor to resolve it, then stage it (git add).",
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
 * Continue is the one to press once resolved. Local stack's banner and the Change view of the commit
 * it stopped at both draw them with this, so they cannot disagree.
 */
export function rebaseButtons(rebase: Pick<Rebase, "stop" | "actions"> | null | undefined): StackAction[] {
  return (rebase?.actions ?? []).map((action) => ({
    label: REBASE_ACTIONS[action].label,
    title: REBASE_ACTIONS[action].title,
    command: { id: REBASE_ACTIONS[action].command, args: [] },
    ...(action === "continue" && rebase?.stop === "resolved" ? { primary: true } : {}),
  }));
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
  if (!up?.behind) {
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

function row(commit: StackCommit, stack: Stack, index: number, pinnedBelow: number, conflicts: Conflicts): StackRow {
  const [below, above] = [stack.commits[index - 1], stack.commits[index + 1]];
  const isPinned = index < pinnedBelow;
  const state = sync(commit);
  const stopped = stoppedAt(commit, stack);
  const marker = stopped ? (stopped === "conflict" ? "conflict" : "rebase") : null;
  const up = above && conflicts(commit.sha, above.sha);
  const down = below && conflicts(below.sha, commit.sha);
  const notes = [commit.is_head ? "HEAD" : "", commit.short_sha, commit.merge ? "merge" : "", state === "code-changed" ? "code changed" : "", commit.held_back ? "held back" : "", commit.abandoned ? "abandoned" : ""];
  const tooltip = [
    commit.summary,
    `${commit.number ? `Change ${commit.number}: ` : ""}${SYNC_WORDS[state]}`,
    reasonWords(commit.attention_reasons) || "no open issues",
    stopped ? MARKER_WORDS[stopped === "applied" ? "rebase" : stopped] : commit.rebase === "todo" ? "Still to be replayed by the rebase." : "",
    commit.held_back ? "Held back by the ready boundary: ger push will not send it." : "",
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
    badges: badges(commit),
    tooltip: tooltip.filter(Boolean).join("\n"),
    squash: below !== undefined && index - 1 >= pinnedBelow,
  };
}

type Conflicts = (lower: string, upper: string) => MoveConflict | undefined;

/** Why the stack cannot be rewritten as it is; null when it can. */
export function frozen(stack: Stack): string | null {
  if (stack.rebase_in_progress) {
    return "A rebase is in progress: finish it (Continue) or abort it (Abort) before you move commits.";
  }
  return null;
}

/** The view for `local` (the overview's local stack, or why there is none). */
export function stackVm(local: Overview["local"] | undefined, choice: UpstreamChoice | undefined, conflicts: Conflicts): StackVm {
  if (!local) {
    return { info: [], rows: [], frozen: null, movable: 0 };
  }
  if ("error" in local) {
    if (!local.needs_upstream) {
      return { info: [{ text: local.error.message.split("\n")[0], tooltip: local.error.message, icon: "warning", tone: "warn" }], rows: [], frozen: null, movable: 0 };
    }
    const info: StackInfo[] = [
      {
        text: `${choice?.branch ?? "This branch"} has no upstream, so ger cannot tell where your stack starts`,
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
  const info: StackInfo[] = [];
  if (local.rebase_in_progress) {
    info.push(...rebaseBanner(local.rebase));
  }
  const upstream = upstreamRow(local);
  if (upstream) {
    info.push(upstream);
  }
  if (!local.commits.length) {
    const behind = local.upstream?.behind ? `is behind ${local.upstream.name}` : `is at ${local.stack.target_branch}`;
    info.push({ text: "No local commits", detail: `${local.branch ?? "HEAD"} ${behind}` });
  }
  const commits = local.commits;
  const fixed = pinned(local);
  const rows = commits.map((commit, index) => row(commit, local, index, fixed, conflicts)).reverse();
  return { info, rows, frozen: frozen(local), movable: commits.length - fixed };
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
  const said = (badge: Badge | null) => (badge ? `${badge.stale ? "last pushed " : ""}${badge.kind}${badge.text ? ` ${badge.text}` : ` ${badge.tone}`}` : "");
  return [
    ...vm.info.map((info) => {
      const buttons = actionsText(info.actions);
      return `${info.conflict ? "  ⚠ " : ""}${info.text}${info.detail ? ` — ${info.detail}` : ""}${buttons ? `  ${buttons}` : ""}`;
    }),
    ...vm.rows.map((r) => {
      // The conflict is drawn as a red warning; the commit it stopped at otherwise as a marker.
      const marker = r.marker ? (r.marker === "conflict" ? "⚠ " : "▶ ") : "";
      const right = r.badges.map(said).filter(Boolean).join(", ");
      return `${marker}${r.summary} — ${[...r.notes, r.syncTitle.split(":")[0]].join(" · ")}${right ? `  [${right}]` : ""}${r.dim ? "  (dim)" : ""}`;
    }),
  ];
}
