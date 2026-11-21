// The `ger serve` API as this extension uses it. Contract: docu/spec/commands/serve.md.
// No `vscode` import here: the client is plain Node, so it can be tested without an editor.
//
// Every wire type comes from `contract.ts`, generated from src/gerrit_workflow_tools/server/contract.py
// (`uv run python scripts/generate_serve_contract.py`). Only what the client adds is written here.

import type {
  Activity,
  Chain,
  CommentChanged,
  CommentWritten,
  Diff,
  DiffQuery,
  EndpointKey,
  Endpoints,
  ErrorAnswer,
  Health,
  Hello,
  Holds,
  NewCommentState,
  Overview,
  Patchsets,
  Replied,
  ReviewDone,
  ReviewState,
  Rewritten,
  Runbook,
  Severity,
  ShowQuery,
  ShownCommit,
  Stack,
  StackMoves,
  StackStep,
  UpstreamChoice,
  UpstreamSet,
} from "./contract";

export * from "./contract";

/** Anything that carries attention reasons, and the severity core gave them. */
export interface Attended {
  attention_reasons: string[];
  severity: Severity;
}

/**
 * A commit as the Change view draws it: `/show`'s answer, or a stand-in built from what a row
 * said, until that answer arrives. A stand-in has no comments, resolution or new-comment state yet.
 */
export type ViewedCommit = Omit<ShownCommit, "comments" | "resolution" | "new_comment"> &
  Partial<Pick<ShownCommit, "comments" | "resolution" | "new_comment">>;

/** What `/show` adds to the commits `rev` names. */
export type ShowOptions = Omit<ShowQuery, "rev">;

/** What `/diff` compares instead of a commit with its parent. */
export type DiffOptions = Omit<DiffQuery, "rev">;

type Params = Record<string, string | string[] | number | boolean | undefined>;

/** One call as `call` takes it: its query, its body, the `{name}` segments of its path. */
interface Call<K extends EndpointKey> {
  query?: Endpoints[K]["query"];
  body?: Endpoints[K]["body"];
  path?: Record<string, string | number>;
  signal?: AbortSignal;
}

export class GerApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly reason?: string,
  ) {
    super(message);
    this.name = "GerApiError";
  }
}

export function isStack(local: Overview["local"] | undefined): local is Stack {
  return !!local && !("error" in local);
}

export class GerClient {
  constructor(readonly hello: Hello) {}

  get repo(): string {
    return this.hello.repo;
  }

  private url(path: string, params: Params = {}): string {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) {
      if (value === undefined || value === false) {
        continue;
      }
      for (const item of Array.isArray(value) ? value : [value]) {
        query.append(name, item === true ? "1" : String(item));
      }
    }
    const tail = query.toString();
    return `${this.hello.url}${this.hello.api}${path}${tail ? `?${tail}` : ""}`;
  }

  /** Make the call `endpoint` names (`GET /show`): its answer, or a `GerApiError`. */
  private async call<K extends EndpointKey>(endpoint: K, call: Call<K> = {}): Promise<Endpoints[K]["answer"]> {
    const [method, route] = endpoint.split(" ");
    const path = route.replace(/\{(\w+)\}/g, (_whole, name: string) => encodeURIComponent(String(call.path?.[name])));
    const headers: Record<string, string> = { Authorization: `Bearer ${this.hello.token}` };
    if (call.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(this.url(path, call.query as Params | undefined), {
      method,
      headers,
      body: call.body === undefined ? undefined : JSON.stringify(call.body),
      signal: call.signal,
    });
    const payload = (await response.json()) as Partial<ErrorAnswer> & Endpoints[K]["answer"];
    if (!response.ok) {
      const error = payload.error ?? { code: "INTERNAL", message: response.statusText };
      throw new GerApiError(error.code, error.message, response.status, error.reason);
    }
    return payload;
  }

  health(): Promise<Health> {
    return this.call("GET /health");
  }

  /** The activity sequence once it moves past `since`, or after `wait` seconds; at once without `since`. */
  activity(since: number | undefined, wait: number, signal?: AbortSignal): Promise<Activity> {
    return this.call("GET /activity", { query: { since, wait: since === undefined ? undefined : wait }, signal });
  }

  overview(refresh = false): Promise<Overview> {
    return this.call("GET /overview", { query: { refresh } });
  }

  chain(number: number): Promise<Chain> {
    return this.call("GET /chains/{number}", { path: { number } });
  }

  async show(revs: string[], options: ShowOptions = {}, signal?: AbortSignal): Promise<ShownCommit[]> {
    return (await this.call("GET /show", { query: { rev: revs, ...options }, signal })).commits;
  }

  diff(rev: string, options: DiffOptions = {}, signal?: AbortSignal): Promise<Diff> {
    return this.call("GET /diff", { query: { rev, ...options }, signal });
  }

  patchsets(rev: string, signal?: AbortSignal): Promise<Patchsets> {
    return this.call("GET /patchsets", { query: { rev }, signal });
  }

  /** What changed since the patchset you last reviewed. */
  reviewDelta(rev: string): Promise<Diff> {
    return this.call("GET /diff", { query: { rev, since: "reviewed" } });
  }

  async file(rev: string, path: string): Promise<string | null> {
    return (await this.call("GET /file", { query: { rev, path } })).content;
  }

  async review(): Promise<ReviewState | null> {
    return (await this.call("GET /review")).review;
  }

  async reviewOpen(change: number | string): Promise<ReviewState> {
    return (await this.call("POST /review/open", { body: { change } })).review;
  }

  reviewDone(): Promise<ReviewDone> {
    return this.call("POST /review/done", { body: {} });
  }

  /** Your draft reply to a chain: created, or the one you had there updated. */
  reply(rev: string, commentId: string, message: string, resolved?: boolean): Promise<Replied> {
    return this.call("POST /comments/reply", { body: { rev, comment_id: commentId, message, resolved } });
  }

  /** The agent runbook for the local stack, or for one of its commits. Fetches CI logs. */
  runbook(rev?: string): Promise<Runbook> {
    return this.call("GET /agent/runbook", { query: { rev } });
  }

  /** Start a comment chain at `location` (`path`, `path:line`, `path:start-end`, lines from 1). */
  newComment(rev: string, location: string, message: string, state: NewCommentState): Promise<CommentWritten> {
    return this.call("POST /comments/new", { body: { rev, location, message, state } });
  }

  /** Give one of your unpublished comments (a draft, a local draft or a note) a new text; `message` is what it said. */
  editComment(rev: string, commentId: string, message: string): Promise<CommentChanged> {
    return this.call("POST /comments/edit", { body: { rev, comment_id: commentId, message } });
  }

  /** Delete one of your unpublished comments; `message` is what it said. */
  discardComment(rev: string, commentId: string): Promise<CommentChanged> {
    return this.call("POST /comments/discard", { body: { rev, comment_id: commentId } });
  }

  /** The working branch's upstream and the one to suggest. */
  upstream(): Promise<UpstreamChoice> {
    return this.call("GET /stack/upstream");
  }

  /** Set the working branch's upstream (a remote-tracking branch, `origin/main`). */
  setUpstream(upstream: string): Promise<UpstreamSet> {
    return this.call("POST /stack/upstream", { body: { upstream } });
  }

  hold(rootId: string, held: boolean): Promise<Holds> {
    return this.call("POST /comments/hold", { body: { root_id: rootId, held } });
  }

  /** Which neighbours of the local stack can swap places. Touches nothing. */
  moves(): Promise<StackMoves> {
    return this.call("GET /stack/moves");
  }

  /** Rewrite the local stack; `steps` are oldest first. All or nothing. `autostash` stashes uncommitted changes for it. */
  rewrite(steps: StackStep[], autostash = false): Promise<Rewritten> {
    return this.call("POST /stack/rewrite", { body: { steps, autostash } });
  }

  /** Give one commit of the local stack a new commit message. Nothing else changes. */
  reword(rev: string, message: string): Promise<Rewritten> {
    return this.call("POST /stack/reword", { body: { rev, message } });
  }

  /** Take a rewrite back, while the branch is still where it left it. */
  undo(head: string, previousHead: string): Promise<Stack> {
    return this.call("POST /stack/undo", { body: { head, previous_head: previousHead } });
  }
}
