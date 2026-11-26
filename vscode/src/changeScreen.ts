// The Change view as a screen: the change (changeRows.ts) with what is drawn around it, the task
// header (taskRows.ts) and Next (nextAction.ts), put together so that the screen has exactly one
// primary button and it is the next step (Q2 of docu/plans/extension-vision.md). The rule, in
// vscode/README.md beside the Next rules: Next is the primary button; where the next step is a
// button the screen already has (Continue in the task header, Update to patchset N under the
// change's state), that button is the primary one and Next is not drawn twice; every other button
// is drawn plain. Pure: no `vscode`, no I/O. The Change view and the gallery both draw with it.

import type { ChangeVm } from "./changeRows";
import type { NextAction, NextKind } from "./nextAction";
import { REBASE_ACTIONS } from "./stackRows";
import type { TaskVm } from "./taskRows";

/** Next kinds that a button already on screen does: the command that button runs. */
const ON_SCREEN: Partial<Record<NextKind, string>> = {
  continue: REBASE_ACTIONS.continue.command,
  update: "ger.reviewUpdate",
};

export interface ScreenParts {
  task?: TaskVm;
  next?: NextAction;
  /** Something under way the view waits on, said in place of Next. */
  busy?: string;
}

const plain = <T extends { primary?: boolean }>(action: T): T => {
  const { primary: _primary, ...rest } = action;
  return rest as T;
};

/**
 * `vm` with the task header and Next. Reworking, the header holds Continue, Abort and Skip: the
 * head of a commit stopped at says what is left, not the buttons again.
 */
export function changeScreen(vm: ChangeVm, parts: ScreenParts): ChangeVm {
  const { task, busy } = parts;
  // While a step runs (here, or in the terminal as the header says), nothing is offered as next.
  const next = busy || task?.busy ? undefined : parts.next;
  const command = next && ON_SCREEN[next.kind];
  // With no Next, the buttons keep what the change's own view model made primary.
  const primaryIf = <T extends { primary?: boolean }>(action: T, runs: string) => (!next ? action : command === runs ? { ...action, primary: true } : plain(action));
  const head = vm.head && { ...vm.head, actions: task?.mode === "rework" ? undefined : vm.head.actions?.map((action) => primaryIf(action, action.command.id)) };
  const notes = vm.notes?.map((note) => ({ ...note, actions: note.actions?.map((action) => primaryIf(action, action.command.id)) }));
  // The step under way is said once: in Next's place, when it says what the header would and more.
  const said = busy !== undefined && task?.busy !== undefined && busy.startsWith(task.busy);
  const header = task && { ...task, busy: said ? undefined : task.busy, actions: task.actions.map((action) => primaryIf(action, action.command)) };
  // Next is drawn unless a button on screen already does it.
  const onScreen = command !== undefined && [...(head?.actions ?? []), ...(notes ?? []).flatMap((note) => note.actions ?? [])].some((action) => action.command.id === command);
  const inHeader = command !== undefined && (header?.actions.some((action) => action.command === command && !action.notNow) ?? false);
  const drawn = next && !onScreen && !inHeader ? { label: next.label, title: next.title } : undefined;
  return { ...vm, head, notes, next: drawn, busy, task: header };
}

/**
 * What the Change view's title bar offers that goes to Gerrit (P6: nothing a press would refuse):
 * Publish Drafts for a change on Gerrit, Vote and Publish also only on somebody else's open change.
 * Neither while the change could not be read (`failed`): what was shown before is not this answer.
 */
export function gerritOffers(shown: { pushed: boolean; own?: boolean | null; change_status?: string | null } | undefined, failed: boolean): { onGerrit: boolean; votable: boolean } {
  const known = failed ? undefined : shown;
  const open = !known?.change_status || known.change_status === "NEW";
  return { onGerrit: Boolean(known?.pushed), votable: Boolean(known?.pushed) && known?.own === false && open };
}
