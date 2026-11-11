import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

import { Quiet, Timers, watches } from "../src/gitWatch";
import { gitDirs } from "../src/workspace";

test("a plain checkout is watched in one place: HEAD, branches, the rebase state", () => {
  const [only, ...rest] = watches({ gitDir: "/r/.git", commonDir: "/r/.git" });
  assert.equal(rest.length, 0);
  assert.equal(only.base, "/r/.git");
  for (const wanted of ["HEAD", "packed-refs", "refs/heads/**", "refs/remotes/**", "rebase-merge/*", "rebase-apply/*"]) {
    assert.ok(only.pattern.includes(wanted), wanted);
  }
  assert.ok(!only.pattern.includes("objects") && !only.pattern.includes("index"));
});

test("a worktree has its HEAD and rebase state in its own directory, its branches in the shared one", () => {
  const [own, shared] = watches({ gitDir: "/r/.git/worktrees/w", commonDir: "/r/.git" });
  assert.equal(own.base, "/r/.git/worktrees/w");
  assert.ok(own.pattern.includes("HEAD") && own.pattern.includes("rebase-merge") && !own.pattern.includes("refs"));
  assert.equal(shared.base, "/r/.git");
  assert.ok(shared.pattern.includes("refs/heads/**") && shared.pattern.includes("packed-refs") && !shared.pattern.includes("HEAD"));
});

test("git directories are found for a plain checkout and for a worktree whose .git is a file", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ger-git-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const main = path.join(root, "main");
  const own = path.join(main, ".git", "worktrees", "w");
  const worktree = path.join(root, "w");
  fs.mkdirSync(own, { recursive: true });
  fs.mkdirSync(worktree);
  fs.writeFileSync(path.join(own, "commondir"), "../..\n");
  fs.writeFileSync(path.join(worktree, ".git"), `gitdir: ${own}\n`);
  assert.deepEqual(gitDirs(main), { gitDir: path.join(main, ".git"), commonDir: path.join(main, ".git") });
  assert.deepEqual(gitDirs(worktree), { gitDir: own, commonDir: path.join(main, ".git") });
  assert.equal(gitDirs(path.join(root, "nowhere")), undefined);
});

/** A clock the test turns by hand. */
function clock() {
  let now = 0;
  const pending = new Map<number, { at: number; run: () => void }>();
  let next = 0;
  const timers: Timers = {
    set: (run, ms) => pending.set(++next, { at: now + ms, run }) && next,
    clear: (handle) => void pending.delete(handle as number),
    now: () => now,
  };
  const advance = (ms: number) => {
    now += ms;
    for (const [id, timer] of [...pending]) {
      if (timer.at <= now) {
        pending.delete(id);
        timer.run();
      }
    }
  };
  return { timers, advance };
}

test("a burst of changes (a long rebase) is one run, once things were quiet, told when the last change was", () => {
  const { timers, advance } = clock();
  const runs: number[] = [];
  const quiet = new Quiet((last) => runs.push(last), timers, 750);
  for (let i = 0; i < 20; i++) {
    quiet.poke();
    advance(200); // a ref update every 200 ms: never quiet for 750 ms
  }
  assert.deepEqual(runs, []);
  advance(750);
  assert.deepEqual(runs, [3800]);
  advance(5000);
  assert.equal(runs.length, 1, "nothing more without another change");
});

test("a change after the quiet makes another run, and disposing cancels a pending one", () => {
  const { timers, advance } = clock();
  const runs: number[] = [];
  const quiet = new Quiet((last) => runs.push(last), timers, 750);
  quiet.poke();
  advance(750);
  quiet.poke();
  advance(750);
  assert.equal(runs.length, 2);
  quiet.poke();
  quiet.dispose();
  advance(5000);
  assert.equal(runs.length, 2);
});
