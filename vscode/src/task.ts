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
import { Outcome, TaskMode, TaskOptions, TaskVm, taskKey, taskLines, taskMode, taskVm } from "./taskRows";
import { feedback } from "./ui";

/** A step's outcome said here (a refusal before anything ran), with when. */
interface Said extends Outcome {
  at: number;
  /** HEAD then: once it moved on, the refusal is history, as a served outcome is. */
  head: string | null | undefined;
}

export class Task implements vscode.Disposable {
  /** The task whose other lists you asked to see: they stay for the rest of it, moving along it too. */
  private allFor: string | undefined;
  private busyText: string | undefined;
  /** Where the checkout stood when a step that runs in the terminal was started (`busyUntilMoved`). */
  private movedFrom: string | undefined;
  private busyTimer: ReturnType<typeof setTimeout> | undefined;
  /** When a step was last started from the views: an outcome older than that is history. */
  private pressedAt = 0;
  /** When the step under way was started: a served outcome at or after it ends it. */
  private busySince = 0;
  /** A refusal said here, before anything ran in the terminal. */
  private said: Said | undefined;
  /** The served outcome last told in the status bar, by its time: each is told once. */
  private told = 0;
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

  /** A step is asked for from the views: what the last one said is history from now. */
  pressed(): void {
    this.pressedAt = Date.now();
    this.said = undefined;
    this.changed.fire();
  }

  /**
   * A step was refused before anything ran: said in the header until the next action, nothing under
   * way. The refusal is core's fresh look, so the stack is read again: what the buttons under it say
   * must not contradict it (state 16: Continue still offered "commit this stop as it is").
   */
  refused(text: string): void {
    this.busy(undefined);
    this.said = { text, ok: false, more: [], at: Date.now(), head: this.model.stack?.head };
    this.changed.fire();
    void this.model.refreshLocal();
  }

  /**
   * Say `text` from now until the step run in the terminal (Continue, Abort, Skip) has ended: its
   * step outcome is served, or the served state moved on (another stop, the rebase over). Called
   * only once the step was sent, never while a question about it is open. Given up after
   * `timeoutMs`: the terminal may be waiting on a question, and the header must not claim it runs.
   */
  busyUntilMoved(text: string, timeoutMs = 120_000): void {
    this.busy(text);
    this.movedFrom = this.position();
    this.busySince = Date.now();
    this.busyTimer = setTimeout(() => this.busy(undefined), timeoutMs);
  }

  /**
   * How the last step ended, until the next action: a refusal said here, else the step outcome ger
   * left, while it is newer than the last press and `HEAD` is where it left it.
   */
  outcome(): Outcome | undefined {
    const served = this.served();
    const said = this.said && this.said.head === this.model.stack?.head ? this.said : undefined;
    if (said && (!served || served.at * 1000 < said.at)) {
      return { text: said.text, ok: said.ok, more: said.more };
    }
    return served ? { text: served.text, ok: served.ok, more: served.more } : undefined;
  }

  private served() {
    const stack = this.model.stack;
    const last = stack?.last_outcome;
    // ger's clock and this one are the same machine's, and the press comes before the step ends.
    return last && last.at * 1000 >= this.pressedAt && last.head === stack?.head ? last : undefined;
  }

  private position(): string {
    const stack = this.model.stack;
    return [stack?.rebase_in_progress, stack?.head, stack?.rebase?.current, stack?.rebase?.stop, stack?.rebase?.unmerged.length].join("/");
  }

  /** Follow the served state: which mode, and the context keys the other views' `when` clauses read. */
  private update(): void {
    const last = this.model.stack?.last_outcome;
    const ended = Boolean(last && this.busySince && last.at * 1000 >= this.busySince);
    if (this.movedFrom !== undefined && (ended || this.position() !== this.movedFrom)) {
      this.busySince = 0;
      this.busy(undefined); // the step in the terminal ended, or moved the checkout on: the header says where it is now
    }
    const served = this.served();
    if (served && served.at > this.told && this.pressedAt) {
      // Each outcome of a step started here is told once, in the past tense, where the "runs in the terminal" note was.
      this.told = served.at;
      feedback(served.text, { icon: served.ok ? "check" : "warning", milliseconds: 8000 });
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
    const outcome = this.busyText ? undefined : this.outcome();
    return this.mode ? taskVm(this.model.overview, this.model.stack, { all: this.all, busy: this.busyText, shown: this.shown(), chain: this.model.reviewChain, inboxUnknown: Boolean(this.model.overview?.error), subjects: this.model.subjects, ...(outcome ? { outcome } : {}) }) : undefined;
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
