// The task you are in: a review of a chain (a staged review here) or a rework of your own (a rebase
// underway). Its header (taskRows.ts) is drawn where the task is worked: at the top of the Change
// view while you review, in place of Local stack's rebase banner while you rework. Review chains
// (and, reviewing, Local stack) step aside by the `ger.task` and `ger.taskAll` context keys their
// `when` clauses read, so the Change view has the room; "All chains" brings them back for the rest
// of that task.
//
// Not a view of its own: VS Code gives a view that appears the room the others left, and restores
// the Change view's stored height (measured: 530 of 790 px for a five-line header).

import * as vscode from "vscode";

import { Model } from "./model";
import { TaskMode, TaskVm, taskKey, taskLines, taskMode, taskVm } from "./taskRows";

export class Task implements vscode.Disposable {
  /** The task whose other lists you asked to see: they stay for the rest of it, moving along it too. */
  private allFor: string | undefined;
  private busyText: string | undefined;
  private keys: string | undefined;
  private readonly changed = new vscode.EventEmitter<void>();
  /** The header changed: the views that draw it draw it anew. */
  readonly onDidChange = this.changed.event;
  private readonly subscription: vscode.Disposable;
  /** What a click on a change of the chain does. Set by the extension. */
  go: (mode: TaskMode, key: string) => unknown = () => undefined;

  constructor(private readonly model: Model) {
    this.subscription = model.onDidChange(() => this.update());
  }

  /** The mode the checkout is in, once ger serve has said; undefined outside both. */
  get mode(): TaskMode | undefined {
    return this.model.state === "ready" ? taskMode(this.model.stack) : undefined;
  }

  /** Whether the other lists are hidden now: a task is on and you did not ask for them. */
  get focused(): boolean {
    return Boolean(this.mode) && !this.all;
  }

  private get all(): boolean {
    const key = taskKey(this.model.overview, this.model.stack);
    return key !== undefined && key === this.allFor;
  }

  /** All chains: the inbox (and Local stack) for the rest of this task. */
  showAll(): void {
    this.allFor = taskKey(this.model.overview, this.model.stack);
    this.update();
  }

  /** Back to the task alone. */
  focus(): void {
    this.allFor = undefined;
    this.update();
  }

  /** Say what a step under way is doing (the checkout of the next change), until `undefined`. */
  busy(text: string | undefined): void {
    this.busyText = text;
    this.changed.fire();
  }

  /** Follow the served state: which mode, and the context keys the other views' `when` clauses read. */
  private update(): void {
    const mode = this.mode ?? "";
    if (!mode) {
      this.allFor = undefined; // the task ended: the next one starts focused again
    }
    const all = this.all;
    if (`${mode}/${all}` !== this.keys) {
      this.keys = `${mode}/${all}`;
      void vscode.commands.executeCommand("setContext", "ger.taskAll", all);
      void vscode.commands.executeCommand("setContext", "ger.task", mode);
    }
    this.changed.fire();
  }

  /** The header for the mode the checkout is in. */
  vm(): TaskVm | undefined {
    return this.mode ? taskVm(this.model.overview, this.model.stack, { all: this.all, busy: this.busyText }) : undefined;
  }

  /** The header as text: for the end-to-end transcript. */
  lines(): string[] {
    return taskLines(this.vm());
  }

  dispose(): void {
    this.subscription.dispose();
    this.changed.dispose();
  }
}
