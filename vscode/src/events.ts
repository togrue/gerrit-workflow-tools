// Gerrit's event stream, as `ger serve` follows it: while it can bring news, long-poll the
// activity sequence and refresh (debounced) when it moves. When the service has no stream,
// this asks once and stops; the poller then does what it always did.

import * as vscode from "vscode";

import { EventsStatus, GerClient } from "./api";
import { eventsMayCome } from "./format";
import { Model } from "./model";

/** How long one `GET /activity` waits on the service before it is asked again. */
const WAIT_SECONDS = 25;
/** Events come in bursts (a vote with comments, a push of a chain): one refresh for a burst. */
const DEBOUNCE_MS = 1000;

export class EventFollower implements vscode.Disposable {
  /** Where the stream is; undefined while the service has not said (or is not running). */
  events: EventsStatus | undefined;
  private readonly changed = new vscode.EventEmitter<void>();
  /** Fires when `events` changes state. */
  readonly onDidChange = this.changed.event;
  private readonly subscription: vscode.Disposable;
  private following: GerClient | undefined;
  /** The client whose service said it has no stream: not asked again. */
  private settled: GerClient | undefined;
  private debounce: ReturnType<typeof setTimeout> | undefined;
  private readonly abort = new AbortController();
  private disposed = false;

  constructor(
    private readonly model: Model,
    private readonly refresh: () => void,
    private readonly log: (line: string) => void,
  ) {
    // A service that (re)started answers once the model is ready: follow it from then.
    this.subscription = model.onDidChange(() => void this.follow());
    void this.follow();
  }

  get live(): boolean {
    return this.events?.state === "live";
  }

  private async follow(): Promise<void> {
    // A stale model has a service that failed to answer: the refresh that works restarts the follow.
    if (this.disposed || this.model.state !== "ready" || this.model.stale || this.following) {
      return;
    }
    let client: GerClient | undefined;
    try {
      client = await this.model.client();
      if (client === this.settled || this.following) {
        return;
      }
      this.following = client;
      let since: number | undefined;
      while (!this.disposed && this.model.state === "ready") {
        const answer = await client.activity(since, WAIT_SECONDS, this.abort.signal);
        this.update(answer.events);
        if (since !== undefined && answer.activity !== since) {
          this.schedule();
        }
        since = answer.activity;
        if (!eventsMayCome(answer.events)) {
          this.settled = client;
          return;
        }
      }
    } catch {
      // The service stopped or restarted; the next ready model follows the new one.
      this.update(undefined);
    } finally {
      if (this.following === client) {
        this.following = undefined;
      }
    }
  }

  private update(events: EventsStatus | undefined): void {
    // The abort at dispose ends the long poll in an error: that is not news, and the emitter is gone.
    if (this.disposed) {
      return;
    }
    if (events?.state === this.events?.state && events?.reason === this.events?.reason) {
      return;
    }
    this.events = events;
    if (events) {
      this.log(`Gerrit events: ${events.state}${events.reason ? ` (${events.reason})` : ""}${events.source ? ` — ${events.source}` : ""}`);
    }
    this.changed.fire();
  }

  private schedule(): void {
    if (this.disposed) {
      return;
    }
    clearTimeout(this.debounce);
    this.debounce = setTimeout(() => this.refresh(), DEBOUNCE_MS);
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.debounce);
    this.abort.abort();
    this.subscription.dispose();
    this.changed.dispose();
  }
}
