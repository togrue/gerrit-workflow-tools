// What becomes of the extension's diff tabs when the local stack is rewritten, and saving what is
// not saved before a rebase goes on. Pure: diffTabs.ts reads the tabs and acts on these answers.

import type { Diff, Stack } from "./api";
import type { Ui } from "./ui";

/**
 * What a diff tab of the extension says about itself, in the fragment of its left side's
 * `ger-file` URI (`stack=<sha>&branch=<name>`, then `&own`, `&stale`). Only a diff of a local
 * commit that was in the local stack when it opened carries one: Gerrit's patchsets and other
 * people's changes never go stale.
 */
export interface DiffMark {
  /** The commit the diff's title names, which was in the local stack. */
  commit: string;
  /** The branch whose stack it was in (`stack.branch`, or `rebase.branch` during a rebase); null: a detached HEAD. */
  branch: string | null;
  /** The right side is your file in the worktree, not the commit's: it opened on the commit you stood on. */
  own: boolean;
  /** Marked stale: the right side no longer is the commit the title names. */
  stale: boolean;
}

export function markFragment(mark: DiffMark): string {
  const fields = new URLSearchParams({ stack: mark.commit });
  if (mark.branch !== null) {
    fields.set("branch", mark.branch);
  }
  return [fields.toString(), ...(mark.own ? ["own"] : []), ...(mark.stale ? ["stale"] : [])].join("&");
}

export function readMark(fragment: string): DiffMark | undefined {
  const fields = new URLSearchParams(fragment);
  const commit = fields.get("stack");
  return commit ? { commit, branch: fields.get("branch"), own: fields.has("own"), stale: fields.has("stale") } : undefined;
}

/** The branch a stack is of: the one checked out, or the one being rebased. */
export function stackBranch(stack: Stack): string | null {
  return stack.branch ?? stack.rebase?.branch ?? null;
}

/**
 * The mark of a diff about to open: only a diff whose right side is a local commit of the stack
 * (against its base, or Gerrit vs. local), not a patchset of Gerrit's, even one the stack has
 * the very commit of. `own`: its right side is your file.
 */
export function markOf(diff: Diff, stack: Stack | undefined, own: boolean): DiffMark | undefined {
  const local = diff.local_commit && typeof diff.since?.to_patch_set !== "number";
  if (!stack || !local || !stack.commits.some((commit) => commit.sha === diff.commit)) {
    return undefined;
  }
  return { commit: diff.commit, branch: stackBranch(stack), own, stale: false };
}

const STALE = " — stale";

/** The title of a diff tab once its commit is gone: "b.txt (6827cbfe) — stale". */
export function staleTitle(title: string): string {
  return title.endsWith(STALE) ? title : `${title}${STALE}`;
}

/** The title of a diff tab whose commit is back: "b.txt (6827cbfe)". */
export function liveTitle(title: string): string {
  return title.endsWith(STALE) ? title.slice(0, -STALE.length) : title;
}

/** What the tabs are judged against: the stack as `GET /stack` has it now. */
export interface StackNow {
  branch: string | null;
  /** HEAD. */
  head: string | null;
  /** The stack's commits (during a rebase, the rebasing stack: what is replayed so far, the commit it stopped at, the todo as it was). */
  commits: ReadonlySet<string>;
}

export function stackNow(stack: Stack): StackNow {
  return { branch: stackBranch(stack), head: stack.head, commits: new Set(stack.commits.map((commit) => commit.sha)) };
}

export type Fate = "keep" | "mark" | "unmark" | "close";

/**
 * What to do with a diff tab now that the stack is `now`. A tab of another branch's stack is not
 * judged: it stays as it is until that branch is back. A diff of the commit's own files is true
 * while the commit is in the stack (still to be replayed, or left as it was); one with your file on
 * its right, only while that commit is HEAD: after a commit on top, an amend, or a rebase that stops
 * below it, your file is another state than the title names. Untrue, its tab is closed when `close`
 * (`ger.closeDiffsOnRebase`), else marked stale; true again (an abort, the branch back), a tab
 * marked stale is unmarked. A tab with unsaved edits is never closed (the edits would go): it is
 * marked instead.
 */
export function fateOf(mark: DiffMark, dirty: boolean, now: StackNow, close: boolean): Fate {
  if (mark.branch !== now.branch) {
    return "keep";
  }
  const live = mark.own ? now.head === mark.commit : now.commits.has(mark.commit);
  if (live) {
    return mark.stale ? "unmark" : "keep";
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

export const SAVE_ALL = "Save All";

/**
 * Before `ger rebase --continue`, which goes on with the files as they are on disk: edits only in
 * an editor (the diff of the commit you stand on has your file on its right) are asked about, never
 * saved unasked nor left behind. Saved, the rebase is asked to go on, and `ger rebase --continue`
 * says what the saved edits still want (to be added, or amended in) before it can. Whether to run
 * it: nothing unsaved, or all of it saved.
 */
export async function saveBeforeContinue(ui: Ui, unsaved: readonly Unsaved[]): Promise<boolean> {
  if (!unsaved.length) {
    return true;
  }
  const names = unsaved.map((file) => file.name);
  const which = names.length === 1 ? names[0] : `${names.length} files`;
  const listed = names.length === 1 ? `${names[0]} has` : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} have`;
  const detail = `${listed} edits that are not saved. ger rebase --continue sees only the files on disk: saved, it says what the edits still need before the rebase can go on.`;
  if ((await ui.message("warning", `Save ${which} before the rebase goes on?`, { modal: true, detail }, [SAVE_ALL])) !== SAVE_ALL) {
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
