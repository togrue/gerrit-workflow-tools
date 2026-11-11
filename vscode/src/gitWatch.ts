// What to watch in a git directory so the views follow the repository, and how to calm a burst
// of changes into one refresh. No `vscode` import: unit-tested. The watcher itself is repoWatcher.ts.

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
  const own = "HEAD,rebase-merge,rebase-merge/*,rebase-apply,rebase-apply/*";
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

/** The injectable clock of {@link Quiet}. */
export interface Timers {
  set(run: () => void, ms: number): unknown;
  clear(handle: unknown): void;
  now(): number;
}

const REAL: Timers = { set: (run, ms) => setTimeout(run, ms), clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>), now: () => Date.now() };

/** Runs `run` once, `QUIET_MS` after the last `poke`; `run` is told when that last change was. */
export class Quiet {
  private handle: unknown;
  private last = 0;

  constructor(
    private readonly run: (lastChange: number) => void,
    private readonly timers: Timers = REAL,
    private readonly ms = QUIET_MS,
  ) {}

  poke(): void {
    this.last = this.timers.now();
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
