import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

import { GerApiError } from "../src/api";
import { ExecutableNotFound, ServeFailed } from "../src/server";
import { failureState, staleReason } from "../src/state";
import { checkoutRoot, chooseRepo, repoNote } from "../src/workspace";

function tree(): { root: string; repo: string; worktree: string; plain: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ger-ws-"));
  const repo = path.join(root, "repo");
  const worktree = path.join(root, "worktree");
  const plain = path.join(root, "plain");
  fs.mkdirSync(path.join(repo, ".git"), { recursive: true });
  fs.mkdirSync(path.join(repo, "src", "deep"), { recursive: true });
  fs.mkdirSync(worktree);
  fs.writeFileSync(path.join(worktree, ".git"), "gitdir: ../repo/.git/worktrees/worktree\n");
  fs.mkdirSync(plain);
  return { root, repo, worktree, plain };
}

test("a folder inside a checkout, or a worktree with a .git file, is served from the checkout's top", (t) => {
  const { root, repo, worktree, plain } = tree();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.equal(checkoutRoot(path.join(repo, "src", "deep")), repo);
  assert.equal(checkoutRoot(worktree), worktree);
  assert.ok(!checkoutRoot(plain)?.startsWith(root), "a folder with no .git above it in the tree is in no checkout");
});

test("with several folders, the first in a checkout is used, and the note says which", (t) => {
  const { root, repo, worktree } = tree();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const sub = path.join(repo, "src");
  const choice = chooseRepo([sub, worktree]);
  assert.deepEqual(choice, { repo, folder: sub, folders: 2 });
  assert.equal(repoNote(choice!), `Repository: ${repo} (found from ${sub}), the first of the 2 workspace folders in a git checkout`);
  assert.equal(chooseRepo([]), undefined);
  assert.equal(repoNote({ repo, folder: repo, folders: 1 }), undefined);
});

test("a failure to load leaves the state its reason calls for; the server classifies Gerrit and git", () => {
  assert.equal(failureState(new ExecutableNotFound("ger")), "no-executable");
  assert.equal(failureState(new GerApiError("CONFIG", "gerrit.webUrl is not set", 500)), "setup");
  assert.equal(failureState(new ServeFailed("not inside a git worktree", "GIT")), "no-repo");
  assert.equal(failureState(new GerApiError("GIT", "a git command failed", 500)), "failed");
  assert.equal(failureState(new ServeFailed("Traceback …")), "failed");
  assert.equal(failureState(new Error("socket hang up")), "failed");
});

test("an overview kept after a failed refresh says why; an unreachable Gerrit says so", () => {
  assert.equal(staleReason(new GerApiError("GERRIT", "HTTP 503 from gerrit.example", 502)), "Gerrit unreachable: HTTP 503 from gerrit.example");
  assert.equal(staleReason(new GerApiError("GIT", "a git command failed", 500)), "a git command failed");
  assert.equal(staleReason(new ServeFailed("ger serve exited")), "ger serve exited");
});
