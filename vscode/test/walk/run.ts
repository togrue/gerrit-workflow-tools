// `npm run walk`: the review loop and the rework loop in a real VS Code, against a real Gerrit,
// with tracing on; then the scorecard of the run. See test/walk/README.md.
//
// GER_WALK_REPO   the checkout to copy (default ~/ger-playground/repo); never used in place
// GER_WALK_OUT    where the trace, the pictures and the scorecard go (default out/walk)
// GER_WALK_REWORK the change whose chain the rework loop works on, checked out first (default 2178)
// GER_WALK_REVIEW the chain to review (default: the first of To review)
// GER_WALK_PROCESS_MS what one subprocess costs on Windows, for the scorecard (default 80)
// GER_WALK_ONLY   review or rework: walk that loop only
// GER_WALK_RESET  0: leave on Gerrit what the walk did (its vote, its drafts); else it is undone
// VSCODE_EXE      the VS Code to run (default the one `npm run e2e -- --get-vscode` downloads)

import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { draftIds, resetGerrit } from "./gerritReset";
import { readTrace, renderScorecard, scorecard } from "./scorecard";

const extensionDir = path.resolve(__dirname, "..", "..", "..");
const projectDir = path.resolve(extensionDir, "..");
const code = process.env.VSCODE_EXE || path.join(os.homedir(), ".cache", "ger-vscode-test", "VSCode-linux-x64", "code");
const source = path.resolve(process.env.GER_WALK_REPO || path.join(os.homedir(), "ger-playground", "repo"));
const out = path.resolve(process.env.GER_WALK_OUT || path.join(extensionDir, "out", "walk"));
const processMs = Number(process.env.GER_WALK_PROCESS_MS || 80);
const work = path.join(out, "work");
const repo = path.join(work, "repo");
const trace = path.join(out, "trace.jsonl");
const shots = path.join(out, "steps");

function sh(command: string, args: string[], cwd: string, env: NodeJS.ProcessEnv = process.env): string {
  const ran = spawnSync(command, args, { cwd, env, encoding: "utf8" });
  if (ran.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed in ${cwd}:\n${ran.stdout}${ran.stderr}`);
  }
  return ran.stdout;
}

async function main(): Promise<void> {
  if (!fs.existsSync(code)) {
    throw new Error(`no VS Code at ${code}: set VSCODE_EXE, or run npm run e2e -- --get-vscode`);
  }
  fs.rmSync(work, { recursive: true, force: true });
  fs.rmSync(shots, { recursive: true, force: true });
  fs.rmSync(trace, { force: true });
  fs.mkdirSync(shots, { recursive: true });
  // A copy: the walk checks out, rebases and amends there, never in the checkout it was given.
  fs.cpSync(source, repo, { recursive: true, verbatimSymlinks: true });
  fs.rmSync(path.join(repo, ".git", "worktrees"), { recursive: true, force: true });
  const profile = path.join(work, "profile");
  fs.mkdirSync(path.join(profile, "User"), { recursive: true });
  const gitconfig = path.join(work, "gitconfig");
  fs.writeFileSync(gitconfig, "[user]\n\tname = Walk\n\temail = walk@example.invalid\n");
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    GIT_CONFIG_GLOBAL: gitconfig,
    GIT_CONFIG_NOSYSTEM: "1",
    XDG_CACHE_HOME: path.join(work, "cache"),
    XDG_CONFIG_HOME: path.join(work, "config"),
    GER_TRACE: trace,
    GER_WALK_SHOTS: shots,
    GER_WALK_DEVTOOLS: path.join(profile, "DevToolsActivePort"),
    GER_WALK_PROJECT: projectDir,
  };
  // As a user's: no editor that answers for them (an agent's shell sets these).
  delete env.GIT_EDITOR;
  delete env.GIT_SEQUENCE_EDITOR;
  delete env.ELECTRON_RUN_AS_NODE;
  const rework = process.env.GER_WALK_REWORK || "2178";
  const ger = (...args: string[]) => sh("uv", ["run", "--project", projectDir, "ger", ...args], repo, { ...env, GER_TRACE: "" });
  ger("checkout", rework, "-b", "walk-rework", "-y", "--force");
  // The review in this window, on the branch: a review worktree opens in a window of its own, which the walk cannot follow.
  sh("git", ["config", "ger.reviewCheckout", "branch"], repo);
  fs.writeFileSync(
    path.join(profile, "User", "settings.json"),
    JSON.stringify({
      "ger.executable": `uv run --project "${projectDir}" ger`,
      "ger.pollSeconds": 3600,
      "ger.notify": false,
      "security.workspace.trust.enabled": false,
      "workbench.startupEditor": "none",
      "chat.disableAIFeatures": true,
      "python.analysis.typeCheckingMode": "off",
      "terminal.integrated.enablePersistentSessions": false,
      "git.enabled": false,
    }),
  );
  const started = new Date(Date.now() - 5000);
  const drafts = process.env.GER_WALK_RESET === "0" ? new Set<string>() : await draftIds(repo);
  const ran = spawnSync(
    code,
    [
      repo,
      `--extensionDevelopmentPath=${extensionDir}`,
      `--extensionTestsPath=${path.join(__dirname, "suite.js")}`,
      `--user-data-dir=${profile}`,
      `--extensions-dir=${path.join(work, "extensions")}`,
      "--disable-workspace-trust",
      "--skip-welcome",
      "--skip-release-notes",
      "--disable-updates",
      ...(process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY ? ["--ozone-platform=headless"] : []),
      "--remote-debugging-port=0",
    ],
    { env, stdio: "inherit", timeout: 1_200_000 },
  );
  console.log(`walk: VS Code exited ${ran.status ?? ran.signal}`);
  if (!fs.existsSync(trace)) {
    throw new Error(`walk: no trace at ${trace}`);
  }
  const records = readTrace(fs.readFileSync(trace, "utf8"));
  if (process.env.GER_WALK_RESET !== "0") {
    const touched = records.filter((r) => r.kind === "touched").flatMap((r) => r.changes as number[]);
    await resetGerrit(repo, started, drafts, [...new Set(touched)], (line) => console.log(`walk: ${line}`));
  }
  const card = scorecard(records, { processMs });
  fs.writeFileSync(path.join(out, "scorecard.json"), `${JSON.stringify(card, null, 2)}\n`);
  fs.writeFileSync(path.join(out, "scorecard.md"), renderScorecard(card));
  console.log(`walk: ${path.join(out, "scorecard.md")}`);
  process.exitCode = ran.status === 0 ? 0 : 1;
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
