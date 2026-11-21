// Files as they are in a commit (`ger-file:` documents) and the extension's diff tabs of them: the
// one place that opens such a diff and knows it again among the tabs. When the local stack is
// rewritten, a tab of a commit that left it is marked stale in its title, or closed
// (`ger.closeDiffsOnRebase`); see staleDiffs.ts for the rule.

import * as path from "node:path";
import * as vscode from "vscode";

import { setting } from "./config";
import { Model } from "./model";
import { DiffMark, Unsaved, fateOf, markFragment, readMark, staleTitle } from "./staleDiffs";

export const FILE_SCHEME = "ger-file";

/** A file as it is in a commit: `ger-file:/<path>?<commit>`. */
export function fileUri(rev: string, file: string): vscode.Uri {
  return vscode.Uri.from({ scheme: FILE_SCHEME, path: `/${file}`, query: rev });
}

/**
 * Open the diff `left` ↔ `right` titled `title`. `stackCommit` is the commit on the right when it is
 * in the local stack: the tab then carries it (in the fragment of its left side, which the content
 * provider does not read), so it can be marked stale once that commit is rewritten.
 */
export async function openDiff(left: vscode.Uri, right: vscode.Uri, title: string, stackCommit: string | undefined): Promise<void> {
  const original = stackCommit ? left.with({ fragment: markFragment({ commit: stackCommit, stale: false }) }) : left;
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

/** Keeps the extension's diff tabs true to the local stack: on each new stack, and when the setting changes. */
export class DiffTabs implements vscode.Disposable {
  private readonly listeners: vscode.Disposable[];
  private running: Promise<void> = Promise.resolve();

  constructor(private readonly model: Model) {
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
    this.running = this.running.then(() => this.apply()).catch(() => undefined);
  }

  private async apply(): Promise<void> {
    const stack = this.model.stack;
    if (!stack) {
      return; // not known now (ger serve stopped, no upstream): nothing is said about the tabs
    }
    const shas = new Set(stack.commits.map((commit) => commit.sha));
    const close = setting("closeDiffsOnRebase", false);
    for (const tab of vscode.window.tabGroups.all.flatMap((group) => group.tabs)) {
      const diff = markedDiff(tab);
      const fate = diff && fateOf(diff.mark, tab.isDirty, shas, close);
      if (fate === "close") {
        await vscode.window.tabGroups.close(tab, true);
      } else if (fate === "mark" && diff) {
        await markStale(tab, diff.input, diff.mark);
      }
    }
  }

  dispose(): void {
    this.listeners.forEach((listener) => listener.dispose());
  }
}

/**
 * VS Code cannot retitle an open tab: the diff opens again, its left side marked stale (another URI,
 * so another tab), titled so, in its group and behind the active tab when it was (a preview tab is
 * replaced in place, any other opens beside the group's active tab), and the old tab closes.
 * Unsaved edits on the right are kept: they belong to the document, which the new tab shows too.
 */
async function markStale(tab: vscode.Tab, input: vscode.TabInputTextDiff, mark: DiffMark): Promise<void> {
  const original = input.original.with({ fragment: markFragment({ ...mark, stale: true }) });
  // `background` (passed on by VS Code, not in its typings): a tab behind another stays behind it.
  const options: vscode.TextDocumentShowOptions & { background: boolean } = { viewColumn: tab.group.viewColumn, preserveFocus: true, preview: tab.isPreview, background: !tab.isActive };
  await vscode.commands.executeCommand("vscode.diff", original, input.modified, staleTitle(tab.label), options);
  const old = tab.group.tabs.find((open) => open.input instanceof vscode.TabInputTextDiff && open.input.original.toString() === input.original.toString());
  if (old) {
    await vscode.window.tabGroups.close(old, true);
  }
}

/** Your files in `repo` with edits only in an editor: what `git rebase --continue` would not see. */
export function unsavedInRepo(repo: string): Unsaved[] {
  return vscode.workspace.textDocuments
    .filter((document) => document.isDirty && document.uri.scheme === "file")
    .map((document) => ({ document, relative: path.relative(repo, document.uri.fsPath) }))
    .filter(({ relative }) => relative && !relative.startsWith("..") && !path.isAbsolute(relative))
    .map(({ document, relative }) => ({ name: relative.split(path.sep).join("/"), save: () => document.save() }));
}
