// Files as they are in a commit (`ger-file:` documents) and the extension's diff tabs of them: the
// one place that opens such a diff and knows it again among the tabs. When the local stack is
// rewritten, a tab whose right side is no longer the commit its title names is marked stale in its
// title, or closed (`ger.closeDiffsOnRebase`), and unmarked once it is again; see staleDiffs.ts for
// the rule. Also your files of the repository: their paths, and which have unsaved edits.

import * as path from "node:path";
import * as vscode from "vscode";

import { setting } from "./config";
import { Model } from "./model";
import { DiffMark, Unsaved, fateOf, liveTitle, markFragment, readMark, stackNow, staleTitle } from "./staleDiffs";

export const FILE_SCHEME = "ger-file";

/** A file as it is in a commit: `ger-file:/<path>?<commit>`. */
export function fileUri(rev: string, file: string): vscode.Uri {
  return vscode.Uri.from({ scheme: FILE_SCHEME, path: `/${file}`, query: rev });
}

/**
 * The repository path of your own file `uri` in `repo` ("src/e.txt"); undefined for anything else
 * (another scheme, a file outside the repository).
 */
export function repoPath(repo: string, uri: vscode.Uri): string | undefined {
  if (uri.scheme !== "file") {
    return undefined;
  }
  const relative = path.relative(repo, uri.fsPath);
  return relative && !relative.startsWith("..") && !path.isAbsolute(relative) ? relative.split(path.sep).join("/") : undefined;
}

/**
 * Open the diff `left` ↔ `right` titled `title`. `mark` (see staleDiffs.ts `markOf`) is carried in
 * the fragment of its left side, which the content provider does not read, so the tab can be marked
 * stale once its right side is no longer the commit the title names.
 */
export async function openDiff(left: vscode.Uri, right: vscode.Uri, title: string, mark: DiffMark | undefined): Promise<void> {
  const original = mark ? left.with({ fragment: markFragment(mark) }) : left;
  await vscode.commands.executeCommand("vscode.diff", original, right, title);
}

/** A diff tab the extension opened of a stack commit, with what its left side says about it. */
function markedDiff(tab: vscode.Tab): { input: vscode.TabInputTextDiff; mark: DiffMark } | undefined {
  const input = tab.input;
  if (!(input instanceof vscode.TabInputTextDiff) || input.original.scheme !== FILE_SCHEME) {
    return undefined;
  }
  const mark = readMark(input.original.fragment);
  return mark ? { input, mark } : undefined;
}

/** The tab open now with the diff `input`, in any group; undefined once it is closed. */
function openTab(input: vscode.TabInputTextDiff): vscode.Tab | undefined {
  const original = input.original.toString();
  const modified = input.modified.toString();
  return vscode.window.tabGroups.all
    .flatMap((group) => group.tabs)
    .find((tab) => tab.input instanceof vscode.TabInputTextDiff && tab.input.original.toString() === original && tab.input.modified.toString() === modified);
}

/** Whether the document `uri` is loaded, so that its `isDirty` is known. */
function loaded(uri: vscode.Uri): boolean {
  const name = uri.toString();
  return vscode.workspace.textDocuments.some((document) => document.uri.toString() === name);
}

/** Keeps the extension's diff tabs true to the local stack: on each new stack, and when the setting changes. */
export class DiffTabs implements vscode.Disposable {
  private readonly listeners: vscode.Disposable[];
  private running: Promise<void> = Promise.resolve();
  /** No stack has been judged yet: the tabs are as VS Code restored them. */
  private first = true;

  constructor(
    private readonly model: Model,
    private readonly log: (line: string) => void,
  ) {
    this.listeners = [
      model.onDidChange(() => this.sweep()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("ger.closeDiffsOnRebase")) {
          this.sweep();
        }
      }),
    ];
  }

  /** Resolves once the tabs are as the last stack has them (for the end-to-end suite). */
  idle(): Promise<void> {
    return this.running;
  }

  private sweep(): void {
    this.running = this.running.then(() => this.apply()).catch((error: unknown) => this.log(`Diff tabs: ${String(error)}`));
  }

  /**
   * Each tab on its own: one that cannot be closed or reopened is logged, and the others are still
   * seen to. A tab closed meanwhile (by you, while an earlier one was reopened) is left closed.
   */
  private async apply(): Promise<void> {
    const stack = this.model.stack;
    if (!stack) {
      return; // not known now (ger serve stopped, no upstream): nothing is said about the tabs
    }
    const now = stackNow(stack);
    const close = setting("closeDiffsOnRebase", false);
    const first = this.first;
    this.first = false;
    for (const seen of vscode.window.tabGroups.all.flatMap((group) => group.tabs)) {
      const diff = markedDiff(seen);
      const tab = diff && openTab(diff.input);
      if (!diff || !tab) {
        continue;
      }
      const fate = fateOf(diff.mark, { dirty: tab.isDirty, loaded: loaded(diff.input.modified), first }, now, close);
      if (fate === "keep") {
        continue;
      }
      try {
        if (fate === "close") {
          await vscode.window.tabGroups.close(tab, true);
        } else {
          await reopen(tab, diff.input, { ...diff.mark, stale: fate === "mark" });
        }
      } catch (error) {
        this.log(`Diff tabs: could not ${fate === "close" ? "close" : fate} the tab "${tab.label}": ${String(error)}`);
      }
    }
  }

  dispose(): void {
    this.listeners.forEach((listener) => listener.dispose());
  }
}

/**
 * VS Code cannot retitle an open tab: the diff opens again, its left side marked as `mark` says
 * (another URI, so another tab), titled stale or not, in its group and behind the active tab when it
 * was (a preview tab is replaced in place, any other opens beside the group's active tab), and the old
 * tab closes. Unsaved edits on the right are kept: they belong to the document, which the new tab
 * shows too.
 */
async function reopen(tab: vscode.Tab, input: vscode.TabInputTextDiff, mark: DiffMark): Promise<void> {
  const original = input.original.with({ fragment: markFragment(mark) });
  const title = mark.stale ? staleTitle(tab.label) : liveTitle(tab.label);
  // `background` (passed on by VS Code, not in its typings): a tab behind another stays behind it.
  const options: vscode.TextDocumentShowOptions & { background: boolean } = { viewColumn: tab.group.viewColumn, preserveFocus: true, preview: tab.isPreview, background: !tab.isActive };
  await vscode.commands.executeCommand("vscode.diff", original, input.modified, title, options);
  const old = openTab(input);
  if (old) {
    await vscode.window.tabGroups.close(old, true);
  }
}

/**
 * Your files in `repo` with edits only in an editor: what `ger rebase --continue` would not see.
 * Every one is named, also one git does not track: asking once too often loses nothing, and the
 * extension does not ask git itself.
 */
export function unsavedInRepo(repo: string): Unsaved[] {
  return vscode.workspace.textDocuments.flatMap((document) => {
    const name = document.isDirty ? repoPath(repo, document.uri) : undefined;
    return name === undefined ? [] : [{ name, save: () => document.save() }];
  });
}
