// The task you are in: a review of a chain (a staged review here) or a rework of your own (a rebase
// underway). Its header (taskRows.ts) is drawn at the top of the Change view, where the task is
// worked, in both modes. Review chains and Local stack step aside by the `ger.task` and
// `ger.taskAll` context keys their `when` clauses read, so the Change view has the room (the
// header lists the chain's changes, as Local stack's rows would); "All chains" brings them back for
// the rest of that task.
//
// Not a view of its own: VS Code gives a view that appears the room the others left, and restores
// the Change view's stored height (measured: 530 of 790 px for a five-line header).

import * as vscode from "vscode";

import { Model } from "./model";
import { TaskMode, TaskOptions, TaskVm, taskKey, taskLines, taskMode, taskVm } from "./taskRows";

export class Task implements vscode.Disposable {
  /** The task whose other lists you asked to see: they stay for the rest of it, moving along it too. */
  private allFor: string | undefined;
  private busyText: string | undefined;
  /** Where the checkout stood when a step that runs in the terminal was started (`busyUntilMoved`). */
  private movedFrom: string | undefined;
  private busyTimer: ReturnType<typeof setTimeout> | undefined;
  /** The change the Change view shows, with its comments counted as it counts them. Set by the extension. */
  shown: () => TaskOptions["shown"] = () => undefined;
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
    this.movedFrom = undefined;
    clearTimeout(this.busyTimer);
    this.changed.fire();
  }

  /**
   * Say `text` from now until the served state moves on (another stop, the rebase over): a step run
   * in the terminal (Continue, Abort, Skip), which does not say when it ends. Given up after
   * `timeoutMs`: the terminal may be waiting on a question, and the header must not claim it runs.
   */
  busyUntilMoved(text: string, timeoutMs = 120_000): void {
    this.busy(text);
    this.movedFrom = this.position();
    this.busyTimer = setTimeout(() => this.busy(undefined), timeoutMs);
  }

  private position(): string {
    const stack = this.model.stack;
    return [stack?.rebase_in_progress, stack?.head, stack?.rebase?.current, stack?.rebase?.stop, stack?.rebase?.unmerged.length].join("/");
  }

  /** Follow the served state: which mode, and the context keys the other views' `when` clauses read. */
  private update(): void {
    if (this.movedFrom !== undefined && this.position() !== this.movedFrom) {
      this.busy(undefined); // the step in the terminal moved the checkout on: the header says where it is now
    }
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
    return this.mode ? taskVm(this.model.overview, this.model.stack, { all: this.all, busy: this.busyText, shown: this.shown(), chain: this.model.reviewChain, inboxUnknown: Boolean(this.model.overview?.error), subjects: this.model.subjects }) : undefined;
  }

  /** The header as text: for the end-to-end transcript. */
  lines(): string[] {
    return taskLines(this.vm());
  }

  dispose(): void {
    clearTimeout(this.busyTimer);
    this.subscription.dispose();
    this.changed.dispose();
  }
}
