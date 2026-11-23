// `npm run e2e`: start a real VS Code on a fixture repository, with the extension under
// development and a fake Gerrit behind `ger serve`, and run suite.ts inside it.
//
// Uses its own profile and extensions directory, so your VS Code is not touched. Needs `uv`
// (to run fake_ger.py with the project's Python) and a VS Code executable: VSCODE_EXE, the
// usual install locations, or the portable one `npm run e2e -- --get-vscode` puts in
// ~/.cache/ger-vscode-test. On Linux without a display, VS Code runs headless.
//
// GER_E2E_PNG=<dir> also writes a picture of the window on start and after every press; that
// is off by default, as it makes the run slower. The transcript is written next to them.
//
// GER_E2E_VSCODE_VERSION=<x.y.z> makes `--get-vscode` download that version instead of the
// latest, so a run does not change with VS Code's releases. GER_E2E_TIMEOUT_SECONDS gives VS Code
// longer than the default 300 seconds to finish the suite.
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

/** Where `--get-vscode` unpacks a portable VS Code for Linux. */
const portable = path.join(os.homedir(), ".cache", "ger-vscode-test");
const portableCode = path.join(portable, `VSCode-linux-${process.arch === "arm64" ? "arm64" : "x64"}`, "code");

function codeExecutable(): string {
  const candidates = [
    process.env.VSCODE_EXE,
    portableCode,
    path.join(process.env.LOCALAPPDATA ?? "", "Programs", "Microsoft VS Code", "Code.exe"),
    "/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
    "/usr/share/code/code",
    "/usr/bin/code",
  ];
  const found = candidates.find((candidate) => candidate && fs.existsSync(candidate));
  if (!found) {
    const get = process.platform === "linux" ? ", or get a portable one: npm run e2e -- --get-vscode" : "";
    throw new Error(`VS Code not found: set VSCODE_EXE to its executable${get}`);
  }
  return found;
}

/** Download a stable VS Code for Linux (x64 or arm64) into `portable`, replacing an older one:
 * GER_E2E_VSCODE_VERSION, or the latest. */
function getVscode(): number {
  const arch = { x64: "x64", arm64: "arm64" }[process.arch as string];
  if (process.platform !== "linux" || !arch) {
    console.log(`e2e: --get-vscode is for Linux on x64 or arm64, not ${process.platform} ${process.arch}`);
    return 1;
  }
  // Unpacked next to the one in use, and swapped in only once it is complete: a failed
  // download leaves a working VS Code where it was.
  fs.mkdirSync(portable, { recursive: true });
  const fresh = fs.mkdtempSync(path.join(portable, "download-"));
  const version = process.env.GER_E2E_VSCODE_VERSION || "latest";
  const url = `https://update.code.visualstudio.com/${version}/linux-${arch}/stable`;
  const got = spawnSync(`curl -fsSL "${url}" | tar xz -C "${fresh}"`, { shell: true, stdio: "inherit" });
  const unpacked = fs.existsSync(fresh) ? fs.readdirSync(fresh).find((name) => name.startsWith("VSCode-linux-")) : undefined;
  if (got.status !== 0 || !unpacked || !fs.existsSync(path.join(fresh, unpacked, "code"))) {
    fs.rmSync(fresh, { recursive: true, force: true });
    console.log(`e2e: could not download VS Code from ${url}`);
    return 1;
  }
  const installed = path.dirname(portableCode);
  fs.rmSync(installed, { recursive: true, force: true });
  fs.renameSync(path.join(fresh, unpacked), installed);
  fs.rmSync(fresh, { recursive: true, force: true });
  console.log(`e2e: VS Code is in ${path.dirname(portableCode)}`);
  return 0;
}

/** Linux with neither X nor Wayland: Electron draws without a screen. */
const noDisplay = process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY;

function main(): number {
  if (process.argv.includes("--get-vscode")) {
    return getVscode();
  }
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
  const env: NodeJS.ProcessEnv = { ...hermetic, GER_E2E_OUT: report, GER_E2E_EVENTS: events, GER_E2E_DOWN: path.join(work, "down"), GER_E2E_API: path.join(work, "api") };
  const pictures = process.env.GER_E2E_PNG ? path.resolve(process.env.GER_E2E_PNG) : undefined;
  if (pictures) {
    // Port 0: Electron picks a free one and writes it to this file in the profile.
    env.GER_E2E_PNG = pictures;
    env.GER_E2E_DEVTOOLS = path.join(profile, "DevToolsActivePort");
  }
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
      ...(noDisplay ? ["--ozone-platform=headless"] : []),
      ...(pictures ? ["--remote-debugging-port=0"] : []),
    ],
    { env, stdio: "inherit", timeout: Number(process.env.GER_E2E_TIMEOUT_SECONDS || 300) * 1000 },
  );
  if (pictures && fs.existsSync(report)) {
    fs.mkdirSync(pictures, { recursive: true });
    fs.copyFileSync(report, path.join(pictures, "transcript.md"));
  }
  if (ran.status !== 0) {
    if (fs.existsSync(report)) {
      console.log(`\n${fs.readFileSync(report, "utf8")}`);
    }
    console.log(`e2e: FAILED (exit ${ran.status ?? ran.signal})`);
    return ran.status ?? 1;
  }
  if (pictures) {
    console.log(`e2e: pictures of every step in ${pictures}`);
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
