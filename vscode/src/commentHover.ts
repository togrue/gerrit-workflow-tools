// A change's comments where its row is: the colour of its comment state on a Review chains row
// (a file decoration: trees draw one icon per row), and on hover the comments themselves, each with
// Go there, Reply Done and Apply. Local stack draws the same entries in its own hover (media/stack.js).

import * as vscode from "vscode";

import { CommentChain } from "./api";
import { CommentEntry, commentEntries } from "./commentEntries";
import { CommentState, CommentTone, commentState, Commented } from "./commentState";
import { attentionIcon } from "./format";
import { ChangeTarget } from "./model";

const SCHEME = "ger-comments";

/** The commands a hover's links run: each takes a {@link CommentLink}. */
export const COMMENT_LINKS = { goto: "ger.comment.goto", done: "ger.comment.done", apply: "ger.comment.apply" } as const;

/** What a hover needs from the rest of the extension: the comment chains of a change, and your drafts on it. */
export interface ChangeComments {
  /** The chains of `target`: what the Change view has loaded when it shows it, else asked once. */
  chains(target: ChangeTarget): Promise<CommentChain[] | undefined>;
  /** Unpublished comments of yours on `target` that core's counts leave out, as far as known without asking. */
  drafts(target: ChangeTarget): number;
}

/** What a hover's link names: the change, as a view opens it, and the chain. */
export interface CommentLink {
  target: ChangeTarget;
  root: string;
}

/** The colour of a comment state on a row: blue for drafts of yours, yellow for what waits on your answer. */
const COLORS: Partial<Record<CommentTone, string>> = { draft: "charts.blue", open: "list.warningForeground" };

/**
 * The resource a row carries so the decoration below colours it, or undefined when nothing is to be
 * said: the state is in the URI, so the provider needs no lookup.
 */
export function commentResource(item: Commented, drafts = 0): vscode.Uri | undefined {
  const state = commentState(item, drafts);
  if (!state || !COLORS[state.tone]) {
    return undefined;
  }
  const badge = state.tone === "open" ? String(Math.min(state.toAnswer, 99)) : "✎";
  return vscode.Uri.from({ scheme: SCHEME, path: "/change", query: new URLSearchParams({ tone: state.tone, badge, words: state.words }).toString() });
}

/** Colours the rows {@link commentResource} marked. */
export function commentDecorations(): vscode.FileDecorationProvider {
  return {
    provideFileDecoration(uri) {
      if (uri.scheme !== SCHEME) {
        return undefined;
      }
      const query = new URLSearchParams(uri.query);
      const color = COLORS[query.get("tone") as CommentTone];
      return color ? new vscode.FileDecoration(query.get("badge") ?? undefined, query.get("words") ?? undefined, new vscode.ThemeColor(color)) : undefined;
    },
  };
}

/** An entry's icon: an open one wears the icon attention gives unresolved comments (format.ts, the one owner of that icon). */
const ICONS: Record<CommentTone, string> = {
  draft: "comment-draft",
  open: attentionIcon({ attention_reasons: ["unresolved-comments"], severity: "warning" }).icon,
  waiting: "comment",
  resolved: "pass",
};

/** A command link of a hover. */
function link(title: string, command: string, arg: CommentLink): string {
  return `[${title}](command:${command}?${encodeURIComponent(JSON.stringify([arg]))})`;
}

/** Plain text in Markdown, nothing in it read as Markdown. */
function plain(text: string): string {
  return text.replace(/[\\`*_{}[\]()#+\-.!|<>~$]/g, (c) => `\\${c}`);
}

/** The paragraphs a hover lists the entries in; `target` is the change their links open, `more` how many are not listed. */
export function entriesMarkdown(entries: CommentEntry[], more: number, target: ChangeTarget): string[] {
  // What the row knew is not needed to open it again, and would only make each link long.
  const bare: ChangeTarget = { ...target, known: undefined };
  const lines = entries.map((entry) => {
    const arg = { target: bare, root: entry.root };
    const links = [link("Go there", COMMENT_LINKS.goto, arg), entry.done ? link("Reply Done", COMMENT_LINKS.done, arg) : "", entry.apply ? link("Apply", COMMENT_LINKS.apply, arg) : ""];
    return [
      `$(${ICONS[entry.tone]}) **${plain(entry.author)}** · ${plain(entry.where)} · *${entry.state}*`,
      entry.text ? `> ${plain(entry.text)}` : "",
      links.filter(Boolean).join(" · "),
    ]
      .filter(Boolean)
      .join("\n\n");
  });
  return more > 0 ? [...lines, `…and ${more} more: the Change view lists them all.`] : lines;
}

/** A row's tooltip with its comments below what it said: `head` (Markdown), the state, the chains. */
export function commentTooltip(head: string, state: CommentState | undefined, chains: readonly CommentChain[], target: ChangeTarget, applies: boolean): vscode.MarkdownString {
  const entries = commentEntries(chains, applies);
  const parts = [head, state ? `**Comments**: ${state.words}` : "", ...entriesMarkdown(entries, chains.length - entries.length, target)];
  const markdown = new vscode.MarkdownString(parts.filter(Boolean).join("\n\n---\n\n"), true);
  markdown.isTrusted = { enabledCommands: Object.values(COMMENT_LINKS) };
  return markdown;
}
