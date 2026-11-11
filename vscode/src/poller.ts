// Background refresh: every `ger.pollSeconds` while the window has focus, and when it regains
// focus. A changed setting applies at once, without a reload.

import * as vscode from "vscode";

import { setting } from "./config";

export class Poller implements vscode.Disposable {
  private timer: ReturnType<typeof setInterval> | undefined;
  private readonly subscriptions: vscode.Disposable[];

  constructor(private readonly refresh: () => void) {
    this.subscriptions = [
      // Coming back to the window is when the stack has most likely changed underneath.
      vscode.window.onDidChangeWindowState((state) => (state.focused ? this.refresh() : undefined)),
      vscode.workspace.onDidChangeConfiguration((event) => (event.affectsConfiguration("ger.pollSeconds") ? this.start() : undefined)),
    ];
    this.start();
  }

  private start(): void {
    clearInterval(this.timer);
    const seconds = Math.max(15, setting("pollSeconds", 120));
    // Unfocused, nobody is looking: the refresh on focus catches up.
    this.timer = setInterval(() => (vscode.window.state.focused ? this.refresh() : undefined), seconds * 1000);
  }

  dispose(): void {
    clearInterval(this.timer);
    this.subscriptions.forEach((subscription) => subscription.dispose());
  }
}
