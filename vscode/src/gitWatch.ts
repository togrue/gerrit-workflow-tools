// What to watch in a git directory so the views follow the repository, and how to calm a burst
// of changes into one refresh: one `GET /stack` for one state of the repository. No `vscode`
// import: unit-tested. The watcher itself is repoWatcher.ts.

import * as fs from "node:fs";
import * as path from "node:path";

import type { GitDirs } from "./workspace";

/** A rebase or a script updates many refs in a row; this long after the last change is quiet enough to look. */
export const QUIET_MS = 750;

export interface Watch {
  /** The directory the pattern is relative to. */
  base: string;
  pattern: string;
}

/**
 * The files that say what a local refresh would find different: where HEAD is, the branches, the
 * remote-tracking branches a fetch moves, and a rebase under way. Objects, the index and the logs change with every git command and say nothing more.
 */
export function watches({ gitDir, commonDir }: GitDirs): Watch[] {
  // ger-outcome.json: a step run in the terminal left how it ended (a refusal moves nothing else).
  const own = "HEAD,rebase-merge,rebase-merge/*,rebase-apply,rebase-apply/*,ger-outcome.json";
  // refs/remotes too: a fetch moves the upstream, and with it what is ahead, behind and pushed.
  const shared = "packed-refs,refs/heads,refs/heads/**,refs/remotes,refs/remotes/**";
  // Outside a worktree they are one directory: one watcher.
  return gitDir === commonDir
    ? [{ base: gitDir, pattern: `{${own},${shared}}` }]
    : [
        { base: gitDir, pattern: `{${own}}` },
        { base: commonDir, pattern: `{${shared}}` },
      ];
}

/**
 * The index, watched only while a rebase is stopped: `git add` of a resolved conflict changes
 * nothing else, and the conflict must not stay shown. At other times it changes with every git
 * command (and VS Code's own git rewrites it), so it is not watched.
 */
export function indexWatch({ gitDir }: GitDirs): Watch {
  return { base: gitDir, pattern: "index" };
}

/**
 * A saved file, or one changed outside the editor (a terminal, an agent): it changes what a stop or a
 * review says (a fix saved, not amended), and nothing in the git directory moves with it. Followed
 * only while a rebase is stopped or a review is staged here (`following`), and calmed into one
 * `GET /stack` a short moment after the last change: VS Code's own events (a save, its watcher of
 * the workspace, which runs anyway), no polling of git.
 */
export const TREE_QUIET_MS = 300;

/** Whether a change at `file` is one of the work tree's: under `repo`, not in a git directory. */
export function treeChange(file: string, repo: string, { gitDir, commonDir }: GitDirs): boolean {
  const within = (dir: string) => {
    const relative = path.relative(dir, file);
    return !relative.startsWith("..") && !path.isAbsolute(relative);
  };
  const relative = path.relative(repo, file);
  return Boolean(relative) && within(repo) && !relative.split(/[\\/]/).includes(".git") && !within(gitDir) && !within(commonDir);
}

/** The injectable clock of {@link Quiet}. */
export interface Timers {
  set(run: () => void, ms: number): unknown;
  clear(handle: unknown): void;
  now(): number;
}

const REAL: Timers = { set: (run, ms) => setTimeout(run, ms), clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>), now: () => Date.now() };

/**
 * Runs `run` once, `QUIET_MS` after the last `poke`; `run` is told when the newest change of the
 * burst was made: the file's own time when the poke knows it (git wrote HEAD then, however late the
 * event arrives), else when the poke came.
 */
export class Quiet {
  private handle: unknown;
  private last = 0;

  constructor(
    private readonly run: (lastChange: number) => void,
    private readonly timers: Timers = REAL,
    private readonly ms = QUIET_MS,
  ) {}

  poke(at: number = this.timers.now()): void {
    this.last = this.handle === undefined ? at : Math.max(this.last, at);
    this.timers.clear(this.handle);
    this.handle = this.timers.set(() => {
      this.handle = undefined;
      this.run(this.last);
    }, this.ms);
  }

  dispose(): void {
    this.timers.clear(this.handle);
    this.handle = undefined;
  }
}

/**
 * Where the repository stands, as far as `GET /stack` would see it, read without starting a
 * process: HEAD's text, and when git last wrote (and how big) the branch HEAD names, the index,
 * the rebase state, the step outcome, packed-refs and the config (an upstream set). Two equal
 * stamps: a read at one stands for the other, but for the work tree, which git does not write
 * down: {@link StackReads} is told of that apart. Unreadable files count as missing.
 */
export function repoStamp({ gitDir, commonDir }: GitDirs): string {
  const stat = (file: string) => {
    try {
      const { mtimeMs, ctimeMs, size } = fs.statSync(file);
      return `${mtimeMs}/${ctimeMs}/${size}`;
    } catch {
      return "-";
    }
  };
  let head = "";
  try {
    head = fs.readFileSync(path.join(gitDir, "HEAD"), "utf8").trim();
  } catch {
    // no HEAD to read: "-" below says as much
  }
  const branch = head.startsWith("ref: ") ? [path.join(commonDir, head.slice(5))] : [];
  const rebase = ["rebase-merge", "rebase-merge/done", "rebase-merge/msgnum", "rebase-merge/amend", "rebase-merge/stopped-sha", "rebase-apply", "rebase-apply/next"];
  const own = ["index", "ORIG_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD", "ger-outcome.json", ...rebase].map((file) => path.join(gitDir, file));
  const shared = ["packed-refs", "config"].map((file) => path.join(commonDir, file));
  return [head || "-", ...[...branch, ...own, ...shared].map(stat)].join(" ");
}

/** The stamp of the repository in `dirs`; without a git directory, one that never repeats (no two moments are known to be the same). */
export function stamper(dirs: GitDirs | undefined): () => string {
  let unknown = 0;
  return dirs ? () => repoStamp(dirs) : () => `unknown ${unknown++}`;
}

/** A read of the stack: the repository's stamp and the time when it began. */
export interface Moment {
  stamp: string;
  at: number;
}

/**
 * One `GET /stack` for one state of the repository. An ask while a read is under way joins it when
 * nothing changed since that read began (same stamp, no work tree change after it), else waits for
 * one more read after it, which every later ask joins. With `ifChanged` (an outside event that may
 * already have been seen: the end of a terminal command, the watcher) an ask is dropped when the
 * last read that came back still stands for now. A whole load, which carries the stack too, counts
 * as a read (`saw`).
 */
export class StackReads {
  private running: (Moment & { done: Promise<void> }) | undefined;
  private queued: Promise<void> | undefined;
  private last: Moment | undefined;
  private treeAt = -Infinity;

  constructor(
    private readonly read: () => Promise<void>,
    private readonly stamp: () => string,
    private readonly now: () => number = Date.now,
  ) {}

  /** The work tree changed at `at` (the file's own time, when known): a read begun before then does not stand for now. */
  touched(at: number = this.now()): void {
    this.treeAt = Math.max(this.treeAt, at);
  }

  /** Now, as a read begun now would stand for it. */
  moment(): Moment {
    return { stamp: this.stamp(), at: this.now() };
  }

  /** A read begun at `moment` came back. */
  saw(moment: Moment): void {
    if (!this.last || moment.at >= this.last.at) {
      this.last = moment;
    }
  }

  ask({ ifChanged = false }: { ifChanged?: boolean } = {}): Promise<void> {
    const stamp = this.stamp();
    const standsForNow = (read: Moment | undefined) => read !== undefined && read.stamp === stamp && this.treeAt < read.at;
    const running = this.running;
    if (running) {
      if (standsForNow(running)) {
        return running.done;
      }
      this.queued ??= running.done.then(() => {
        this.queued = undefined;
        return this.ask();
      });
      return this.queued;
    }
    return ifChanged && standsForNow(this.last) ? Promise.resolve() : this.start();
  }

  private start(): Promise<void> {
    const moment = this.moment();
    const done = this.read()
      .then(() => this.saw(moment))
      .catch(() => undefined)
      .finally(() => {
        this.running = undefined;
      });
    this.running = { ...moment, done };
    return done;
  }
}
