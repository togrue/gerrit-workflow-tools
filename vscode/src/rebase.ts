// Going on with a rebase underway: Continue, Abort and Skip on Local stack's banner (and in the
// palette), the conflicted files under it, and Push while a rebase is stopped. What can be done
// where, and why not, is core's verdict (`GET /stack`'s `rebase.actions`, `rebase.refused` and
// `push_blocked`); this runs it.

import * as path from "node:path";
import * as vscode from "vscode";

import { unsavedInRepo } from "./diffTabs";
import { runInTerminal } from "./launch";
import { sentence } from "./format";
import { Model, describeError } from "./model";
import { RebaseAction } from "./api";
import { REBASE_ACTIONS, REBASE_WORDS, abortQuestion, pushWaysOn, rebaseNotNowFresh, skipQuestion } from "./stackRows";
import { saveBeforeContinue } from "./staleDiffs";
import { feedback, ui } from "./ui";

const SAID: Record<RebaseAction, string> = {
  continue: "ger rebase --continue runs in the ger terminal: the views follow once the rebase moves on",
  abort: "ger rebase --abort runs in the ger terminal: the branch goes back to where it was before the rebase",
  skip: "ger rebase --skip runs in the ger terminal: the rebase goes on without that commit",
};

/** What the views say around a step run in the terminal: that it started (sent, nothing left to ask), or why it did not. */
export interface StepHooks {
  started?(): void;
  refused?(text: string): void;
}

/** A refusal said before anything ran: in the view (the hooks), and as a warning that is not waited on. */
function refuse(hooks: StepHooks, text: string): void {
  hooks.refused?.(text);
  void ui().message("warning", text, {}, []);
}

/**
 * `ger rebase --<step>` in the ger terminal, where git can open an editor or say why not. Only a
 * way on core allows from this stop: the palette has Skip only where it does (`ger.rebaseSkippable`),
 * and says why not when it is run anyway, after a fresh look at the stack (a `git add` in the terminal
 * may have resolved it since). Skip asks first: it leaves a commit out. Abort asks first where it
 * throws away what no stash keeps (core's `discards`: commits made in this rebase, a resolution).
 * Continue asks to save the repository's files with unsaved edits first: git goes on with the files
 * on disk. `hooks.started` runs once every question is answered and the step is sent, never before.
 */
export async function rebaseStep(model: Model, step: RebaseAction, hooks: StepHooks = {}): Promise<boolean> {
  const notNow = await rebaseNotNowFresh(model, step);
  if (notNow) {
    refuse(hooks, `ger rebase --${step} did not run: ${notNow}`);
    return false;
  }
  if (step === "skip" && !(await confirmSkip(model))) {
    return false;
  }
  if (step === "abort" && !(await confirmAbort(model, hooks))) {
    return false;
  }
  if (step === "continue" && !(await saveBeforeContinue(ui(), unsavedInRepo(model.repo)))) {
    return false;
  }
  if (step !== "abort" && (await refusedNow(model, step, `ger rebase --${step} did not run`, hooks))) {
    return false;
  }
  hooks.started?.();
  runInTerminal(model.repo, ["rebase", `--${step}`]);
  feedback(SAID[step], { icon: "terminal", milliseconds: 8000 });
  return true;
}

/**
 * Whether `ger rebase --<step>` would refuse now, as core's one check before every "go on" says
 * (`GET /rebase/refusal`: the stop, and for Continue the files changed and not amended or added).
 * A refusal is said here, after `what`: in the terminal it would go unseen behind the views.
 */
export async function refusedNow(model: Model, step: RebaseAction, what: string, hooks: StepHooks = {}): Promise<boolean> {
  let refused: string | null;
  try {
    refused = await (await model.client()).rebaseRefusal(step);
  } catch (error) {
    refused = describeError(error);
  }
  if (refused) {
    refuse(hooks, `${what}: ${sentence(refused)}.`);
  }
  return Boolean(refused);
}

/** Abort's question, where it throws away what no stash keeps: what goes, in words, and the button. A ger before API 1.8 says nothing: no question, as before. */
async function confirmAbort(model: Model, hooks: StepHooks): Promise<boolean> {
  let check: Awaited<ReturnType<Awaited<ReturnType<Model["client"]>>["abortCheck"]>>;
  try {
    check = await (await model.client()).abortCheck();
  } catch (error) {
    refuse(hooks, `ger rebase --abort did not run: ${sentence(describeError(error))}.`);
    return false;
  }
  if (check.refused) {
    refuse(hooks, `ger rebase --abort did not run: ${sentence(check.refused)}.`);
    return false;
  }
  if (!check.discards?.asks) {
    return true;
  }
  const { message, detail } = abortQuestion(check.discards);
  const abort = REBASE_ACTIONS.abort.label;
  return (await ui().message("warning", message, { modal: true, detail }, [abort])) === abort;
}

/**
 * Rebase onto the upstream: core's one command for it (`upstream.argv`) in the ger terminal, where it shows the todo
 * first. Only once core says the upstream moved on (`upstream.behind`); a conflict the preview
 * foresaw stops the rebase there, and the banner takes over.
 */
export async function rebaseOntoUpstream(model: Model): Promise<void> {
  const upstream = model.stack?.upstream;
  if (!upstream?.behind) {
    await ui().message("info", model.stack?.rebase_in_progress ? REBASE_WORDS.finishFirst : REBASE_WORDS.upToDate, {}, []);
    return;
  }
  runInTerminal(model.repo, upstream.argv);
  feedback(`ger ${upstream.argv.join(" ")} runs in the ger terminal: it shows the todo, then replays the stack onto ${upstream.name}`, { icon: "terminal", milliseconds: 8000 });
}

async function confirmSkip(model: Model): Promise<boolean> {
  const { message, detail } = skipQuestion(model.stack);
  const skip = REBASE_ACTIONS.skip.label;
  return (await ui().message("warning", message, { modal: true, detail }, [skip])) === skip;
}

/**
 * A conflicted file, to resolve: the file itself, with its conflict markers, in the text editor,
 * where VS Code offers its inline Accept Current / Incoming / Both and *Resolve in Merge Editor*.
 * Not the merge editor at once: its result starts out dirty ("No Changes Accepted"), and saving it
 * would write what it computed over a resolution made meanwhile in a terminal or another editor.
 */
export async function openConflict(model: Model, file: string): Promise<void> {
  await vscode.window.showTextDocument(vscode.Uri.file(path.join(model.repo, file)), { preview: false });
}

/**
 * Push to Gerrit…: `ger push` in the ger terminal, unless core says it would refuse whatever the
 * stack holds. During a rebase that is said here, with the ways on core allows (Continue once
 * nothing is unmerged, Abort) instead.
 */
export async function push(model: Model): Promise<void> {
  const blocked = model.stack?.push_blocked;
  if (blocked?.reason === "rebase") {
    const ways = pushWaysOn(model.stack);
    const picked = await ui().message("warning", "Finish the rebase first", { modal: true, detail: `${sentence(blocked.message)}.` }, ways.map((way) => REBASE_ACTIONS[way].label));
    const way = ways.find((way) => REBASE_ACTIONS[way].label === picked);
    if (way) {
      await rebaseStep(model, way);
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
