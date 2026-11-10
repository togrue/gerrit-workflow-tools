// `npm run e2e`: start a real VS Code on a fixture repository, with the extension under
// development and a fake Gerrit behind `ger serve`, and run suite.ts inside it.
//
// Uses its own profile and extensions directory, so your VS Code is not touched. Needs `uv`
// (to run fake_ger.py with the project's Python) and a VS Code executable: VSCODE_EXE, or the
// usual install locations.

import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const extensionDir = path.resolve(__dirname, "..", "..", "..");
const projectDir = path.resolve(extensionDir, "..");
const fakeGer = path.join(extensionDir, "test", "e2e", "fake_ger.py");

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
  const report = path.join(work, "views.md");
  const fake = `uv run --project "${projectDir}" python "${fakeGer}"`;

  const made = spawnSync(`${fake} --make-repo "${repo}"`, { shell: true, stdio: "inherit" });
  if (made.status !== 0) {
    throw new Error("could not create the fixture repository");
  }
  fs.mkdirSync(path.join(profile, "User"), { recursive: true });
  const settings = { "ger.executable": fake, "ger.pollSeconds": 3600, "security.workspace.trust.enabled": false };
  fs.writeFileSync(path.join(profile, "User", "settings.json"), JSON.stringify(settings));

  const env: NodeJS.ProcessEnv = { ...process.env, GER_E2E_OUT: report };
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
    { env, stdio: "inherit", timeout: 180_000 },
  );
  if (fs.existsSync(report)) {
    console.log(`\n${fs.readFileSync(report, "utf8")}`);
  }
  console.log(ran.status === 0 ? "e2e: passed" : `e2e: FAILED (exit ${ran.status ?? ran.signal})`);
  return ran.status ?? 1;
}

process.exit(main());
