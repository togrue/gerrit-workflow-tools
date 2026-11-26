// A change's comments at a glance, for every row that stands for a change (Review chains, Local
// stack, the Change view's head, the task header): one state with a colour and a shape, the number
// of open chains, and their share of all chains, which a webview draws as a ring (`share`) and a
// tree row writes in words (`3 open`). Never `3/5`: it reads as "3 of 5 done". Pure: no `vscode`,
// no I/O. The comments themselves, as a hover lists them: commentEntries.ts.


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
  /** The open count as a webview's badge shows it beside the ring: `2`; empty once all are resolved. */
  text: string;
  /** What a text-only row says: `2 open`, `all resolved`, or empty when there is nothing to say. */
  label: string;
  /** The ring: open of all (0..1), 1 when all is not known but some are open, 0 once all are resolved. */
  share: number;
  /** The second cue besides the colour: a solid ring (to answer), a dashed one (your drafts), a thin one (waiting), a tick (resolved). */
  shape: CommentShape;
  /** The same in words, for a tooltip: `3 of 5 open · 2 answered by your drafts`. */
  words: string;
}

export type CommentShape = "solid" | "dashed" | "thin" | "tick";

const SHAPES: Record<CommentTone, CommentShape> = { open: "solid", draft: "dashed", waiting: "thin", resolved: "tick" };

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
  const resolved = all === 1 ? "the comment chain resolved" : all !== null ? `all ${chains(all)} resolved` : total === null ? "every comment chain resolved" : "";
  const open = all !== null ? `${count} of ${chains(all)} open` : `${chains(count)} open`;
  const parts = [
    count ? open : resolved,
    left ? `${left} to answer` : "",
    drafted ? `${drafted} answered by your drafts` : "",
    yours > drafted ? `${yours - drafted === 1 ? "a draft" : `${yours - drafted} drafts`} of yours not published` : "",
    awaiting ? `${awaiting} waiting on a reply` : "",
  ];
  const resolvedAll = count === 0 && (all !== null || total === null);
  return {
    tone,
    unresolved: count,
    total: all,
    toAnswer: left,
    drafted: yours,
    text: count ? String(count) : "",
    label: count ? `${count} open` : resolvedAll ? "all resolved" : "",
    share: all !== null ? count / all : count ? 1 : 0,
    shape: SHAPES[tone],
    words: parts.filter(Boolean).join(" · "),
  };
}

/** The phrases `words` is made of, as patterns around a count pattern `n`: what a tooltip of ours may say (anonymize.ts keeps them). */
export function commentPhrases(n: string): string[] {
  const chains = `comment chains?`;
  return [
    `${n} of ${n} ${chains} open`,
    `${n} ${chains} open`,
    "[Ee]very comment chain resolved",
    "[Tt]he comment chain resolved",
    `[Aa]ll ${n} comment chains resolved`,
    "[Aa] draft of yours not published",
    `${n} drafts of yours not published`,
    `${n} to answer`,
    `${n} answered by your drafts`,
    `${n} waiting on a reply`,
  ];
}

