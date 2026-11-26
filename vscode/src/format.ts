// How review state is put into words; how urgent it is, core says (`severity`). Pure: no `vscode`, no I/O.

import * as path from "node:path";

import { Attended, Chain, CommitStatus, EventsStatus, Headline, MoveConflict, Overview, Rebase, RebaseHeadline, RebaseStop, ReviewDone, ReviewState, Severity, Stack, StackMoves } from "./api";
import { Commented, commentState } from "./commentState";

/** Every reason core names (`Attention`, core/attention.py), in words: test/format.test.ts checks them against test/attention.json, which tests/test_attention_severity.py checks against core. */
const WORDS: Record<string, string> = {
  "ci-failed": "CI failed",
  diverged: "Gerrit has a patchset you never had",
  "unsquashed-fixup": "unsquashed fixup commit",
  "gerrit-unknown": "Gerrit state unknown",
  "unresolved-comments": "unresolved comments",
  "awaiting-reply": "comments wait on a reply",
  "drafts-to-publish": "drafts to publish with the push",
  "review-issues": "negative vote",
  "not-pushed": "not pushed",
  "ahead-of-gerrit": "local changes not pushed",
  "outdated-patchset": "Gerrit has a newer patchset",
  "awaiting-review": "awaiting review",
  "no-reviewers": "no reviewers",
  "waiting-on-you": "waiting for your review",
  "waiting-on-author": "waiting on the author",
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
 * The headline now: the rebase underway as the stack says it (the stack owns whether one is), else
 * the overview's, unless that one is a rebase that has ended since (state 24: "Rebase stopped at …"
 * stayed after the abort). A ger that serves no stack headline (before API 1.10) leaves the overview's.
 */
export function currentHeadline(overview: Pick<Overview, "headline"> | undefined, stack: Pick<Stack, "headline"> | undefined): Headline | undefined {
  const served = overview?.headline ?? undefined;
  if (stack?.headline === undefined) {
    return served;
  }
  return stack.headline ?? (served?.section === "local" ? undefined : served);
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

/** What to do next, by why it stopped; `unmerged` names the conflicted files. The ways on are Local stack's buttons (`REBASE_ACTIONS`), never raw git. */
const REBASE_NEXT: Record<RebaseStop, (unmerged: string) => string> = {
  edit: () => "Change the commit and amend it (git commit --amend), then Continue in Local stack.",
  split: () => "Commit what is left of the commit you split, then Continue in Local stack.",
  conflict: (unmerged) => `Resolve the conflicts${unmerged} and git add the files, then Continue in Local stack. Or go back with Abort.`,
  resolved: () => "The conflicts are resolved: Continue in Local stack commits the commit and goes on.",
  paused: () => "When you are done here, Continue in Local stack.",
};

/** A subject cut short enough for the status bar. */
function shortSubject(subject: string): string {
  return subject.length > 30 ? `${subject.slice(0, 29)}…` : subject;
}

/**
 * The status bar item while core's headline is a rebase underway: `text`, short, names the commit
 * it stopped at by its change number when it is on Gerrit, else by its subject; `next` says what
 * to do. `rebase` is the stack's, for the paths still unmerged and the ways on core allows: where
 * core refuses Continue with nothing left to resolve, `next` is core's refusal, which names the way
 * on there is (a resolution committed by hand goes on by Skip), as Local stack's banner does.
 */
export function rebaseItem(headline: RebaseHeadline, rebase?: Rebase | null): { text: string; next: string } {
  const { stop, number, subject } = headline;
  // A subject is quoted, so "Conflict in" does not read as naming a file.
  const what = number ? String(number) : subject ? `"${shortSubject(subject)}"` : undefined;
  const named = !stop ? "Rebase underway" : what ? REBASE_ITEM[stop](what) : stop === "paused" ? "Rebase paused" : "Rebase stopped";
  // Folding a fixup, core names the commit it folds into: the fixup itself is not the change.
  const text = headline.folding && what ? `${named}, folding a fixup into it` : named;
  const unmerged = rebase?.unmerged ?? [];
  const where = unmerged.length === 1 ? ` in ${unmerged[0]}` : unmerged.length ? ` in ${plural(unmerged.length, "file")}` : "";
  const refused = rebase && !unmerged.length && !rebase.actions.includes("continue") ? rebase.refused.continue : undefined;
  const next = refused
    ? `${sentence(refused)}.`
    : stop
      ? REBASE_NEXT[stop](where)
      : "Finish it with Continue in Local stack, or go back with Abort.";
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

/** The open comment chains in words (`💬2 open`: never `2/5`, which reads as "2 done"), and a small mark for those your drafts answer (`✎1`: they wait on your push). */
export function commentMarks(item: Commented): string[] {
  const state = commentState(item);
  return [state?.unresolved ? `💬${state.label}` : "", state?.drafted ? `✎${state.drafted}` : ""].filter(Boolean);
}

/** The votes that exist and the unresolved comments, e.g. `V+1 CR-1 💬2 open ✎1`; `no votes` when there is nothing. */
export function votes(item: { verified: number | null; code_review: number | null } & Commented): string {
  const parts: string[] = [];
  if (item.verified !== null && item.verified !== undefined) {
    parts.push(vote("V", item.verified));
  }
  if (item.code_review !== null && item.code_review !== undefined) {
    parts.push(vote("CR", item.code_review));
  }
  parts.push(...commentMarks(item));
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
  const comments = commentMarks(commit).map((mark) => ` ${mark}`).join("");
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
export function refreshMode(events: EventsStatus | undefined, pollSeconds: number, last?: Date): string {
  // Gerrit's events and the poll are about Gerrit; the repository is watched whatever they do.
  const local = " Commits, checkouts and rebases here refresh them at once.";
  if (events?.state === "live") {
    return `Following Gerrit's event stream${events.source ? ` (${events.source})` : ""}: review activity refreshes the views, and a poll every ${age(pollSeconds)} is the safety net.${local}`;
  }
  // Not following Gerrit live: how fresh the views are is said, beside how they keep up.
  const at = last ? `Last read from Gerrit at ${clock(last)}. ` : "";
  const every = `${at}Refreshing every ${age(pollSeconds)}`;
  const focus = ", and when this window gets the focus again";
  if (!events || events.state === "connecting") {
    return `${every}${focus}.${local}`;
  }
  const why = events.reason ? `: ${events.reason.replace(/\.$/, "")}` : "";
  return `${events.state === "retrying" ? `${every} while Gerrit's event stream is down${why}${focus}.` : `${every}${focus}; no Gerrit event stream${why}.`}${local}`;
}

/**
 * Whether the views follow Gerrit live, in a few words beside Review chains' title: `live` while the
 * event stream is; else when Gerrit was last read and when the next check is (`checked 12:40 · next
 * in 58 min`); `not reachable since 12:41` while Gerrit cannot be asked. Unobtrusive, and only here:
 * the status bar's tooltip has the whole of it (`refreshMode`).
 */
export function liveness(options: { live: boolean; checked?: Date; nextAt?: number; awaySince?: Date; now: number }): string {
  if (options.awaySince) {
    return `not reachable since ${clock(options.awaySince)}`;
  }
  if (options.live) {
    return "live";
  }
  const minutes = options.nextAt === undefined ? undefined : Math.max(1, Math.ceil((options.nextAt - options.now) / 60_000));
  const next = minutes === undefined ? "" : minutes >= 60 ? ` · next at ${clock(new Date(options.nextAt!))}` : ` · next in ${minutes} min`;
  return options.checked ? `checked ${clock(options.checked)}${next}` : `not live${next}`;
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

/**
 * A chain's votes: the worst cast of each label, then the labels some members have no vote on
 * (`V+1 CR+1 💬5 · V missing on 2, CR on 1`). A label no member has a vote on is left out, as
 * for one change; `no votes` only when no member has any.
 */
export function chainVotes(chain: Chain): string {
  const cast = chain.votes_cast ?? chain;
  const missing = chain.votes_missing ?? { verified: 0, code_review: 0 };
  const shown = votes({ ...chain, verified: cast.verified, code_review: cast.code_review });
  const gaps = ([["V", missing.verified], ["CR", missing.code_review]] as const).filter(([, n]) => n > 0 && n < chain.depth);
  const words = gaps.map(([label, n], i) => (i === 0 ? `${label} missing on ${n}` : `${label} on ${n}`));
  return words.length ? `${shown} · ${words.join(", ")}` : shown;
}

export function chainDescription(chain: Chain, section: string): string {
  const parts = [plural(chain.depth, "change"), chainVotes(chain)];
  if (section === "to-review") {
    parts.unshift(chain.owner.name);
    if (chain.your_turn !== false) {
      parts.push(`waiting ${age(chain.unreviewed_age_seconds)}`);
    }
  }
  parts.push(`active ${age(chain.wait_age_seconds)} ago`);
  return parts.join(" · ");
}

/** Last activity per chain, as remembered from the previous look. */
export type Seen = Record<string, string>;

/**
 * Chains whose last activity is newer than what was seen. A chain never seen before is not news,
 * nor one whose last activity is your own (core's `last_activity_yours`: your push, your vote).
 */
export function chainsWithActivity(overview: Overview | undefined, seen: Seen): Set<string> {
  const active = new Set<string>();
  for (const section of overview?.sections ?? []) {
    for (const chain of section.chains) {
      const before = seen[chain.key];
      if (before !== undefined && !chain.last_activity_yours && chain.last_activity !== null && chain.last_activity > before) {
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
      // What you did yourself counts as seen: you were there.
      const now = before === undefined || marked.has(chain.key) || chain.last_activity_yours;
      next[chain.key] = now ? (chain.last_activity ?? "") : before;
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
  return `"${moving}" cannot change places with "${other}": ${wouldConflict(conflict)}.`;
}

/** `"Refactor parser init" would conflict in a.txt`: the first commit a rewrite or a rebase would stop at. */
export function wouldConflict(conflict: MoveConflict): string {
  return `"${conflict.summary}" would conflict${conflict.files.length ? ` in ${conflict.files.join(", ")}` : ""}`;
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

/**
 * `text` as Markdown that shows it as it is: every punctuation mark Markdown (or a theme icon,
 * `$(name)`) could read escaped, a line break a paragraph. `MarkdownString.appendText` turns
 * each space into `&nbsp;`, which a status bar tooltip shows as those six characters.
 */
export function markdownText(text: string): string {
  return text.replace(/[\\`*_{}[\]()#+\-.!|<>$~&]/g, "\\$&").replace(/\n/g, "\n\n");
}

/** Where leaving the review left you, in words: the branch this checkout is back on, or the review worktree freed. */
export function leftReview(done: ReviewDone, repo: string): string {
  const here = path.resolve(done.worktree ?? repo) === path.resolve(repo);
  const where = here ? (done.branch ? `this checkout is back on ${done.branch}` : "this checkout is back where it was") : "the review worktree is free for the next review";
  return `Left the review of change ${done.done}: ${where}.`;
}

/** A time of day as the status bar says it: `08:51`. */
export function clock(time: Date): string {
  return `${String(time.getHours()).padStart(2, "0")}:${String(time.getMinutes()).padStart(2, "0")}`;
}
