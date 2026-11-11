// `npm run e2e`: start a real VS Code on a fixture repository, with the extension under
// development and a fake Gerrit behind `ger serve`, and run suite.ts inside it.
//
// Uses its own profile and extensions directory, so your VS Code is not touched. Needs `uv`
// (to run fake_ger.py with the project's Python) and a VS Code executable: VSCODE_EXE, or the
// usual install locations.
//
// The suite writes a transcript: for every button it presses, what a user expects, what the
// extension said and did, and how the views changed. It must match transcript.golden.md; on a
// difference the run fails and prints the diff. `npm run e2e -- --update` accepts the new one.

import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const extensionDir = path.resolve(__dirname, "..", "..", "..");
const projectDir = path.resolve(extensionDir, "..");
const fakeGer = path.join(extensionDir, "test", "e2e", "fake_ger.py");
const golden = path.join(extensionDir, "test", "e2e", "transcript.golden.md");

function codeExecutable(): string {
  const candidates = [
    process.env.VSCODE_EXE,
    path.join(process.env.LOCALAPPDATA ?? "", "Programs", "Microsoft VS Code", "Code.exe"),
    "/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
    "/usr/share/code/code",
    "/usr/bin/code",
  ];
  const found = candidates.find((candidate) => candidate && fs.existsSync(candidate));
  if (!found) {
    throw new Error("VS Code not found: set VSCODE_EXE to its executable");
  }
  return found;
}

function main(): number {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "ger-e2e-"));
  const repo = path.join(work, "repo");
  const profile = path.join(work, "profile");
  const report = path.join(work, "transcript.md");
  const fake = `uv run --project "${projectDir}" python "${fakeGer}"`;

  // Your own git config and ger's cache and config stay out: no Gerrit settings, tokens or
  // recent upstreams of yours, and no cached change from an earlier run with other commit ids.
  const gitconfig = path.join(work, "gitconfig");
  fs.writeFileSync(gitconfig, "");
  const hermetic: NodeJS.ProcessEnv = {
    ...process.env,
    GIT_CONFIG_GLOBAL: gitconfig,
    GIT_CONFIG_NOSYSTEM: "1",
    XDG_CACHE_HOME: path.join(work, "cache"),
    XDG_CONFIG_HOME: path.join(work, "config"),
  };

  const made = spawnSync(`${fake} --make-repo "${repo}"`, { shell: true, stdio: "inherit", env: hermetic });
  if (made.status !== 0) {
    throw new Error("could not create the fixture repository");
  }
  fs.mkdirSync(path.join(profile, "User"), { recursive: true });
  const settings = { "ger.executable": fake, "ger.pollSeconds": 3600, "security.workspace.trust.enabled": false };
  fs.writeFileSync(path.join(profile, "User", "settings.json"), JSON.stringify(settings));

  // Gerrit's event stream for fake_ger.py: the suite appends an event, the fake reads it.
  const events = path.join(work, "events.jsonl");
  fs.writeFileSync(events, "");
  const env: NodeJS.ProcessEnv = { ...hermetic, GER_E2E_OUT: report, GER_E2E_EVENTS: events, GER_E2E_DOWN: path.join(work, "down") };
  delete env.ELECTRON_RUN_AS_NODE; // set when started from VS Code's own terminal; it would run Code as plain Node
  const ran = spawnSync(
    codeExecutable(),
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
    ],
    { env, stdio: "inherit", timeout: 300_000 },
  );
  if (ran.status !== 0) {
    if (fs.existsSync(report)) {
      console.log(`\n${fs.readFileSync(report, "utf8")}`);
    }
    console.log(`e2e: FAILED (exit ${ran.status ?? ran.signal})`);
    return ran.status ?? 1;
  }
  return compare(report);
}

/** The transcript against the golden one: the same, or a diff to judge (and `--update` to accept). */
function compare(report: string): number {
  const transcript = fs.readFileSync(report, "utf8");
  if (process.argv.includes("--update")) {
    fs.writeFileSync(golden, transcript, "utf8");
    console.log(`e2e: passed; transcript written to ${golden}`);
    return 0;
  }
  if (fs.existsSync(golden) && fs.readFileSync(golden, "utf8") === transcript) {
    console.log("e2e: passed; the transcript matches transcript.golden.md");
    return 0;
  }
  spawnSync("git", ["diff", "--no-index", "--no-color", golden, report], { stdio: "inherit" });
  console.log(`e2e: FAILED: the transcript differs from transcript.golden.md (new one: ${report}).`);
  console.log("Check each changed step against its Expect line; if the new behaviour is right: npm run e2e -- --update");
  return 1;
}

process.exit(main());
