// How ger is started: the program `ger.executable` names, and commands that need a human in front of them.

import * as fs from "node:fs";
import * as os from "node:os";
import * as vscode from "vscode";

import type { Health } from "./api";
import { programArgv, shellCommandLine, shellKind } from "./commandLine";
import { packageSkew } from "./compat";
import { setting } from "./config";
import { installArgv, installedGer, onPath, releaseUrl } from "./install";
import { feedback, ui } from "./ui";

function isFile(candidate: string): boolean {
  try {
    return fs.statSync(candidate).isFile();
  } catch {
    return false;
  }
}

/** The program and leading arguments that run ger: `["ger"]`, `["uv", "run", "ger"]`, a path with blanks. */
export function gerProgram(): string[] {
  const argv = programArgv(setting("executable", "ger"), "ger", isFile);
  if (argv.length === 1 && argv[0] === "ger" && !onPath("ger", process.env, process.platform, isFile)) {
    // Just installed: this editor's PATH is from before.
    return [installedGer(os.homedir(), process.platform, isFile) ?? "ger"];
  }
  return argv;
}

/** `ger <args>` as a line the terminal's shell runs, quoted for that shell. */
export function gerCommandLine(args: string[]): string {
  return shellCommandLine([...gerProgram(), ...args], shellKind(vscode.env.shell));
}

/** The one terminal every ger command the extension starts runs in. */
export const GER_TERMINAL = "ger";

/** Run a ger command that needs a human in front of it (it prompts, or opens an editor), in the ger terminal. */
export function runInTerminal(repo: string, args: string[]): void {
  ui().terminal({ name: GER_TERMINAL, cwd: repo }, gerCommandLine(args));
}

/**
 * Install the ger release that matches this extension, in a terminal the user watches: with uv,
 * else pipx. With neither there, the install guide says what to do.
 */
export async function installGer(cwd: string, manifest: { version: string; repository: { url: string } }): Promise<void> {
  const argv = installArgv(releaseUrl(manifest.repository.url, manifest.version), (program) => !!onPath(program, process.env, process.platform, isFile));
  if (argv) {
    ui().terminal({ name: GER_TERMINAL, cwd }, shellCommandLine(argv, shellKind(vscode.env.shell)));
    feedback(`Installing ger with ${argv[0]} in the ger terminal: Retry once it is done`, { icon: "terminal", milliseconds: 8000 });
    return;
  }
  const guide = "Open Install Guide";
  const picked = await ui().message("warning", "Installing ger needs uv or pipx, and neither is on the PATH. The install guide shows how to get one.", {}, [guide]);
  if (picked === guide) {
    await ui().openExternal(vscode.Uri.parse(`${manifest.repository.url}#install`));
  }
}

const SKEW_KEY = "ger.versionSkewDismissed";

/**
 * What to say when ger's package version is not the extension's while the API fits: a warning, once
 * per session for each pair of versions, unless you said not to for that pair. **Update ger** only
 * when ger is the older one: a newer ger is never downgraded, the extension is what to update then.
 * Every comparison and every answer goes to the log.
 */
export function versionSkewWarner(
  cwd: string,
  manifest: { version: string; repository: { url: string } },
  memento: vscode.Memento,
  log: (line: string) => void,
): (health: Health) => void {
  const warned = new Set<string>();
  return (health) => {
    const skew = packageSkew(health.version, manifest.version);
    const pair = `ger ${health.version} / extension ${manifest.version}`;
    if (skew === "same") {
      return;
    }
    if (skew === "unknown") {
      log(`Versions differ (${pair}), but one is a dev or local build: no warning.`);
      return;
    }
    const dismissed = memento.get<string[]>(SKEW_KEY, []);
    if (dismissed.includes(pair)) {
      log(`Versions differ (${pair}): not shown, you chose Don't Show Again for this pair.`);
      return;
    }
    if (warned.has(pair)) {
      return;
    }
    warned.add(pair);
    const update = "Update ger";
    const never = "Don't Show Again";
    const older = skew === "ger-older";
    const text = older
      ? `ger ${health.version} is older than the extension (${manifest.version}). It works, but some views may lack what a newer ger serves.`
      : `ger ${health.version} is newer than the extension (${manifest.version}). It works; update the extension to match.`;
    log(`Versions differ (${pair}): ${older ? "ger is older, offered Update ger" : "ger is newer, suggested updating the extension"}.`);
    void ui()
      .message("warning", text, {}, older ? [update, never] : [never])
      .then(async (picked) => {
        log(`Versions differ (${pair}): ${picked ? `you chose ${picked}` : "dismissed"}.`);
        if (picked === update) {
          await installGer(cwd, manifest);
        } else if (picked === never) {
          await memento.update(SKEW_KEY, [...dismissed, pair]);
        }
      });
  };
}
