// How review state is put into words and ordered by urgency. Pure: no `vscode`, no I/O.

import { Chain, MoveConflict, Overview, StackCommit, StackMoves } from "./api";

export type Severity = "error" | "warning" | "pending" | "ok";

const ERRORS = ["ci-failed", "merged-drift", "abandoned", "missing-change-id"];
const WARNINGS = ["unresolved-comments", "review-issues", "outdated-patchset", "merged-unknown"];
const PENDING = ["not-pushed", "ahead-of-gerrit", "awaiting-review", "no-reviewers", "chain-blocked", "wip"];

const WORDS: Record<string, string> = {
  "ci-failed": "CI failed",
  "unresolved-comments": "unresolved comments",
  "review-issues": "negative vote",
  "not-pushed": "not pushed",
  "ahead-of-gerrit": "local changes not pushed",
  "outdated-patchset": "Gerrit has a newer patchset",
  "awaiting-review": "awaiting review",
  "no-reviewers": "no reviewers",
  "chain-blocked": "blocked by an earlier change",
  "missing-change-id": "no Change-Id",
  "merged-drift": "merged, but differs locally",
  "merged-unknown": "merged, equivalence unknown",
  abandoned: "abandoned",
  wip: "work in progress",
};

/** The most urgent thing the attention reasons say. */
export function severity(reasons: readonly string[]): Severity {
  if (reasons.some((r) => ERRORS.includes(r))) {
    return "error";
  }
  if (reasons.some((r) => WARNINGS.includes(r))) {
    return "warning";
  }
  return reasons.some((r) => PENDING.includes(r)) ? "pending" : "ok";
}

export function reasonWords(reasons: readonly string[]): string {
  return reasons.map((r) => WORDS[r] ?? r).join(", ");
}

/** `V+1`, `CR-2`; a missing vote is a dot so columns keep their place. */
export function vote(label: string, value: number | null): string {
  if (value === null || value === undefined) {
    return `${label}·`;
  }
  return `${label}${value > 0 ? "+" : ""}${value}`;
}

/** The votes that exist and the unresolved comments, e.g. `V+1 CR-1 💬2`; `no votes` when there is nothing. */
export function votes(item: { verified: number | null; code_review: number | null; comments_unresolved: number }): string {
  const parts: string[] = [];
  if (item.verified !== null && item.verified !== undefined) {
    parts.push(vote("V", item.verified));
  }
  if (item.code_review !== null && item.code_review !== undefined) {
    parts.push(vote("CR", item.code_review));
  }
  if (item.comments_unresolved > 0) {
    parts.push(`💬${item.comments_unresolved}`);
  }
  return parts.join(" ") || "no votes";
}

const FILE_STATES: Record<string, string> = { A: "added", M: "modified", D: "deleted", R: "renamed", C: "copied", T: "type changed" };

/** Git's status letter in a word. */
export function fileState(letter: string): string {
  return FILE_STATES[letter] ?? letter;
}

export function sentence(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** Compact age: `12m`, `4h`, `3d` — the same steps `ger inbox` prints. */
export function age(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function chainDescription(chain: Chain, section: string): string {
  const parts = [plural(chain.depth, "change"), votes(chain)];
  if (section === "to-review") {
    parts.unshift(chain.owner.name);
    parts.push(`waiting ${age(chain.unreviewed_age_seconds)}`);
  }
  parts.push(`active ${age(chain.wait_age_seconds)} ago`);
  return parts.join(" · ");
}

export function commitDescription(commit: StackCommit): string {
  const parts = [commit.short_sha];
  parts.push(commit.pushed ? votes(commit) : "not on Gerrit");
  if (commit.patchset_status === "newer") {
    parts.push("ahead of Gerrit");
  }
  if (commit.held_back) {
    parts.push("held back");
  }
  return parts.join(" · ");
}

/** Last activity per chain, as remembered from the previous look. */
export type Seen = Record<string, string>;

/** Chains whose last activity is newer than what was seen. A chain never seen before is not news. */
export function chainsWithActivity(overview: Overview | undefined, seen: Seen): Set<string> {
  const active = new Set<string>();
  for (const section of overview?.sections ?? []) {
    for (const chain of section.chains) {
      const before = seen[chain.key];
      if (before !== undefined && chain.last_activity !== null && chain.last_activity > before) {
        active.add(chain.key);
      }
    }
  }
  return active;
}

/** `seen`, with first sightings recorded and chains that are gone dropped; `mark` keys count as seen now. */
export function rememberSeen(overview: Overview | undefined, seen: Seen, mark: Iterable<string> = []): Seen {
  const marked = new Set(mark);
  const next: Seen = {};
  for (const section of overview?.sections ?? []) {
    for (const chain of section.chains) {
      const before = seen[chain.key];
      next[chain.key] = before === undefined || marked.has(chain.key) ? (chain.last_activity ?? "") : before;
    }
  }
  return next;
}

/** Swaps that would conflict, by `swapKey`. */
export function blockedSwaps(moves: StackMoves | undefined): Map<string, MoveConflict> {
  const blocked = new Map<string, MoveConflict>();
  for (const swap of moves?.swaps ?? []) {
    if (swap.conflict) {
      blocked.set(swapKey(swap.lower, swap.upper), swap.conflict);
    }
  }
  return blocked;
}

export function swapKey(lower: string, upper: string): string {
  return `${lower}:${upper}`;
}

/** Why `moving` cannot change places with `other`, as one sentence. */
export function blockedMove(moving: string, other: string, conflict: MoveConflict): string {
  const files = conflict.files.length ? ` in ${conflict.files.join(", ")}` : "";
  return `"${moving}" cannot change places with "${other}": "${conflict.summary}" would conflict${files}.`;
}

/** `commits` (oldest first) with the one at `index` moved by `step` places; undefined when it cannot move. */
export function moved<T>(commits: readonly T[], index: number, step: number): T[] | undefined {
  const target = index + step;
  if (index < 0 || index >= commits.length || target < 0 || target >= commits.length || step === 0) {
    return undefined;
  }
  const next = [...commits];
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}

/**
 * `commits` (oldest first) after `dragged` is dropped on `target`: it takes the target's place,
 * whichever way it travelled, so dropping on the neighbour below swaps the two. No target (the
 * empty space under the list, which shows newest first) means the bottom: oldest. Undefined
 * when nothing would change.
 */
export function droppedOn<T>(commits: readonly T[], dragged: T, target: T | undefined): T[] | undefined {
  const from = commits.indexOf(dragged);
  const to = target === undefined ? 0 : commits.indexOf(target);
  return to < 0 ? undefined : moved(commits, from, to - from);
}
