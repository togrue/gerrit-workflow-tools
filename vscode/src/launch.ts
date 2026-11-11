// How ger is started: the `ger.executable` setting, and commands that need a human in front of them.

import { setting } from "./config";
import { ui } from "./ui";

export function executable(): string {
  return setting("executable", "ger").trim() || "ger";
}

/** Run a ger command that needs a human in front of it (it prompts, or opens an editor). */
export function runInTerminal(repo: string, args: string): void {
  ui().terminal({ name: "ger", cwd: repo, reuse: true }, `${executable()} ${args}`);
}
