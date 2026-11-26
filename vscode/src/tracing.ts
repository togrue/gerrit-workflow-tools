// Switches the inspection points of trace.ts on and off (`ger.trace`, GER_TRACE), and hooks them
// to what VS Code shows: an editor opening, the cursor moving to a comment, a terminal, the
// Review chains tree being drawn, a picker or a status message. Off, nothing is wrapped.

import * as path from "node:path";
import * as vscode from "vscode";

import { setting } from "./config";
import { Tracer, activeTracer, fileSink, traced, useTracer } from "./trace";
import type { Ui } from "./ui";

/** Where the trace goes: GER_TRACE, else `ger.trace`; empty when off. */
export function traceFile(): string {
  return process.env.GER_TRACE || setting("trace", "");
}

/** Trace while `traceFile()` names a file, following the setting. */
export function startTracing(log: (line: string) => void): vscode.Disposable {
  let file = "";
  let close: () => void = () => undefined;
  const apply = () => {
    const wanted = traceFile();
    if (wanted === file) {
      return;
    }
    close();
    file = wanted;
    if (!file) {
      useTracer(undefined);
      close = () => undefined;
      log("Tracing is off.");
      return;
    }
    const sink = fileSink(file);
    useTracer(new Tracer(sink));
    close = sink.close;
    log(`Tracing every user action to ${file}.`);
  };
  apply();
  const shown = (what: string) => activeTracer()?.visible(what);
  const name = (uri: vscode.Uri) => `${uri.scheme}:${path.basename(uri.path)}`;
  const subscriptions = [
    vscode.workspace.onDidChangeConfiguration((event) => event.affectsConfiguration("ger.trace") && apply()),
    vscode.window.onDidChangeActiveTextEditor((editor) => editor && shown(`editor:${name(editor.document.uri)}`)),
    vscode.window.onDidChangeTextEditorSelection((event) => shown(`cursor:${name(event.textEditor.document.uri)}`)),
    vscode.window.tabGroups.onDidChangeTabs(() => shown("tab")),
    vscode.window.onDidOpenTerminal((terminal) => shown(`terminal:${terminal.name}`)),
  ];
  return new vscode.Disposable(() => {
    subscriptions.forEach((subscription) => subscription.dispose());
    close();
    useTracer(undefined);
  });
}

/** A command, traced as one user action while tracing is on. */
export function tracedCommand(name: string, run: (...args: unknown[]) => unknown): (...args: unknown[]) => unknown {
  return (...args) => traced(name, "command", () => run(...args));
}

/** The Review chains tree: VS Code asking for its rows is when it draws them. */
export function tracedTree<T>(provider: vscode.TreeDataProvider<T>): vscode.TreeDataProvider<T> {
  return new Proxy(provider, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver);
      if (property === "getChildren" && typeof value === "function") {
        return (element?: T) => {
          activeTracer()?.visible("tree");
          return value.call(target, element);
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

/** The answer to `asked`: once it comes, the work after it is the user action of answering. */
function waitingOnUser<R>(what: string, asked: () => Thenable<R>): Thenable<R> {
  const tracer = activeTracer();
  if (!tracer) {
    return asked();
  }
  tracer.visible(`ui:${what}`);
  const answered = tracer.waitForUser();
  return asked().then(
    (value) => {
      answered(what);
      return value;
    },
    (error: unknown) => {
      answered(what);
      throw error;
    },
  );
}

function showing<R>(what: string, show: () => R): R {
  activeTracer()?.visible(`ui:${what}`);
  return show();
}

/**
 * What the user sees of `ui`: a prompt shows, and the action waits on the user until it is
 * answered (a notification only when it has buttons to answer with); a status message, a
 * progress, a terminal, a document show.
 */
export function tracedUi(ui: Ui): Ui {
  return {
    message: (level, text, options, buttons) =>
      buttons.length || options.modal ? waitingOnUser("message", () => ui.message(level, text, options, buttons)) : showing("message", () => ui.message(level, text, options, buttons)),
    pick: (items, options) => waitingOnUser("pick", () => ui.pick(items, options)),
    input: (options) => waitingOnUser("input", () => ui.input(options)),
    reply: (options) => waitingOnUser("reply", () => ui.reply(options)),
    status: (text, milliseconds) => showing("status", () => ui.status(text, milliseconds)),
    progress: (location, title, task) => showing("progress", () => ui.progress(location, title, task)),
    terminal: (options, text) => showing("terminal", () => ui.terminal(options, text)),
    copy: (text) => showing("copy", () => ui.copy(text)),
    openExternal: (uri) => showing("browser", () => ui.openExternal(uri)),
    openFolder: (uri) => ui.openFolder(uri),
    document: (content, language) => showing("document", () => ui.document(content, language)),
    save: (name, content) => waitingOnUser("save", () => ui.save(name, content)),
  };
}
