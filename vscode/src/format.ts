// How review state is put into words; how urgent it is, core says (`severity`). Pure: no `vscode`, no I/O.

import { Attended, Chain, CommitStatus, EventsStatus, Headline, MoveConflict, Overview, Rebase, RebaseHeadline, RebaseStop, ReviewState, Severity, StackMoves } from "./api";

/** Every reason core names (`Attention`, core/attention.py), in words: test/format.test.ts checks them against test/attention.json, which tests/test_attention_severity.py checks against core. */
const WORDS: Record<string, string> = {
  "ci-failed": "CI failed",
  diverged: "Gerrit has a patchset you never had",
  "unsquashed-fixup": "unsquashed fixup commit",
  "gerrit-unknown": "Gerrit state unknown",
  "unresolved-comments": "unresolved comments",
  "review-issues": "negative vote",
  "not-pushed": "not pushed",
  "ahead-of-gerrit": "local changes not pushed",
  "outdated-patchset": "Gerrit has a newer patchset",
  "awaiting-review": "awaiting review",
  "no-reviewers": "no reviewers",
  "waiting-on-you": "waiting for your review",
  "chain-blocked": "blocked by an earlier change",
  "missing-change-id": "no Change-Id",
  "invalid-change-id": "invalid Change-Id",
  "merged-drift": "merged, but differs locally",
  "merged-unknown": "merged, equivalence unknown",
  abandoned: "abandoned",
  wip: "work in progress",
};

const SEVERITY_ICONS: Record<Severity, string> = { error: "error", warning: "warning", pending: "clock", ok: "pass" };

/** Every icon `attentionIcon` can give. */
export const ATTENTION_ICONS: readonly string[] = [...Object.values(SEVERITY_ICONS), "comment-unresolved", "eye"];

/**
 * The codicon for a change with these attention reasons, and how urgent it is. An eye means it
 * waits on your review, as under To review. The comment icon means unresolved comments, which the
 * Change view lists; a negative vote or a newer patchset on Gerrit is a warning without one.
 */
export function attentionIcon({ attention_reasons: reasons, severity: level }: Readonly<Attended>): { icon: string; severity: Severity } {
  const comments = level === "warning" && reasons.includes("unresolved-comments");
  const yours = level === "pending" && reasons.includes("waiting-on-you");
  return { icon: comments ? "comment-unresolved" : yours ? "eye" : SEVERITY_ICONS[level], severity: level };
}

/**
 * The status bar's codicon for the headline: the icon `attentionIcon` gives its reason (an eye for
 * a chain waiting on your review; a negative vote is a warning, not a comment bubble); a check
 * when nothing needs you.
 */
export function headlineIcon(headline: Pick<Headline, "reason" | "severity"> | null | undefined): string {
  return headline ? attentionIcon({ attention_reasons: [headline.reason], severity: headline.severity }).icon : "pass";
}

/** What the status bar says of a rebase underway, by why it stopped, around the commit it is at. */
const REBASE_ITEM: Record<RebaseStop, (what: string) => string> = {
  edit: (what) => `Rebase stopped at ${what}`,
  split: (what) => `Rebase stopped to split ${what}`,
  conflict: (what) => `Conflict in ${what}`,
  resolved: (what) => `Conflict resolved in ${what}`,
  paused: (what) => `Rebase paused at ${what}`,
};

/** What to do next, by why it stopped; `unmerged` names the conflicted files. */
const REBASE_NEXT: Record<RebaseStop, (unmerged: string) => string> = {
  edit: () => "Change the commit and amend it (git commit --amend), then git rebase --continue.",
  split: () => "Commit what is left of the commit you split, then git rebase --continue.",
  conflict: (unmerged) => `Resolve the conflicts${unmerged} and git add the files, then git rebase --continue. Or go back with git rebase --abort.`,
  resolved: () => "The conflicts are resolved: git rebase --continue commits the commit and goes on.",
  paused: () => "When you are done here, git rebase --continue.",
};

/** A subject cut short enough for the status bar. */
function shortSubject(subject: string): string {
  return subject.length > 30 ? `${subject.slice(0, 29)}…` : subject;
}

/**
 * The status bar item while core's headline is a rebase underway: `text`, short, names the commit
 * it stopped at by its change number when it is on Gerrit, else by its subject; `next` says what
 * to do. `rebase` is the stack's, for the paths still unmerged.
 */
export function rebaseItem(headline: RebaseHeadline, rebase?: Rebase | null): { text: string; next: string } {
  const { stop, number, subject } = headline;
  // A subject is quoted, so "Conflict in" does not read as naming a file.
  const what = number ? String(number) : subject ? `"${shortSubject(subject)}"` : undefined;
  const text = !stop ? "Rebase underway" : what ? REBASE_ITEM[stop](what) : stop === "paused" ? "Rebase paused" : "Rebase stopped";
  const unmerged = rebase?.unmerged ?? [];
  const where = unmerged.length === 1 ? ` in ${unmerged[0]}` : unmerged.length ? ` in ${plural(unmerged.length, "file")}` : "";
  const next = stop ? REBASE_NEXT[stop](where) : "Finish it with git rebase --continue, or go back with git rebase --abort.";
  return { text, next };
}

/** Every attention reason this knows, in words: what `reasonWords` can say of ours. */
export const REASON_WORDS: readonly string[] = Object.values(WORDS);

/** Every attention reason this has words for: all that core names. */
export const WORDED_REASONS: readonly string[] = Object.keys(WORDS);

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

/**
 * A commit's votes. When its code moved on from the pushed patchset, that patchset's votes,
 * marked as such: Gerrit may not keep them for the next push.
 */
export function commitVotes(commit: CommitStatus): string {
  const last = commit.last_pushed_votes;
  if (!last || (last.verified === null && last.code_review === null)) {
    return votes(commit);
  }
  const comments = commit.comments_unresolved > 0 ? ` 💬${commit.comments_unresolved}` : "";
  return `last pushed ${votes({ ...last, comments_unresolved: 0 })}${comments}`;
}

export const FILE_STATES: Record<string, string> = { A: "added", M: "modified", D: "deleted", R: "renamed", C: "copied", T: "type changed" };

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

/** Gerrit's event stream can still bring news: worth asking `GET /activity` again. */
export function eventsMayCome(events: EventsStatus | undefined): boolean {
  return events !== undefined && ["connecting", "live", "retrying"].includes(events.state);
}

/** How the views stay current, in words, for the status bar tooltip and the log. */
export function refreshMode(events: EventsStatus | undefined, pollSeconds: number): string {
  // Gerrit's events and the poll are about Gerrit; the repository is watched whatever they do.
  const local = " Commits, checkouts and rebases here refresh them at once.";
  if (events?.state === "live") {
    return `Following Gerrit's event stream${events.source ? ` (${events.source})` : ""}: review activity refreshes the views, and a poll every ${age(pollSeconds)} is the safety net.${local}`;
  }
  const every = `Refreshing every ${age(pollSeconds)}`;
  if (!events || events.state === "connecting") {
    return `${every}.${local}`;
  }
  const why = events.reason ? `: ${events.reason.replace(/\.$/, "")}` : "";
  return `${events.state === "retrying" ? `${every} while Gerrit's event stream is down${why}.` : `${every}; no Gerrit event stream${why}.`}${local}`;
}

/** What `ger review` did, in words. `inPlace`: it switched this checkout instead of using the review worktree. */
export function reviewOpened(review: Pick<ReviewState, "number" | "patch_set" | "branch" | "worktree" | "staged">, inPlace: boolean): string {
  const change = `Change ${review.number} (patchset ${review.patch_set}) is checked out for review`;
  const staged = review.staged ? ", with its changes staged" : "";
  return inPlace
    ? `${change}: this checkout was switched to ${review.branch}${staged}. Leave Review Checkout switches back.`
    : `${change} on ${review.branch} in ${review.worktree}${staged}.`;
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
/**
 * Whether a loaded headline is news, and the headline to remember: one seen for the first time,
 * or after a load you caused, is not news. A rebase is yours, never news, and not remembered, so
 * the inbox's headline from before it is not news again when it ends.
 */
export function headlineNews(last: string | undefined, headline: Headline | undefined, quietly: boolean): { news: boolean; last: string | undefined } {
  if (headline?.section === "local") {
    return { news: false, last };
  }
  const key = headline ? `${headline.reason}:${headline.number}` : undefined;
  return { news: quietly && headline !== undefined && key !== last, last: key };
}

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

/**
 * `order` after the `moving` items are taken out and put back together at `gap`, in the order
 * they had. Gap 0 is before the first item, `order.length` after the last. Undefined when
 * nothing would change, or nothing of `moving` is in `order`.
 */
export function movedTo<T>(order: readonly T[], moving: readonly T[], gap: number): T[] | undefined {
  const picked = order.filter((item) => moving.includes(item));
  if (!picked.length || gap < 0 || gap > order.length) {
    return undefined;
  }
  const stays = (item: T) => !moving.includes(item);
  const next = [...order.slice(0, gap).filter(stays), ...picked, ...order.slice(gap).filter(stays)];
  return next.every((item, index) => item === order[index]) ? undefined : next;
}

export type ChainSort = "state" | "newest" | "oldest";

export const CHAIN_SORTS: Record<ChainSort, string> = {
  state: "By chain state",
  newest: "By last activity, newest first",
  oldest: "By last activity, oldest first",
};

/** The chains of one section in the order asked for; `state` is the order the service gives (most urgent first). */
export function sortedChains(chains: readonly Chain[], sort: ChainSort): Chain[] {
  const time = (chain: Chain) => chain.last_activity ?? "";
  if (sort === "state") {
    return [...chains];
  }
  return [...chains].sort((a, b) => (sort === "newest" ? time(b).localeCompare(time(a)) : time(a).localeCompare(time(b))));
}

/** A LOCATION for `ger comment`: the file, one line, or a range of lines (from 1). */
export function commentLocation(file: string, lines?: { start: number; end: number }): string {
  if (!lines) {
    return file;
  }
  const [start, end] = [Math.min(lines.start, lines.end), Math.max(lines.start, lines.end)];
  return start === end ? `${file}:${start}` : `${file}:${start}-${end}`;
}
