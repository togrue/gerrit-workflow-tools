// The comments of a change as a hover lists them (a Review chains row, a Local stack badge): who,
// where, in which state, the first lines, and what can be done from there. Pure: no `vscode`.

import * as path from "node:path";

import { CommentChain } from "./api";
import { NOT_FILES, isYours, ownComment, toAnswer } from "./changeRows";
import { CommentTone } from "./commentState";
import { latestSuggestion } from "./suggestions";

/** One comment chain as a hover lists it, with what can be done from there. */
export interface CommentEntry {
  root: string;
  author: string;
  /** `file.ts:12`, or `whole change`. */
  where: string;
  /** `to answer`, `your draft`, `waiting on a reply`, `resolved`… */
  state: string;
  tone: CommentTone;
  /** The first lines of the newest comment, cut short. */
  text: string;
  /** Reply Done resolves it with your draft: somebody's open chain. */
  done: boolean;
  /** Apply writes the edit it suggests into your file: your own commit's chain. */
  apply: boolean;
}

/** The first `lines` lines of `text`, at most `width` characters. */
function firstLines(text: string, lines = 2, width = 160): string {
  const kept = text.trim().split("\n").slice(0, lines).join(" ").replace(/\s+/g, " ");
  return kept.length > width ? `${kept.slice(0, width - 1)}…` : kept;
}

/**
 * The chains a hover lists, open ones first, each with its newest comment.
 * `applies`: the change is your own commit of the local stack, so a suggested edit can be applied.
 * At most `limit`; the caller says how many more there are.
 */
export function commentEntries(list: readonly CommentChain[], applies: boolean, limit = 8): CommentEntry[] {
  // Open chains first, then the resolved ones: a hover is no place for every resolved one.
  const order = (chain: CommentChain) => (chain.resolved ? 1 : 0);
  return [...list].sort((a, b) => order(a) - order(b)).slice(0, limit).map((chain) => {
    const last = chain.comments.at(-1);
    const mine = isYours(chain);
    const drafted = !mine && ownComment(chain) !== undefined;
    const tone: CommentTone = mine || drafted ? "draft" : chain.resolved ? "resolved" : toAnswer(chain) ? "open" : "waiting";
    const state = mine ? "your draft" : drafted ? "your draft reply" : chain.resolved ? "resolved" : toAnswer(chain) ? "to answer" : "waiting on a reply";
    return {
      root: chain.root_id,
      author: last ? (last.author ?? "you") : "",
      where: NOT_FILES.includes(chain.path) ? "whole change" : `${path.posix.basename(chain.path)}${chain.line ? `:${chain.line}` : ""}`,
      state,
      tone,
      text: firstLines(last?.message ?? ""),
      done: !mine && !chain.resolved,
      apply: applies && !mine && !chain.resolved && latestSuggestion(chain.comments) !== undefined,
    };
  });
}
