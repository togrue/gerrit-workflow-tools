// Giving the working branch an upstream: without one there is no local stack to show.

import * as vscode from "vscode";

import { Model, describeError } from "./model";
import { ui } from "./ui";

/** Set `upstream` directly, else the one picked from the suggestion and the remote branches. */
export async function setUpstream(model: Model, upstream?: string): Promise<void> {
  try {
    const client = await model.client();
    let chosen = upstream;
    if (!chosen) {
      const choice = await client.upstream();
      if (!choice.branch) {
        void ui().message("warning", "HEAD is detached: check out a branch to give it an upstream.", {}, []);
        return;
      }
      const items = choice.candidates.map((name): vscode.QuickPickItem => ({
        label: name,
        description:
          [name === choice.upstream ? "current" : "", name === choice.suggested ? `$(star-full) suggested: ${choice.why ?? ""}` : ""]
            .filter(Boolean)
            .join(" · ") || undefined,
      }));
      const picked = await ui().pick(items, {
        title: `Upstream for ${choice.branch}`,
        placeHolder: "The local stack is the commits above it; ger push goes to its branch on Gerrit",
        matchOnDescription: true,
      });
      chosen = picked?.label;
    }
    if (!chosen) {
      return;
    }
    const done = await client.setUpstream(chosen);
    ui().status(`$(check) ${done.branch} now tracks ${done.upstream}`, 6000);
    await model.refresh();
  } catch (error) {
    void ui().message("warning", `Upstream not set: ${describeError(error)}`, {}, []);
  }
}
