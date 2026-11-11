// How ger is started: the program `ger.executable` names, and commands that need a human in front of them.

import * as fs from "node:fs";
import * as os from "node:os";
import * as vscode from "vscode";

import { programArgv, shellCommandLine, shellKind } from "./commandLine";
import { setting } from "./config";
import { installArgv, installedGer, onPath, releaseUrl } from "./install";
import { ui } from "./ui";

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

/**
 * Run a ger command that needs a human in front of it (it prompts, or opens an editor). Each
 * gets a terminal of its own: typing into one where a command still runs would answer its prompt.
 */
export function runInTerminal(repo: string, args: string[]): void {
  ui().terminal({ name: `ger ${args[0]}`, cwd: repo }, gerCommandLine(args));
}

/**
 * Install the ger release that matches this extension, in a terminal the user watches: with uv,
 * else pipx. With neither there, the install guide says what to do.
 */
export async function installGer(cwd: string, manifest: { version: string; repository: { url: string } }): Promise<void> {
  const argv = installArgv(releaseUrl(manifest.repository.url, manifest.version), (program) => !!onPath(program, process.env, process.platform, isFile));
  if (argv) {
    ui().terminal({ name: "install ger", cwd }, shellCommandLine(argv, shellKind(vscode.env.shell)));
    return;
  }
  const guide = "Open Install Guide";
  const picked = await ui().message("warning", "Installing ger needs uv or pipx, and neither is on the PATH. The install guide shows how to get one.", {}, [guide]);
  if (picked === guide) {
    await ui().openExternal(vscode.Uri.parse(`${manifest.repository.url}#install`));
  }
}
