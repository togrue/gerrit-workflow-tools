// Background refresh: every `ger.pollSeconds` while the window has focus, and when it regains
// focus (not while only the user can fix what is wrong). A changed setting applies at once, without a reload. While Gerrit's event stream is
// live the events bring the news, and the poll is only a slow safety net.

import * as vscode from "vscode";

import { setting } from "./config";
import { EventFollower } from "./events";

/** The poll while events are live: for what Gerrit sends no event about (the attention set). */
export const SAFETY_NET_SECONDS = 900;

/** Seconds between background refreshes, with or without live events. */
export function pollSeconds(live: boolean): number {
  const seconds = Math.max(15, setting("pollSeconds", 120));
  return live ? Math.max(seconds, SAFETY_NET_SECONDS) : seconds;
}

export class Poller implements vscode.Disposable {
  private timer: ReturnType<typeof setInterval> | undefined;
  private readonly subscriptions: vscode.Disposable[];
  private seconds: number | undefined;

  constructor(
    private readonly refresh: () => void,
    private readonly events: EventFollower,
    /** Whether the timer should skip this tick: there is nothing a refresh could fix until the user acts. */
    private readonly paused: () => boolean = () => false,
  ) {
    this.subscriptions = [
      // Coming back to the window is when the stack has most likely changed underneath.
      vscode.window.onDidChangeWindowState((state) => (state.focused ? this.refresh() : undefined)),
      vscode.workspace.onDidChangeConfiguration((event) => (event.affectsConfiguration("ger.pollSeconds") ? this.start() : undefined)),
      events.onDidChange(() => this.start()),
    ];
    this.start();
  }

  private start(): void {
    const seconds = pollSeconds(this.events.live);
    if (seconds === this.seconds && this.timer) {
      return;
    }
    clearInterval(this.timer);
    this.seconds = seconds;
    // Unfocused, nobody is looking: the refresh on focus catches up.
    this.timer = setInterval(() => (vscode.window.state.focused && !this.paused() ? this.refresh() : undefined), seconds * 1000);
  }

  dispose(): void {
    clearInterval(this.timer);
    this.subscriptions.forEach((subscription) => subscription.dispose());
  }
}
