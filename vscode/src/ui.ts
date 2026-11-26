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

/** A reply box: an input box with Gerrit's Resolved choice, as the Change view's reply box has it. */
export interface ReplyOptions {
  title: string;
  placeHolder?: string;
  value?: string;
  /** Where Resolved starts: where the comment stands once your drafts are published. */
  resolved: boolean;
  /** The line under the box, saying what Enter saves with Resolved as it is. */
  prompt(resolved: boolean): string;
}

/** What a reply box gave: the text, and whether the reply resolves the comment. */
export interface Reply {
  text: string;
  resolved: boolean;
}

/** The Resolved toggle in a reply box's title bar, as the editor thread's header has it. */
export const RESOLVED_TOGGLE = {
  on: { icon: "pass-filled", tooltip: "Resolved: Yes (click to leave it unresolved; Ctrl+Shift+Enter saves and resolves)" },
  off: { icon: "circle-large-outline", tooltip: "Resolved: No (click to resolve it with your reply; Ctrl+Shift+Enter saves and resolves)" },
};

/** A picker's options; `active` is the item it opens on (the first one otherwise). */
export type PickOptions<T> = vscode.QuickPickOptions & { active?: T };

export interface Ui {
  /** A notification (or a modal dialog) with buttons; resolves to the button pressed. */
  message(level: Level, text: string, options: MessageOptions, buttons: string[]): Thenable<string | undefined>;
  pick<T extends vscode.QuickPickItem>(items: T[], options: PickOptions<T>): Thenable<T | undefined>;
  input(options: vscode.InputBoxOptions): Thenable<string | undefined>;
  /** An input box for a reply, with a Resolved toggle in its title bar; Ctrl+Shift+Enter saves and resolves. */
  reply(options: ReplyOptions): Thenable<Reply | undefined>;
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
  /**
   * Open `content` as an untitled document in `language`, to read and to edit. Resolves to what reads its text as it is
   * by then; once the document is closed, as the user last left it (saved to a file, say), never the original again.
   * Undefined when it was closed without saving: VS Code empties it first, so what the user made of it is gone.
   */
  document(content: string, language: string): Thenable<() => string | undefined>;
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
  pick(items, options) {
    const active = options.active;
    if (!active) {
      return vscode.window.showQuickPick(items, options);
    }
    // showQuickPick always opens on the first item: a picker of our own opens on the one asked for.
    return new Promise((resolve) => {
      const picker = vscode.window.createQuickPick<(typeof items)[number]>();
      [picker.items, picker.activeItems] = [items, [active]];
      [picker.title, picker.placeholder] = [options.title, options.placeHolder];
      [picker.matchOnDescription, picker.matchOnDetail] = [options.matchOnDescription ?? false, options.matchOnDetail ?? false];
      picker.ignoreFocusOut = options.ignoreFocusOut ?? false;
      picker.onDidAccept(() => {
        resolve(picker.selectedItems[0] ?? picker.activeItems[0]);
        picker.hide();
      });
      picker.onDidHide(() => {
        resolve(undefined);
        picker.dispose();
      });
      picker.show();
    });
  },
  input: (options) => vscode.window.showInputBox(options),
  reply(options) {
    return new Promise((resolve) => {
      const box = vscode.window.createInputBox();
      let resolved = options.resolved;
      let done = false;
      const draw = () => {
        const toggle = resolved ? RESOLVED_TOGGLE.on : RESOLVED_TOGGLE.off;
        box.buttons = [{ iconPath: new vscode.ThemeIcon(toggle.icon), tooltip: toggle.tooltip }];
        box.prompt = options.prompt(resolved);
      };
      [box.title, box.placeholder, box.value] = [options.title, options.placeHolder, options.value ?? ""];
      // What you typed is not lost to a click elsewhere (the toggle, the code you answer about): Escape cancels.
      box.ignoreFocusOut = true;
      draw();
      const accept = (resolving: boolean) => {
        done = true;
        resolve({ text: box.value, resolved: resolving });
        box.hide();
      };
      box.onDidTriggerButton(() => {
        resolved = !resolved;
        draw();
      });
      box.onDidAccept(() => accept(resolved));
      box.onDidHide(() => {
        replyBox = undefined;
        void vscode.commands.executeCommand("setContext", "ger.replyBox", false);
        if (!done) {
          resolve(undefined);
        }
        box.dispose();
      });
      replyBox = () => accept(true);
      void vscode.commands.executeCommand("setContext", "ger.replyBox", true);
      box.show();
    });
  },
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
    // A closed document's text is gone, and what the user took out of it must stay out: keep the text as it last was.
    // Closing without saving empties the document before it closes (still dirty, as an edit would be), so a closed
    // empty document gives nothing rather than the original or an earlier version.
    let last = content;
    const listeners = [
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.document === document) {
          last = document.getText();
        }
      }),
      vscode.workspace.onDidCloseTextDocument((closed) => {
        if (closed === document) {
          listeners.forEach((listener) => listener.dispose());
        }
      }),
    ];
    return () => (!document.isClosed ? document.getText() : last || undefined);
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

/** Saves the reply box open now, resolving the comment (Ctrl+Shift+Enter); undefined while none is open. */
let replyBox: (() => void) | undefined;

/** Ctrl+Shift+Enter in a reply box: save the reply, and resolve the comment with it. */
export function saveReplyResolved(): void {
  replyBox?.();
}

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

/** A way on a failure offers: its button's words and the command it runs (a `StackAction` of stackRows.ts). */
export interface WayOn {
  label: string;
  command: { id: string; args: unknown[] };
}

/**
 * Say that something did not happen, with the ways on as the notification's buttons; the one pressed
 * runs. P10 and Q8: the state is said as a failure, with its own icon and words, and the options.
 */
export async function offerWaysOn(level: Level, text: string, ways: readonly WayOn[]): Promise<void> {
  const pressed = await ui().message(level, text, {}, ways.map((way) => way.label));
  const way = ways.find((candidate) => candidate.label === pressed);
  if (way) {
    await vscode.commands.executeCommand(way.command.id, ...way.command.args);
  }
}
