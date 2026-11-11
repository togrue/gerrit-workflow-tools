// How ger is started: the program `ger.executable` names, and commands that need a human in front of them.

import * as fs from "node:fs";
import * as vscode from "vscode";

import { programArgv, shellCommandLine, shellKind } from "./commandLine";
import { setting } from "./config";
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
  return programArgv(setting("executable", "ger"), "ger", isFile);
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
