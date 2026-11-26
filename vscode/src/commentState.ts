// A change's comments at a glance, for every row that stands for a change (Review chains, Local
// stack, the Change view's head): one state and its colour, and the share of open chains in all of
// them (`2/5`). Pure: no `vscode`, no I/O. The comments themselves, as a hover lists them: commentEntries.ts.


/**
 * `draft`: drafts of yours not published yet (blue); `open`: unresolved comments wait for your
 * answer (yellow); `waiting`: the unresolved ones wait on the other side; `resolved`: all resolved
 * (both plain).
 */
export type CommentTone = "draft" | "open" | "waiting" | "resolved";

export interface CommentState {
  tone: CommentTone;
  unresolved: number;
  /** Every chain, resolved or not (core's `comments_total`); null when core did not count them. */
  total: number | null;
  toAnswer: number;
  drafted: number;
  /** The share as the row shows it: `2/5` (open of all), `2` when all is not known, `0/5` once all are resolved. */
  text: string;
  /** The same in words, for a tooltip. */
  words: string;
}

/** What `commentState` reads: core's counts of a change. */
export interface Commented {
  comments_unresolved: number;
  comments_awaiting_reply?: number;
  comments_drafted?: number;
  comments_to_answer?: number;
  comments_total?: number | null;
}

/** How a row's unresolved comment chains split: left to answer, answered by your drafts, waiting on a reply (core's counts). */
export function commentTurns(item: Commented): { count: number; left: number; drafted: number; awaiting: number } {
  const count = item.comments_unresolved;
  const awaiting = Math.min(item.comments_awaiting_reply ?? 0, count);
  const drafted = Math.min(item.comments_drafted ?? 0, count - awaiting);
  return { count, left: item.comments_to_answer ?? count - awaiting - drafted, drafted, awaiting };
}

const chains = (count: number) => `${count} comment chain${count === 1 ? "" : "s"}`;

/**
 * The comment state of a change from core's counts. `drafts`: unpublished comments of yours known
 * beside them (the Change view's chains of the change shown); core counts only the drafts that
 * answer an unresolved chain. Undefined when the change has no comments at all.
 */
export function commentState(item: Commented, drafts = 0): CommentState | undefined {
  const { count, left, drafted, awaiting } = commentTurns(item);
  const total = item.comments_total === undefined ? null : item.comments_total;
  const yours = Math.max(drafted, drafts);
  // Gerrit says nothing is there (0), or an older ger did not say (undefined): only what is unresolved shows.
  if (!count && !yours && (total === 0 || item.comments_total === undefined)) {
    return undefined;
  }
  // What waits for your answer leads: drafts of yours are blue once nothing is left to answer.
  const tone: CommentTone = left > 0 ? "open" : yours ? "draft" : count > 0 ? "waiting" : "resolved";
  // Gerrit's own count wins over a total that is behind it; a total of none says nothing of its own.
  const all = total !== null && total > 0 && total >= count ? total : null;
  const text = all !== null ? `${count}/${all}` : count ? String(count) : "";
  const parts = [
    all !== null ? `${count} of ${chains(all)} unresolved` : count ? `${chains(count)} unresolved` : total === null ? "every comment chain resolved" : "",
    left ? `${left} to answer` : "",
    drafted ? `${drafted} answered by your drafts` : "",
    yours > drafted ? `${yours - drafted === 1 ? "a draft" : `${yours - drafted} drafts`} of yours not published` : "",
    awaiting ? `${awaiting} waiting on a reply` : "",
  ];
  return { tone, unresolved: count, total: all, toAnswer: left, drafted: yours, text, words: parts.filter(Boolean).join(" · ") };
}

/** The phrases `words` is made of, as patterns around a count pattern `n`: what a tooltip of ours may say (anonymize.ts keeps them). */
export function commentPhrases(n: string): string[] {
  const chains = `comment chains?`;
  return [
    `${n} of ${n} ${chains} unresolved`,
    `${n} ${chains} unresolved`,
    "[Ee]very comment chain resolved",
    "[Aa] draft of yours not published",
    `${n} drafts of yours not published`,
    `${n} to answer`,
    `${n} answered by your drafts`,
    `${n} waiting on a reply`,
  ];
}

