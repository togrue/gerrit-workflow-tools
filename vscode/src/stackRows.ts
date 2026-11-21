// What the "Local stack" view shows, as plain data: one row per commit, newest first, with how it
// relates to Gerrit, where a rebase underway stands, and its votes as badges. The webview draws
// it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import { CodeChange, CommitStatus, MoveConflict, Overview, Reviewer, Stack, StackCommit, UpstreamChoice } from "./api";
import { blockedMove, reasonWords } from "./format";
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
export function badges(commit: CommitStatus): (Badge | null)[] {
  const last = commit.last_pushed_votes;
  const stale = Boolean(last && (last.verified !== null || last.code_review !== null));
  const note = stale ? " on the last pushed patchset; Gerrit may not keep it for this code" : "";
  const review = stale ? (last?.code_review ?? null) : commit.code_review;
  const verified = stale ? (last?.verified ?? null) : commit.verified;
  const failed = commit.ci_failures.length > 0 || (verified !== null && verified < 0);
  const count = commit.comments_unresolved;
  const who = reviewerWords(commit.reviewers);
  return [
    count > 0
      ? {
          kind: "comments",
          icon: "comment-unresolved",
          tone: "warn",
          text: String(count),
          title: `${count} unresolved comment${count === 1 ? "" : "s"}`,
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
          icon: "error",
          tone: "bad",
          text: "",
          title: `CI failed${commit.ci_failures.length ? `: ${commit.ci_failures.join(", ")}` : ""}${note}`,
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
  /** The rebase underway stands here: stopped with the commit applied (`rebase`), or on a conflict. */
  marker: "rebase" | "conflict" | null;
  /** Not (wholly) there yet: still to be replayed by the rebase, or stopped part-way. */
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

export interface StackInfo {
  text: string;
  detail?: string;
  icon?: string;
  tone?: Tone;
  tooltip?: string;
  /** Clicking the row runs this. */
  command?: { id: string; args: unknown[] };
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
  resolved: "The rebase stopped here on a conflict, now resolved: git rebase --continue commits it.",
};

/** The banner above a stack a rebase is underway on. */
export const REBASE_WORDS = {
  progress: "A rebase is in progress",
  conflict: "A rebase stopped on a conflict",
  resolved: "A rebase stopped with its conflicts resolved",
  ready: "ready to continue",
  asBefore: "this is the branch as it was before",
  tooltip: "The marker shows where the rebase stands; dimmed commits are not replayed yet and are shown as they were. Finish with git rebase --continue, or go back with git rebase --abort.",
};

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
  const stopped = commit.rebase === "current";
  const conflict = stopped && stack.rebase?.stop === "conflict";
  const resolved = stopped && stack.rebase?.stop === "resolved";
  const marker = stopped ? (conflict ? "conflict" : "rebase") : null;
  const up = above && conflicts(commit.sha, above.sha);
  const down = below && conflicts(below.sha, commit.sha);
  const notes = [commit.is_head ? "HEAD" : "", commit.short_sha, commit.merge ? "merge" : "", state === "code-changed" ? "code changed" : "", commit.held_back ? "held back" : "", commit.abandoned ? "abandoned" : ""];
  const tooltip = [
    commit.summary,
    `${commit.number ? `Change ${commit.number}: ` : ""}${SYNC_WORDS[state]}`,
    reasonWords(commit.attention_reasons) || "no open issues",
    marker ? MARKER_WORDS[resolved ? "resolved" : marker] : commit.rebase === "todo" ? "Still to be replayed by the rebase." : "",
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
    dim: commit.rebase === "todo" || conflict,
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
    return "A rebase is in progress: finish it (git rebase --continue) or abort it (git rebase --abort) before you move commits.";
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
    const stop = local.rebase?.stop;
    info.push({
      text: stop === "conflict" ? REBASE_WORDS.conflict : stop === "resolved" ? REBASE_WORDS.resolved : REBASE_WORDS.progress,
      detail: stop === "resolved" ? REBASE_WORDS.ready : local.rebase?.as_before ? REBASE_WORDS.asBefore : undefined,
      icon: "warning",
      tone: "warn",
      tooltip: REBASE_WORDS.tooltip,
    });
  }
  if (!local.commits.length) {
    info.push({ text: "No local commits", detail: `${local.branch ?? "HEAD"} is at ${local.stack.target_branch}` });
  }
  const commits = local.commits;
  const fixed = pinned(local);
  const rows = commits.map((commit, index) => row(commit, local, index, fixed, conflicts)).reverse();
  return { info, rows, frozen: frozen(local), movable: commits.length - fixed };
}

/** The view as text, one line per row: what the end-to-end transcript shows of it. */
export function stackLines(vm: StackVm): string[] {
  if (vm.waiting) {
    return [vm.waiting.text];
  }
  const said = (badge: Badge | null) => (badge ? `${badge.stale ? "last pushed " : ""}${badge.kind}${badge.text ? ` ${badge.text}` : ` ${badge.tone}`}` : "");
  return [
    ...vm.info.map((info) => `${info.text}${info.detail ? ` — ${info.detail}` : ""}`),
    ...vm.rows.map((r) => {
      const marker = r.marker ? `▶${r.marker === "conflict" ? "!" : ""} ` : "";
      const right = r.badges.map(said).filter(Boolean).join(", ");
      return `${marker}${r.summary} — ${[...r.notes, r.syncTitle.split(":")[0]].join(" · ")}${right ? `  [${right}]` : ""}${r.dim ? "  (dim)" : ""}`;
    }),
  ];
}
