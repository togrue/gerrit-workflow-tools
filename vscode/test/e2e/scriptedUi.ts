// The Ui the end-to-end suite swaps in: it answers each prompt from a script and writes down,
// as a user would have seen it, every notification, dialog, picker, input box, status bar
// message, terminal command, clipboard write, browser and window it was asked for.

import type * as vscode from "vscode";

import type { Level, MessageOptions, TerminalOptions, Ui } from "../../src/ui";

/** How the scripted user answers the next prompt. Nothing queued: they dismiss it. */
export type Answer = { button: string } | { pick: string } | { type: string } | { dismiss: true };

function describe(answer: Answer): string {
  return "button" in answer ? `press "${answer.button}"` : "pick" in answer ? `pick "${answer.pick}"` : "type" in answer ? `type "${answer.type}"` : "dismiss";
}

export class ScriptedUi implements Ui {
  private lines: string[] = [];
  private queue: Answer[] = [];
  private problems: string[] = [];

  constructor(private readonly normalize: (text: string) => string) {}

  /** Queue the answers for the next prompts, in order. */
  script(answers: Answer[]): void {
    this.queue = [...answers];
  }

  /** What happened since the last call, and what went against the script. */
  drain(): { lines: string[]; problems: string[] } {
    const unused = this.queue.map((answer) => `the script said to ${describe(answer)}, but nothing asked`);
    const result = { lines: this.lines, problems: [...this.problems, ...unused] };
    [this.lines, this.queue, this.problems] = [[], [], []];
    return result;
  }

  private say(line: string): void {
    this.lines.push(this.normalize(line));
  }

  private next(kind: "button" | "pick" | "type"): Answer | undefined {
    const answer = this.queue.shift();
    if (answer && !("dismiss" in answer) && !(kind in answer)) {
      this.problems.push(`the script said to ${describe(answer)}, but a ${kind === "button" ? "message" : kind === "pick" ? "picker" : "input box"} asked`);
      return undefined;
    }
    return answer;
  }

  message(level: Level, text: string, options: MessageOptions, buttons: string[]): Thenable<string | undefined> {
    const kind = options.modal ? "dialog" : "notification";
    const offered = (options.modal ? [...buttons, "Cancel"] : buttons).map((b) => `[${b}]`).join(" ");
    this.say(`${kind} (${level}): ${text}${offered ? `  ${offered}` : ""}`);
    if (options.detail) {
      this.say(`    detail: ${options.detail}`);
    }
    const answer = this.next("button");
    const pressed = answer && "button" in answer ? answer.button : undefined;
    if (pressed !== undefined && !buttons.includes(pressed)) {
      this.problems.push(`the script said to press "${pressed}", but the ${kind} offered ${offered || "no buttons"}`);
      return Promise.resolve(undefined);
    }
    if (buttons.length || options.modal) {
      this.say(`    → ${pressed ? `pressed "${pressed}"` : options.modal ? "cancelled" : "dismissed"}`);
    }
    return Promise.resolve(pressed);
  }

  pick<T extends vscode.QuickPickItem>(items: T[], options: vscode.QuickPickOptions): Thenable<T | undefined> {
    this.say(`picker: ${options.title ?? ""}${options.placeHolder ? ` — "${options.placeHolder}"` : ""}`);
    for (const item of items) {
      this.say(`    ${item.label}${item.description ? ` — ${item.description}` : ""}`);
    }
    const answer = this.next("pick");
    const wanted = answer && "pick" in answer ? answer.pick : undefined;
    const picked = wanted === undefined ? undefined : items.find((item) => item.label === wanted);
    if (wanted !== undefined && !picked) {
      this.problems.push(`the script said to pick "${wanted}", which the picker did not offer`);
    }
    this.say(`    → ${picked ? `picked "${picked.label}"` : "escaped"}`);
    return Promise.resolve(picked);
  }

  input(options: vscode.InputBoxOptions): Thenable<string | undefined> {
    this.say(`input box: ${options.title ?? ""}`);
    if (options.prompt) {
      this.say(`    prompt: ${options.prompt}`);
    }
    if (options.placeHolder) {
      this.say(`    placeholder: ${options.placeHolder}`);
    }
    if (options.value) {
      this.say(`    already filled in: "${options.value.replaceAll("\n", "⏎")}"`);
    }
    const answer = this.next("type");
    const typed = answer && "type" in answer ? answer.type : undefined;
    this.say(`    → ${typed === undefined ? "escaped" : `${options.value ? "changed it to" : "typed"} "${typed}" and pressed Enter`}`);
    return Promise.resolve(typed);
  }

  status(text: string): void {
    this.say(`status bar message: ${text}`);
  }

  progress<R>(location: vscode.ProgressLocation | { viewId: string }, title: string | undefined, task: () => Thenable<R>): Thenable<R> {
    const where = typeof location === "object" ? `in view ${location.viewId}` : "notification";
    this.say(`progress (${where}): ${title ?? ""}`);
    return task();
  }

  terminal(options: TerminalOptions, text: string): void {
    const env = options.env ? ` with ${Object.entries(options.env).map(([k, v]) => `${k}=${v}`).join(" ")}` : "";
    this.say(`terminal "${options.name}"${env}: runs \`${text}\``);
  }

  copy(text: string): Thenable<void> {
    const lines = text.split("\n");
    this.say(`clipboard: ${lines.length} lines, starting "${lines[0]}"`);
    return Promise.resolve();
  }

  openExternal(uri: vscode.Uri): Thenable<boolean> {
    this.say(`browser opens: ${uri.toString(true)}`);
    return Promise.resolve(true);
  }

  openFolder(uri: vscode.Uri): Thenable<unknown> {
    this.say(`new VS Code window on: ${uri.fsPath}`);
    return Promise.resolve();
  }
}

/** The lines of `after` that are not in `before` (`+`) and the other way round (`-`), in order. */
export function lineDiff(before: string[], after: string[]): string[] {
  const n = before.length;
  const m = after.length;
  const common: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      common[i][j] = before[i] === after[j] ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
    }
  }
  const out: string[] = [];
  let [i, j] = [0, 0];
  while (i < n || j < m) {
    if (i < n && j < m && before[i] === after[j]) {
      [i, j] = [i + 1, j + 1];
    } else if (j < m && (i === n || common[i][j + 1] >= common[i + 1][j])) {
      out.push(`+ ${after[j++]}`);
    } else {
      out.push(`- ${before[i++]}`);
    }
  }
  return out;
}
