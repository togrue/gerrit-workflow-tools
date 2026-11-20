// How review state is put into words and ordered by urgency. Pure: no `vscode`, no I/O.

import { Chain, Comment, CommitStatus, EventsStatus, Headline, MoveConflict, Overview, ReviewState, StackMoves } from "./api";

export type Severity = "error" | "warning" | "pending" | "ok";

const ERRORS = ["ci-failed", "merged-drift", "abandoned", "missing-change-id", "invalid-change-id"];
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
  "invalid-change-id": "invalid Change-Id",
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

const SEVERITY_ICONS: Record<Severity, string> = { error: "error", warning: "warning", pending: "clock", ok: "pass" };

/** Every icon `attentionIcon` can give. */
export const ATTENTION_ICONS: readonly string[] = [...Object.values(SEVERITY_ICONS), "comment-unresolved"];

/**
 * The codicon for a change with these attention reasons, and how urgent it is. The comment icon
 * means unresolved comments, which the Change view lists; a negative vote or a newer patchset on
 * Gerrit is a warning without one.
 */
export function attentionIcon(reasons: readonly string[]): { icon: string; severity: Severity } {
  const level = severity(reasons);
  const comments = level === "warning" && reasons.includes("unresolved-comments");
  return { icon: comments ? "comment-unresolved" : SEVERITY_ICONS[level], severity: level };
}

/**
 * The status bar's codicon for the headline: an eye for a chain waiting on your review, else the
 * icon `attentionIcon` gives its reason (a negative vote is a warning, not a comment bubble); a
 * check when nothing needs you.
 */
export function headlineIcon(headline: Pick<Headline, "section" | "reason"> | null | undefined): string {
  if (!headline) {
    return "pass";
  }
  return headline.section === "to-review" ? "eye" : attentionIcon([headline.reason]).icon;
}

/** Every attention reason this knows, in words: what `reasonWords` can say of ours. */
export const REASON_WORDS: readonly string[] = Object.values(WORDS);

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

/** What a fix's replacement does and the lines it writes (none for a delete), its location passed through `where`. */
interface FixPart {
  what: string;
  code: string[];
}

/**
 * The Gerrit fix suggestions of a comment, as they are written out below its text. A
 * ```suggestion block of the text is already there, so it is left out.
 */
function shownFixes(comment: Pick<Comment, "suggested_edits">, where: (location: string) => string): { description: string; parts: FixPart[] }[] {
  return (comment.suggested_edits ?? [])
    .filter((edit) => !edit.in_message)
    .map((edit) => ({
      description: edit.description,
      parts: edit.replacements.map((rep) => {
        const location = where(rep.location);
        if (!rep.replacement) {
          return { what: `${location}: delete`, code: [] };
        }
        const r = rep.range;
        const inserts = r.start_line === r.end_line && r.start_character === r.end_character;
        return { what: inserts ? `insert at ${location}:` : `${location} becomes:`, code: rep.replacement.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n") };
      }),
    }));
}

/** A Markdown fence longer than any run of backticks in `lines`, so none of them closes it. */
export function codeFence(lines: string[]): string {
  const longest = Math.max(0, ...lines.flatMap((line) => (line.match(/`+/g) ?? []).map((run) => run.length)));
  return "`".repeat(Math.max(3, longest + 1));
}

/**
 * A comment's text with the Gerrit fix suggestions it carries written out below it in Markdown:
 * what each replaces, and with what (fenced). For the editor's comment threads.
 */
export function commentText(comment: Pick<Comment, "message" | "suggested_edits">): string {
  const fixes = shownFixes(comment, (location) => `\`${location}\``).map(({ description, parts }) => {
    const lines = [`**Suggested fix:**${description ? ` ${description}` : ""}`];
    for (const { what, code } of parts) {
      lines.push(what);
      if (code.length) {
        const fence = codeFence(code);
        lines.push(fence, ...code, fence);
      }
    }
    return lines.join("\n");
  });
  return [comment.message, ...fixes].filter(Boolean).join("\n\n");
}

/**
 * The same as plain text, as `ger show` writes it: for the Change view and tooltips, which show
 * text as it is and would print Markdown's `**` and backticks.
 */
export function commentPlainText(comment: Pick<Comment, "message" | "suggested_edits">): string {
  const fixes = shownFixes(comment, (location) => location).map(({ description, parts }) => {
    const lines = [description ? `suggested fix: ${description}` : "suggested fix:"];
    for (const { what, code } of parts) {
      lines.push(`  ${what}`, ...code.map((line) => `  + ${line}`));
    }
    return lines.join("\n");
  });
  return [comment.message, ...fixes].filter(Boolean).join("\n\n");
}
