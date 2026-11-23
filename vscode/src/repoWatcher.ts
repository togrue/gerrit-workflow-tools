// The views follow the repository, as the built-in Git view does: a commit, a checkout, an
// `ger edit` and its `git rebase --continue` change the local stack, and Gerrit's event stream
// says nothing about local git. So watch HEAD, the branches and the rebase state, and refresh
// (locally: no Gerrit reads are forced) once things have been quiet for a moment.

import * as vscode from "vscode";

import { Quiet, indexWatch, watches } from "./gitWatch";
import { GitDirs } from "./workspace";

export class RepoWatcher implements vscode.Disposable {
  private readonly watchers: vscode.FileSystemWatcher[];
  private readonly quiet: Quiet;

  /** `changed` gets the time (ms) of the last change it is told about; the index counts while `rebasing()`. */
  constructor(dirs: GitDirs, changed: (lastChange: number) => void, rebasing: () => boolean = () => false) {
    this.quiet = new Quiet(changed);
    this.watchers = watches(dirs).map(({ base, pattern }) => {
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(base), pattern));
      for (const event of [watcher.onDidCreate, watcher.onDidChange, watcher.onDidDelete]) {
        event(() => this.quiet.poke());
      }
      return watcher;
    });
    const { base, pattern } = indexWatch(dirs);
    const index = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(base), pattern));
    for (const event of [index.onDidCreate, index.onDidChange, index.onDidDelete]) {
      event(() => rebasing() && this.quiet.poke());
    }
    this.watchers.push(index);
  }

  dispose(): void {
    this.quiet.dispose();
    this.watchers.forEach((watcher) => watcher.dispose());
  }
}
