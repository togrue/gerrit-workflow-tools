// Going on with a rebase underway: Continue, Abort and Skip on Local stack's banner (and in the
// palette), the conflicted files under it, and Push while a rebase is stopped. What can be done
// where is core's verdict (`GET /stack`'s `rebase` and `push_blocked`); this runs it.

import * as path from "node:path";
import * as vscode from "vscode";

import { runInTerminal } from "./launch";
import { sentence } from "./format";
import { Model } from "./model";
import { REBASE_ACTIONS, RebaseStep } from "./stackRows";
import { feedback, ui } from "./ui";

const SAID: Record<RebaseStep, string> = {
  continue: "ger rebase --continue runs in the ger terminal: the views follow once the rebase moves on",
  abort: "ger rebase --abort runs in the ger terminal: the branch goes back to where it was before the rebase",
  skip: "ger rebase --skip runs in the ger terminal: the rebase goes on without that commit",
};

/** `ger rebase --<step>` in the ger terminal, where git can open an editor or say why not. Skip asks first: it leaves a commit out. */
export async function rebaseStep(model: Model, step: RebaseStep): Promise<void> {
  if (step === "skip" && !(await confirmSkip(model))) {
    return;
  }
  runInTerminal(model.repo, ["rebase", `--${step}`]);
  feedback(SAID[step], { icon: "terminal", milliseconds: 8000 });
}

async function confirmSkip(model: Model): Promise<boolean> {
  const stopped = model.stack?.commits.find((commit) => commit.rebase === "current");
  const what = stopped ? `"${stopped.summary}"` : "the commit the rebase stopped at";
  const gerrit = stopped?.pushed ? " Its change stays open on Gerrit until you abandon it there." : "";
  const detail = `The rebase goes on without it: once it is finished, the branch no longer has this commit. Until then, Abort brings it back.${gerrit}`;
  const skip = REBASE_ACTIONS.skip.label;
  return (await ui().message("warning", `Skip ${what}?`, { modal: true, detail }, [skip])) === skip;
}

/**
 * A conflicted file, to resolve: VS Code's merge editor, through the built-in git extension (it
 * reads the three versions from the index). Without it, or when it opened nothing (git disabled, the
 * repository not open in it), the file itself, with its conflict markers.
 */
export async function openConflict(model: Model, file: string): Promise<void> {
  const uri = vscode.Uri.file(path.join(model.repo, file));
  if ((await vscode.commands.getCommands(true)).includes("git.openMergeEditor")) {
    await Promise.resolve(vscode.commands.executeCommand("git.openMergeEditor", uri)).catch(() => undefined);
    if (mergeEditorOn(uri)) {
      return;
    }
  }
  await vscode.commands.executeCommand("vscode.open", uri);
}

/** The merge editor's tab input (`TabInputTextMerge`, not in the 1.85 API) has the file it writes as `result`. */
function mergeEditorOn(uri: vscode.Uri): boolean {
  const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input as { result?: unknown } | undefined;
  return input?.result instanceof vscode.Uri && input.result.toString() === uri.toString();
}

/**
 * Push to Gerrit…: `ger push` in the ger terminal, unless core says it would refuse whatever the
 * stack holds. During a rebase that is said here, with the way on (Continue, Abort) instead.
 */
export async function push(model: Model): Promise<void> {
  const blocked = model.stack?.push_blocked;
  if (blocked?.reason === "rebase") {
    const [go, back] = [REBASE_ACTIONS.continue.label, REBASE_ACTIONS.abort.label];
    const picked = await ui().message("warning", "Finish the rebase first", { modal: true, detail: `${sentence(blocked.message)}.` }, [go, back]);
    if (picked) {
      await rebaseStep(model, picked === go ? "continue" : "abort");
    }
    return;
  }
  if (blocked) {
    await ui().message("warning", "Finish the git am first", { modal: true, detail: `${sentence(blocked.message)}.` }, []);
    return;
  }
  runInTerminal(model.repo, ["push"]);
  feedback("ger push runs in the ger terminal: it shows what it will push and asks first", { icon: "terminal", milliseconds: 8000 });
}
