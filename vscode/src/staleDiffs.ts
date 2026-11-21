// What becomes of the extension's diff tabs when the local stack is rewritten, and saving what is
// not saved before a rebase goes on. Pure: diffTabs.ts reads the tabs and acts on these answers.

import type { Ui } from "./ui";

/**
 * What a diff tab of the extension says about itself, in the fragment of its left side's
 * `ger-file` URI (`stack=<sha>`, then `&stale`). Only a diff of a commit that was in the local
 * stack when it opened carries one: Gerrit's patchsets and other people's changes never go stale.
 */
export interface DiffMark {
  /** The commit on the diff's right side, which was in the local stack. */
  commit: string;
  /** Marked stale: the commit has since been rewritten or rebased away. */
  stale: boolean;
}

export function markFragment(mark: DiffMark): string {
  return `stack=${mark.commit}${mark.stale ? "&stale" : ""}`;
}

export function readMark(fragment: string): DiffMark | undefined {
  const fields = new URLSearchParams(fragment);
  const commit = fields.get("stack");
  return commit ? { commit, stale: fields.has("stale") } : undefined;
}

const STALE = " — stale";

/** The title of a diff tab once its commit is gone: "b.txt (6827cbfe) — stale". */
export function staleTitle(title: string): string {
  return title.endsWith(STALE) ? title : `${title}${STALE}`;
}

export type Fate = "keep" | "mark" | "close";

/**
 * What to do with a diff tab now that the local stack holds `stack` (during a rebase, the rebasing
 * stack: what is replayed so far, the commit it stopped at, the todo as it was). A commit still in
 * it stays as it is. One that left it was rewritten (an amend, a rebase going on or ending, a
 * reorder) or rebased away: its tab is closed when `close` (`ger.closeDiffsOnRebase`), else marked
 * stale. A tab with unsaved edits is never closed (the edits would go): it is marked instead.
 */
export function fateOf(mark: DiffMark, dirty: boolean, stack: ReadonlySet<string>, close: boolean): Fate {
  if (!mark.stale && stack.has(mark.commit)) {
    return "keep";
  }
  if (close && !dirty) {
    return "close";
  }
  return mark.stale ? "keep" : "mark";
}

/** A file with edits that are only in the editor. */
export interface Unsaved {
  /** As the user knows it: its path in the repository. */
  name: string;
  /** Write it to disk; whether that worked. */
  save(): Thenable<boolean>;
}

export const SAVE_AND_CONTINUE = "Save All and Continue";

/**
 * Before `git rebase --continue`, which goes on with the files as they are on disk: edits only in
 * an editor (the diff of the commit you stand on has your file on its right) are asked about, never
 * saved unasked nor left behind. Whether the rebase may go on: nothing unsaved, or all of it saved.
 */
export async function saveBeforeContinue(ui: Ui, unsaved: readonly Unsaved[]): Promise<boolean> {
  if (!unsaved.length) {
    return true;
  }
  const names = unsaved.map((file) => file.name);
  const which = names.length === 1 ? names[0] : `${names.length} files`;
  const listed = names.length === 1 ? `${names[0]} has` : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} have`;
  const detail = `${listed} edits that are not saved. git rebase --continue goes on with the files as they are on disk: it would leave these edits out, and may then change the files under them.`;
  if ((await ui.message("warning", `Save ${which} before the rebase goes on?`, { modal: true, detail }, [SAVE_AND_CONTINUE])) !== SAVE_AND_CONTINUE) {
    return false;
  }
  const failed: string[] = [];
  for (const file of unsaved) {
    if (!(await file.save())) {
      failed.push(file.name);
    }
  }
  if (failed.length) {
    await ui.message("error", `Could not save ${failed.join(", ")}: the rebase did not go on.`, {}, []);
    return false;
  }
  return true;
}
