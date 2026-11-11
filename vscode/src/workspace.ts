// Which repository the extension serves: the first workspace folder that sits in a git checkout.
// No `vscode` import: unit-tested.

import * as fs from "node:fs";
import * as path from "node:path";

/** The top of the git checkout `dir` is in, where `.git` is (a folder, or a worktree's file); undefined outside one. */
export function checkoutRoot(dir: string): string | undefined {
  let current = path.resolve(dir);
  for (;;) {
    if (fs.existsSync(path.join(current, ".git"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

export interface GitDirs {
  /** This checkout's own git directory: `HEAD` and the rebase state. In a worktree, under the main one's `worktrees/`. */
  gitDir: string;
  /** The directory shared by all worktrees: `refs/` and `packed-refs`. The same as `gitDir` outside a worktree. */
  commonDir: string;
}

/** Where git keeps the state of the checkout at `root` (see {@link checkoutRoot}); undefined when `.git` cannot be read. */
export function gitDirs(root: string): GitDirs | undefined {
  try {
    const dotGit = path.join(root, ".git");
    // A worktree's `.git` is a file: "gitdir: <path to its own directory>".
    const pointer = fs.statSync(dotGit).isFile() ? /^gitdir:\s*(.+)$/m.exec(fs.readFileSync(dotGit, "utf8"))?.[1] : undefined;
    const gitDir = pointer ? path.resolve(root, pointer.trim()) : dotGit;
    const common = path.join(gitDir, "commondir"); // a worktree's directory names the shared one, relative to itself
    const commonDir = fs.existsSync(common) ? path.resolve(gitDir, fs.readFileSync(common, "utf8").trim()) : gitDir;
    return { gitDir, commonDir };
  } catch {
    return undefined;
  }
}

export interface RepoChoice {
  /** The checkout's top: where `ger serve` runs, and what file paths are relative to. */
  repo: string;
  /** The workspace folder it was found from. */
  folder: string;
  /** How many folders the window has: with more than one, say which is used. */
  folders: number;
}

/** The first of `folders` in a git checkout; undefined when none is. */
export function chooseRepo(folders: string[]): RepoChoice | undefined {
  for (const folder of folders) {
    const repo = checkoutRoot(folder);
    if (repo) {
      return { repo, folder, folders: folders.length };
    }
  }
  return undefined;
}

/** One line on which repository is used, when the window has several folders to choose from. */
export function repoNote(choice: RepoChoice): string | undefined {
  if (choice.folders < 2) {
    return undefined;
  }
  const from = path.resolve(choice.folder) === path.resolve(choice.repo) ? "" : ` (found from ${choice.folder})`;
  return `Repository: ${choice.repo}${from}, the first of the ${choice.folders} workspace folders in a git checkout`;
}
