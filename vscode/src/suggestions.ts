// Suggested edits as a comment thread shows them (old → new, labelled), and where one goes in your
// file, or why it cannot. What a comment suggests, and where its lines are in your working file, is
// core's (`suggested_edits`, each replacement's `worktree` and `original`). No `vscode` import.

import { Comment, FixReplacement, SuggestedEdit } from "./api";

/** A fence longer than any run of backticks in `lines`, so none of them closes it. */
function fence(lines: string[]): string {
  const longest = Math.max(0, ...lines.flatMap((line) => (line.match(/`+/g) ?? []).map((run) => run.length)));
  return "`".repeat(Math.max(3, longest + 1));
}

/** `text` as lines, each after `prefix`; a final line ending adds no line, and nothing is no line. */
function side(prefix: string, text: string): string[] {
  const plain = text.replace(/\r\n/g, "\n");
  return plain === "" ? [] : plain.replace(/\n$/, "").split("\n").map((line) => `${prefix}${line}`);
}

/** One replacement as a small diff: what it replaces, when that is known, then what it writes. */
export function replacementDiff(replacement: FixReplacement, old: string | null | undefined): string[] {
  const lines = [...(old === null || old === undefined ? [] : side("-", old)), ...side("+", replacement.replacement)];
  const mark = fence(lines);
  return [`${mark}diff`, ...lines, mark];
}

/** The ```` ```suggestion ```` block of `edit` as the comment's text has it; undefined for a Gerrit fix suggestion. */
function block(edit: SuggestedEdit): string | undefined {
  return edit.in_message ? `\`\`\`suggestion\n${edit.replacements[0]?.replacement ?? ""}\`\`\`` : undefined;
}

/**
 * The comment's text in Markdown with each edit it suggests as old → new, under **Suggested edit**:
 * a ```` ```suggestion ```` block in its place, a fix suggestion below the text, its location named.
 * `old` is the text a replacement replaces, where known. Without suggested edits, core's Markdown.
 */
export function suggestionMarkdown(comment: Comment, old: (replacement: FixReplacement) => string | null | undefined): string {
  const edits = comment.suggested_edits ?? [];
  if (!edits.length) {
    return comment.markdown;
  }
  let text = comment.message.replace(/\r\n/g, "\n");
  const below: string[] = [];
  for (const edit of edits) {
    const label = `**Suggested edit**${edit.description ? `: ${edit.description}` : ""}`;
    const diffs = edit.replacements.flatMap((rep) => [...(edit.in_message ? [] : [`\`${rep.location}\``]), ...replacementDiff(rep, old(rep))]);
    const shown = [label, ...diffs].join("\n");
    const written = block(edit);
    if (written && text.includes(written)) {
      text = text.replace(written, shown);
    } else {
      below.push(shown);
    }
  }
  return [text, ...below].join("\n\n");
}

/** The edit a chain's newest comment with one suggests: the one Apply writes. */
export function latestSuggestion(comments: Comment[]): { comment: Comment; edit: SuggestedEdit } | undefined {
  for (const comment of [...comments].reverse()) {
    const edit = comment.suggested_edits?.[0];
    if (edit?.replacements.length) {
      return { comment, edit };
    }
  }
  return undefined;
}

/** Where a replacement goes in your file: 0-based lines and characters, as an editor counts them. */
export interface Place {
  path: string;
  start: { line: number; character: number };
  end: { line: number; character: number };
  replacement: string;
}

export const APPLY_WORDS = {
  moved: (where: string) => `The lines the suggestion replaces (${where}) have changed in your file since the comment was written, or ger cannot tell where they are now: apply it by hand.`,
  differs: (where: string) => `${where} in your file is not what the suggestion replaces any more: apply it by hand.`,
  applied: (where: string) => `Applied the suggested edit to ${where}. Nothing is committed: amend it in (or ger fix) once you are done.`,
};

/**
 * Where each replacement of `edit` goes in your file (core's `worktree`: its lines there unchanged),
 * checked against what the file says now (`now`, the text of a range): it must still be the text the
 * suggestion replaces (`original`), unsaved edits included. Else why not, in a sentence.
 */
export function applyPlan(edit: SuggestedEdit, now: (place: Place) => string | undefined): { places: Place[] } | { refused: string } {
  const places: Place[] = [];
  for (const rep of edit.replacements) {
    const range = rep.worktree;
    if (!range) {
      return { refused: APPLY_WORDS.moved(rep.location) };
    }
    const place: Place = {
      path: rep.path,
      start: { line: range.start_line - 1, character: range.start_character },
      end: { line: range.end_line - 1, character: range.end_character },
      replacement: rep.replacement,
    };
    const found = now(place);
    if (rep.original === null || rep.original === undefined || found === undefined || found.replace(/\r\n/g, "\n") !== rep.original.replace(/\r\n/g, "\n")) {
      return { refused: APPLY_WORDS.differs(`${rep.path}:${range.start_line}`) };
    }
    places.push(place);
  }
  return { places };
}
