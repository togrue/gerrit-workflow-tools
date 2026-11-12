// What the "Local stack" view shows, as plain data: one row per commit, newest first, with how it
// relates to Gerrit, where a rebase underway stands, and its votes as badges. The webview draws
// it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import { MoveConflict, Overview, Stack, StackCommit, UpstreamChoice } from "./api";
import { blockedMove, reasonWords } from "./format";
import { Waiting } from "./state";

/** How a commit relates to what Gerrit has: the colour of its cloud. */
export type Cloud = "same" | "changed" | "outdated" | "diverged" | "not-pushed" | "merged" | "unknown";

const CLOUD_WORDS: Record<Cloud, string> = {
  same: "the same as on Gerrit",
  changed: "changed here since the last push",
  outdated: "outdated: Gerrit has a newer patchset",
  diverged: "diverged: changed here, and Gerrit has a patchset from elsewhere",
  "not-pushed": "not on Gerrit",
  merged: "merged on Gerrit",
  unknown: "Gerrit could not be asked",
};

export function cloud(commit: StackCommit): Cloud {
  switch (commit.patchset_status) {
    case "active":
      return "same";
    case "newer":
      return commit.diverged ? "diverged" : "changed";
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

/** Comments, review vote, CI vote, in that order; a slot with nothing to say is null. */
export function badges(commit: StackCommit): (Badge | null)[] {
  const last = commit.last_pushed_votes;
  const stale = Boolean(last && (last.verified !== null || last.code_review !== null));
  const note = stale ? " on the last pushed patchset; Gerrit may not keep it for this code" : "";
  const review = stale ? (last?.code_review ?? null) : commit.code_review;
  const verified = stale ? (last?.verified ?? null) : commit.verified;
  const failed = commit.ci_failures.length > 0 || (verified !== null && verified < 0);
  const count = commit.comments_unresolved;
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
          title: `Code-Review ${signed(review)}${note}`,
          stale,
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
  cloud: Cloud;
  cloudTitle: string;
  /** The rebase underway stands here: stopped with the commit applied (`rebase`), or on a conflict. */
  marker: "rebase" | "conflict" | null;
  /** Not (wholly) there yet: still to be replayed by the rebase, or stopped part-way. */
  dim: boolean;
  merge: boolean;
  /** Words after the summary: `HEAD`, the short id, `held back`. */
  notes: string[];
  badges: (Badge | null)[];
  tooltip: string;
  /** There is a commit below to squash into. */
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
}

const MARKER_WORDS = {
  rebase: "The rebase stopped here, with this commit applied.",
  conflict: "The rebase stopped here on a conflict: this commit is not applied yet.",
};

function row(commit: StackCommit, stack: Stack, below: StackCommit | undefined, above: StackCommit | undefined, conflicts: Conflicts): StackRow {
  const state = cloud(commit);
  const stopped = commit.rebase === "current";
  const conflict = stopped && stack.rebase?.applied === false;
  const marker = stopped ? (conflict ? "conflict" : "rebase") : null;
  const up = above && conflicts(commit.sha, above.sha);
  const down = below && conflicts(below.sha, commit.sha);
  const notes = [commit.is_head ? "HEAD" : "", commit.short_sha, commit.merge ? "merge" : "", commit.held_back ? "held back" : "", commit.abandoned ? "abandoned" : ""];
  const tooltip = [
    commit.summary,
    `${commit.number ? `Change ${commit.number}: ` : ""}${CLOUD_WORDS[state]}`,
    reasonWords(commit.attention_reasons) || "no open issues",
    marker ? MARKER_WORDS[marker] : commit.rebase === "todo" ? "Still to be replayed by the rebase." : "",
    commit.held_back ? "Held back by the ready boundary: ger push will not send it." : "",
    commit.reviewers.length ? `Reviewers: ${commit.reviewers.join(", ")}` : "",
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
    cloud: state,
    cloudTitle: CLOUD_WORDS[state],
    marker,
    dim: commit.rebase === "todo" || conflict,
    merge: Boolean(commit.merge),
    notes: notes.filter(Boolean),
    badges: badges(commit),
    tooltip: tooltip.filter(Boolean).join("\n"),
    squash: below !== undefined,
  };
}

type Conflicts = (lower: string, upper: string) => MoveConflict | undefined;

/** Why the stack cannot be rewritten as it is; null when it can. */
export function frozen(stack: Stack): string | null {
  if (stack.rebase_in_progress) {
    return "A rebase is in progress: finish it (git rebase --continue) or abort it (git rebase --abort) before you move commits.";
  }
  if (stack.commits.some((commit) => commit.merge)) {
    return "The stack contains a merge commit, which cannot be replayed in another place: rewrite it by hand with ger rebase.";
  }
  return null;
}

/** The view for `local` (the overview's local stack, or why there is none). */
export function stackVm(local: Overview["local"] | undefined, choice: UpstreamChoice | undefined, conflicts: Conflicts): StackVm {
  if (!local) {
    return { info: [], rows: [], frozen: null };
  }
  if ("error" in local) {
    if (!local.needs_upstream) {
      return { info: [{ text: local.error.message.split("\n")[0], tooltip: local.error.message, icon: "warning", tone: "warn" }], rows: [], frozen: null };
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
    return { info, rows: [], frozen: null };
  }
  const info: StackInfo[] = [];
  if (local.rebase_in_progress) {
    const conflict = local.rebase?.applied === false;
    info.push({
      text: conflict ? "A rebase stopped on a conflict" : "A rebase is in progress",
      detail: "this is the branch as it was before",
      icon: "warning",
      tone: "warn",
      tooltip: "The marker shows where the rebase stands; dimmed commits are not replayed yet. Finish with git rebase --continue, or go back with git rebase --abort.",
    });
  }
  if (!local.commits.length) {
    info.push({ text: "No local commits", detail: `${local.branch ?? "HEAD"} is at ${local.stack.target_branch}` });
  }
  const commits = local.commits;
  const rows = commits.map((commit, index) => row(commit, local, commits[index - 1], commits[index + 1], conflicts)).reverse();
  return { info, rows, frozen: frozen(local) };
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
      return `${marker}${r.summary} — ${[...r.notes, r.cloudTitle.split(":")[0]].join(" · ")}${right ? `  [${right}]` : ""}${r.dim ? "  (dim)" : ""}`;
    }),
  ];
}
