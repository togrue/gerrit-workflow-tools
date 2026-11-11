// Everything the extension says to the user or does outside its own views: notifications,
// pickers, input boxes, the status bar, progress, terminals, the clipboard, the browser and
// new windows. One seam, so the end-to-end suite can answer each prompt and record what a user
// would have seen and what would have happened.

import * as vscode from "vscode";

export type Level = "info" | "warning" | "error";

export interface MessageOptions {
  modal?: boolean;
  detail?: string;
}

export interface TerminalOptions {
  name: string;
  cwd: string;
  env?: Record<string, string>;
}

export interface Ui {
  /** A notification (or a modal dialog) with buttons; resolves to the button pressed. */
  message(level: Level, text: string, options: MessageOptions, buttons: string[]): Thenable<string | undefined>;
  pick<T extends vscode.QuickPickItem>(items: T[], options: vscode.QuickPickOptions): Thenable<T | undefined>;
  input(options: vscode.InputBoxOptions): Thenable<string | undefined>;
  /** A transient status bar message. */
  status(text: string, milliseconds: number): void;
  progress<R>(location: vscode.ProgressLocation | { viewId: string }, title: string | undefined, task: () => Thenable<R>): Thenable<R>;
  /** Open a new terminal and run the shell command line `text` in it. */
  terminal(options: TerminalOptions, text: string): void;
  copy(text: string): Thenable<void>;
  openExternal(uri: vscode.Uri): Thenable<boolean>;
  openFolder(uri: vscode.Uri): Thenable<unknown>;
}

export const vscodeUi: Ui = {
  message(level, text, options, buttons) {
    const show = level === "error" ? vscode.window.showErrorMessage : level === "warning" ? vscode.window.showWarningMessage : vscode.window.showInformationMessage;
    return options.modal || options.detail ? show(text, { modal: options.modal, detail: options.detail }, ...buttons) : show(text, ...buttons);
  },
  pick: (items, options) => vscode.window.showQuickPick(items, options),
  input: (options) => vscode.window.showInputBox(options),
  status(text, milliseconds) {
    vscode.window.setStatusBarMessage(text, milliseconds);
  },
  progress: (location, title, task) => vscode.window.withProgress({ location, title }, task),
  terminal(options, text) {
    const term = vscode.window.createTerminal({ name: options.name, cwd: options.cwd, env: options.env });
    term.show(true);
    term.sendText(text);
  },
  copy: (text) => vscode.env.clipboard.writeText(text),
  openExternal: (uri) => vscode.env.openExternal(uri),
  openFolder: (uri) => vscode.commands.executeCommand("vscode.openFolder", uri, { forceNewWindow: true }),
};

/** The one the extension uses; the end-to-end suite swaps in a scripted one. */
let current: Ui = vscodeUi;

export function ui(): Ui {
  return current;
}

export function useUi(next: Ui): void {
  current = next;
}
