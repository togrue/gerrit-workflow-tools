import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

import { Quiet, StackReads, TREE_QUIET_MS, Timers, indexWatch, repoStamp, stamper, treeChange, watches } from "../src/gitWatch";
import { gitDirs } from "../src/workspace";

test("a plain checkout is watched in one place: HEAD, branches, the rebase state", () => {
  const [only, ...rest] = watches({ gitDir: "/r/.git", commonDir: "/r/.git" });
  assert.equal(rest.length, 0);
  assert.equal(only.base, "/r/.git");
  for (const wanted of ["HEAD", "packed-refs", "refs/heads/**", "refs/remotes/**", "rebase-merge/*", "rebase-apply/*", "ger-outcome.json"]) {
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

test("a change is dated by the file git wrote, not by when its event arrived: an own refresh after it already saw it", () => {
  const { timers, advance } = clock();
  const runs: number[] = [];
  const quiet = new Quiet((last) => runs.push(last), timers, 750);
  advance(1000);
  // HEAD written at 400 (the checkout the extension asked for), its event at 1000; a ref at 600.
  quiet.poke(400);
  quiet.poke(600);
  advance(750);
  assert.deepEqual(runs, [600], "the newest of the burst, by the files' own times");
  quiet.poke(); // unknown (gone again): now
  advance(750);
  assert.deepEqual(runs, [600, 1750]);
});

test("the index is its own watch, in the worktree's own git directory, for a rebase to follow git add", () => {
  assert.deepEqual(indexWatch({ gitDir: "/r/.git/worktrees/w", commonDir: "/r/.git" }), { base: "/r/.git/worktrees/w", pattern: "index" });
});

test("states 16 and 28: a file saved or changed in the work tree is a change to follow; the git directories are not", () => {
  const dirs = { gitDir: "/r/.git", commonDir: "/r/.git" };
  assert.ok(treeChange("/r/calc/ops.py", "/r", dirs));
  assert.ok(treeChange("/r/README.md", "/r", dirs));
  for (const other of ["/r/.git/index", "/r/.git/HEAD", "/r/sub/.git/index", "/other/file.py", "/r"]) {
    assert.ok(!treeChange(other, "/r", dirs), other);
  }
  // A worktree's git directories live outside it, in the main checkout.
  assert.ok(!treeChange("/main/.git/worktrees/w/index", "/main", { gitDir: "/main/.git/worktrees/w", commonDir: "/main/.git" }));
  assert.ok(TREE_QUIET_MS < 1000, "within a moment of the save");
});

/** Reads of the stack the test answers by hand, against a repository stamp and a clock it sets. */
function reads() {
  const world = { stamp: "a", now: 0 };
  const pending: { resolve: () => void; reject: (error: Error) => void }[] = [];
  let count = 0;
  const stack = new StackReads(
    () => {
      count++;
      return new Promise<void>((resolve, reject) => pending.push({ resolve, reject }));
    },
    () => world.stamp,
    () => world.now,
  );
  const answer = async (fail = false) => {
    const next = pending.shift();
    assert.ok(next, "a read was under way");
    if (fail) {
      next.reject(new Error("ger serve went away"));
    } else {
      next.resolve();
    }
    await new Promise((resolve) => setImmediate(resolve));
  };
  return { stack, world, answer, reads: () => count };
}

test("one read for one state: an ask while the same state is being read joins that read", async () => {
  const { stack, world, answer, reads: count } = reads();
  // The terminal's end, then the watcher's poke of the index the same command wrote: one GET /stack.
  const first = stack.ask();
  world.now = 300;
  const second = stack.ask({ ifChanged: true });
  const third = stack.ask(); // a refused step reading again, nothing changed
  assert.equal(count(), 1);
  await answer();
  await Promise.all([first, second, third]);
  assert.equal(count(), 1);
});

test("an ask after a change while a read is under way waits for one more read, which every later ask joins", async () => {
  const { stack, world, answer, reads: count } = reads();
  const first = stack.ask();
  world.stamp = "b"; // HEAD moved (an amend) while ger serve was reading
  world.now = 100;
  const later = [stack.ask(), stack.ask({ ifChanged: true }), stack.ask()];
  assert.equal(count(), 1, "not two reads at once");
  await answer();
  await first;
  assert.equal(count(), 2, "the state since then is read once");
  await answer();
  await Promise.all(later);
  assert.equal(count(), 2);
});

test("an outside event is dropped when the last read still stands; a step's own ask is not", async () => {
  const { stack, world, answer, reads: count } = reads();
  const read = stack.ask();
  await answer();
  await read;
  world.now = 2000;
  await stack.ask({ ifChanged: true }); // the watcher, after the terminal's end already read this state
  assert.equal(count(), 1);
  const own = stack.ask(); // a refusal: core looked afresh, the views read again
  assert.equal(count(), 2);
  await answer();
  await own;
  world.stamp = "c"; // the step outcome written: the state is new
  const changed = stack.ask({ ifChanged: true });
  assert.equal(count(), 3);
  await answer();
  await changed;
});

test("a save after a read began makes the next ask read again; a file written before it began does not", async () => {
  const { stack, world, answer, reads: count } = reads();
  world.now = 1000;
  const read = stack.ask();
  await answer();
  await read;
  stack.touched(900); // the checkout wrote it before the read began; its event came after
  await stack.ask({ ifChanged: true });
  assert.equal(count(), 1, "already seen");
  stack.touched(1500); // a fix saved at the stop: git writes nothing, the stack's files differ
  const saved = stack.ask({ ifChanged: true });
  assert.equal(count(), 2, "the save shows");
  await answer();
  await saved;
  world.now = 3000;
  stack.touched(); // the time unknown: now
  const unknown = stack.ask({ ifChanged: true });
  assert.equal(count(), 3);
  await answer();
  await unknown;
});

test("a whole load counts as a read of its moment; a read that failed counts for nothing", async () => {
  const { stack, world, answer, reads: count } = reads();
  stack.saw(stack.moment()); // an overview came back: it carries the stack
  world.now = 500;
  await stack.ask({ ifChanged: true });
  assert.equal(count(), 0);
  world.stamp = "b";
  const failed = stack.ask({ ifChanged: true });
  await answer(true);
  await failed; // the caller is not left with a rejection
  const again = stack.ask({ ifChanged: true });
  assert.equal(count(), 2, "the state the failed read was for is asked again");
  await answer();
  await again;
});

test("the repository's stamp moves with HEAD, the index, the rebase and the step outcome, and with nothing else", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ger-stamp-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const git = path.join(root, ".git");
  fs.mkdirSync(path.join(git, "refs", "heads"), { recursive: true });
  const dirs = { gitDir: git, commonDir: git };
  const write = (file: string, text: string, at: number) => {
    fs.mkdirSync(path.dirname(path.join(git, file)), { recursive: true });
    fs.writeFileSync(path.join(git, file), text);
    fs.utimesSync(path.join(git, file), at, at);
  };
  write("HEAD", "ref: refs/heads/main\n", 1000);
  write("refs/heads/main", "1".repeat(40), 1000);
  write("index", "index", 1000);
  const before = repoStamp(dirs);
  fs.writeFileSync(path.join(root, "saved.py"), "work tree: told apart");
  fs.mkdirSync(path.join(git, "objects", "ab"), { recursive: true });
  fs.writeFileSync(path.join(git, "objects", "ab", "cd"), "an object: says nothing");
  assert.equal(repoStamp(dirs), before);
  const moves: [string, () => void][] = [
    ["the branch moved", () => write("refs/heads/main", "2".repeat(40), 2000)],
    ["HEAD detached", () => write("HEAD", `${"2".repeat(40)}\n`, 2000)],
    ["git add", () => write("index", "index2", 3000)],
    ["a rebase stopped", () => write("rebase-merge/msgnum", "1", 3000)],
    ["it went on", () => write("rebase-merge/msgnum", "2", 4000)],
    ["a step ended", () => write("ger-outcome.json", "{}", 4000)],
    ["an upstream set", () => write("config", "[branch]", 4000)],
  ];
  let last = before;
  for (const [what, move] of moves) {
    move();
    const now = repoStamp(dirs);
    assert.notEqual(now, last, what);
    last = now;
  }
  const unknown = stamper(undefined);
  assert.notEqual(unknown(), unknown(), "without a git directory no two moments are the same");
});
