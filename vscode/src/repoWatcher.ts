// The views follow the repository, as the built-in Git view does: a commit, a checkout, an
// `ger edit` and its `git rebase --continue` change the local stack, and Gerrit's event stream
// says nothing about local git. So watch HEAD, the branches and the rebase state, and refresh
// (locally: no Gerrit reads are forced) once things have been quiet for a moment.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { Quiet, TREE_QUIET_MS, indexWatch, treeChange, watches } from "./gitWatch";
import { GitDirs } from "./workspace";

/**
 * When the file was written: the later of its modification and status change times (a file moved
 * in, or copied with its old time kept, has a new status time); for one deleted, its directory's (a
 * branch deleted takes its entry out of `refs/heads`). A refresh the extension started right after
 * its own checkout already saw it, though the event arrives later. Unknown: now.
 */
function changedAt(uri: vscode.Uri, deleted = false): number | undefined {
  try {
    const { mtimeMs, ctimeMs } = fs.statSync(deleted ? path.dirname(uri.fsPath) : uri.fsPath);
    return Math.max(mtimeMs, ctimeMs);
  } catch {
    return undefined;
  }
}

/** What the watcher tells: git moved (`changed`), or the work tree did (`touched`; `treeChanged` while `following()`). */
export interface RepoChanges {
  changed(lastChange: number): void;
  /** The index (while rebasing), a saved file or one changed elsewhere: what a stop or a review says may differ now. */
  treeChanged(lastChange: number): void;
  /** A file of the work tree changed at `at` (its own time), followed or not: a read of the stack begun before then is old. */
  touched(at: number | undefined): void;
  /** A rebase is stopped, or a review is staged here: only then is the work tree followed. */
  following(): boolean;
}

export class RepoWatcher implements vscode.Disposable {
  private readonly watchers: vscode.FileSystemWatcher[];
  private readonly quiet: Quiet;
  private readonly tree: Quiet;
  private readonly saves: vscode.Disposable;

  constructor(dirs: GitDirs, repo: string, { changed, treeChanged, touched, following }: RepoChanges) {
    this.quiet = new Quiet(changed);
    this.tree = new Quiet(treeChanged, undefined, TREE_QUIET_MS);
    this.watchers = watches(dirs).map(({ base, pattern }) => {
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(base), pattern));
      for (const event of [watcher.onDidCreate, watcher.onDidChange]) {
        event((uri) => this.quiet.poke(changedAt(uri)));
      }
      watcher.onDidDelete((uri) => this.quiet.poke(changedAt(uri, true)));
      return watcher;
    });
    const { base, pattern } = indexWatch(dirs);
    const index = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(base), pattern));
    // The work tree: VS Code watches the workspace anyway, so this adds no watcher of the system's.
    const files = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(repo), "**/*"));
    // Each told with when the file was written: a read that began after it already saw it (the end of
    // the terminal command that wrote it reads the stack at once; the watcher's event comes later).
    const poke = (uri: vscode.Uri, deleted = false) => {
      if (uri.scheme !== "file" || !treeChange(uri.fsPath, repo, dirs)) {
        return;
      }
      const at = changedAt(uri, deleted);
      touched(at);
      if (following()) {
        this.tree.poke(at);
      }
    };
    for (const event of [index.onDidCreate, index.onDidChange, index.onDidDelete]) {
      event((uri) => following() && this.tree.poke(changedAt(uri)));
    }
    files.onDidCreate((uri) => poke(uri));
    files.onDidChange((uri) => poke(uri));
    files.onDidDelete((uri) => poke(uri, true));
    // A save is told at once, ahead of the watcher's event of the same write: the same burst, one request.
    this.saves = vscode.workspace.onDidSaveTextDocument((document) => poke(document.uri));
    this.watchers.push(index, files);
  }

  dispose(): void {
    this.quiet.dispose();
    this.tree.dispose();
    this.saves.dispose();
    this.watchers.forEach((watcher) => watcher.dispose());
  }
}
