// Everything the extension says to the user or does outside its own views: notifications,
// pickers, input boxes, the status bar, progress, terminals, the clipboard, the browser, new
// windows, untitled documents and files saved where the user says. One seam, so the end-to-end
// suite can answer each prompt and record what a user would have seen and what would have happened.

import * as os from "node:os";
import * as path from "node:path";
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
  /**
   * Run the shell command line `text` in the terminal called `options.name`, and show it. One
   * terminal per name, reused: a new one only when there is none, it was closed or its shell
   * exited, or a command still runs in it (typing there would answer that command's prompt).
   */
  terminal(options: TerminalOptions, text: string): void;
  copy(text: string): Thenable<void>;
  openExternal(uri: vscode.Uri): Thenable<boolean>;
  openFolder(uri: vscode.Uri): Thenable<unknown>;
  /** Open `content` as an untitled document in `language`, to read and to edit. Resolves to what reads its text as it is by then. */
  document(content: string, language: string): Thenable<() => string>;
  /** Ask where to save `content`, suggesting the file name `name`, and write it there. Resolves to the file's path; undefined when the user cancelled. */
  save(name: string, content: string): Thenable<string | undefined>;
}

/** What VS Code 1.93+ tells about commands run in a terminal (shell integration); absent before. */
interface ShellExecutionEvents {
  onDidStartTerminalShellExecution?: vscode.Event<{ terminal: vscode.Terminal }>;
  onDidEndTerminalShellExecution?: vscode.Event<{ terminal: vscode.Terminal }>;
}

/** The terminals `Ui.terminal` reuses, one per name, and which of them run a command right now. */
class Terminals {
  private readonly byName = new Map<string, vscode.Terminal>();
  private readonly busy = new Set<vscode.Terminal>();
  private watching = false;

  take(options: TerminalOptions): vscode.Terminal {
    this.watch();
    const known = this.byName.get(options.name);
    const open = known && known.exitStatus === undefined && vscode.window.terminals.includes(known);
    if (known && open && !this.busy.has(known)) {
      return known;
    }
    // Without shell integration a running command is not seen, and the terminal is reused.
    const term = vscode.window.createTerminal({ name: options.name, cwd: options.cwd, env: options.env });
    this.byName.set(options.name, term);
    return term;
  }

  private watch(): void {
    if (this.watching) {
      return;
    }
    this.watching = true;
    const events = vscode.window as unknown as ShellExecutionEvents;
    events.onDidStartTerminalShellExecution?.((event) => this.busy.add(event.terminal));
    events.onDidEndTerminalShellExecution?.((event) => this.busy.delete(event.terminal));
    vscode.window.onDidCloseTerminal((closed) => {
      this.busy.delete(closed);
      for (const [name, term] of this.byName) {
        if (term === closed) {
          this.byName.delete(name);
        }
      }
    });
  }
}

const terminals = new Terminals();

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
    const term = terminals.take(options);
    term.show(true);
    term.sendText(text);
  },
  copy: (text) => vscode.env.clipboard.writeText(text),
  openExternal: (uri) => vscode.env.openExternal(uri),
  openFolder: (uri) => vscode.commands.executeCommand("vscode.openFolder", uri, { forceNewWindow: true }),
  async document(content, language) {
    const document = await vscode.workspace.openTextDocument({ language, content });
    await vscode.window.showTextDocument(document, { preview: false });
    return () => (document.isClosed ? content : document.getText());
  },
  async save(name, content) {
    const extension = path.extname(name).slice(1);
    // Suggested in the home folder, not in the workspace: a report is not part of the repository.
    const uri = await vscode.window.showSaveDialog({ defaultUri: vscode.Uri.file(path.join(os.homedir(), name)), filters: extension ? { [extension.toUpperCase()]: [extension] } : undefined });
    if (!uri) {
      return undefined;
    }
    await vscode.workspace.fs.writeFile(uri, Buffer.from(content, "utf8"));
    return uri.fsPath;
  },
};

/** The one the extension uses; the end-to-end suite swaps in a scripted one. */
let current: Ui = vscodeUi;

export function ui(): Ui {
  return current;
}

export function useUi(next: Ui): void {
  current = next;
}

/** How long `feedback` stays in the status bar. */
export const FEEDBACK_MS = 4000;

/**
 * Say in the status bar, for a few seconds, what an action just did ("Copied Change-Id I6e95…",
 * "Chains sorted by last activity"), so every click visibly lands. `icon` is a codicon name:
 * `check` for done, `terminal` for a command that now runs in a terminal.
 */
export function feedback(text: string, options: { icon?: string; milliseconds?: number } = {}): void {
  ui().status(`$(${options.icon ?? "check"}) ${text}`, options.milliseconds ?? FEEDBACK_MS);
}
