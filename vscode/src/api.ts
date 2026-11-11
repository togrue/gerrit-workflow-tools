// The `ger serve` API as this extension uses it. Contract: docu/spec/commands/serve.md.
// No `vscode` import here: the client is plain Node, so it can be tested without an editor.

export interface Hello {
  url: string;
  api: string;
  token: string;
  repo: string;
  pid: number;
}

export interface ApiError {
  code: string;
  message: string;
}

export interface Member {
  number: number;
  change_id: string;
  subject: string;
  verified: number | null;
  code_review: number | null;
  comments_unresolved: number;
  attention_reasons: string[];
  url: string | null;
  /** The local stack commit carrying this Change-Id, if there is one. */
  local_sha: string | null;
}

export interface Chain {
  key: string;
  top: { number: number; change_id: string; subject: string; url: string | null };
  project: string;
  branch: string;
  owner: { name: string; email: string | null };
  depth: number;
  wait_age_seconds: number;
  unreviewed_age_seconds: number;
  last_activity: string | null;
  verified: number | null;
  code_review: number | null;
  comments_unresolved: number;
  attention_reasons: string[];
  partial_chain: boolean;
  members: Member[];
  local?: boolean;
}

export type SectionName = "to-review" | "outgoing";

export interface Section {
  name: SectionName;
  query: string;
  summary: Record<string, number>;
  chains: Chain[];
}

export interface Headline {
  section: SectionName;
  reason: string;
  chain: string;
  number: number;
  subject: string;
  url: string | null;
  text: string;
}

export interface CiLink {
  label: string;
  url: string;
  source: string;
}

/** The overlay state of one commit: the fields `ger log --json` and `ger show --json` share. */
export interface CommitStatus {
  sha: string | null;
  short_sha: string;
  change_id: string | null;
  summary: string;
  pushed: boolean;
  patchset_status: string;
  verified: number | null;
  code_review: number | null;
  comments_unresolved: number;
  ci_failures: string[];
  ci_links: CiLink[];
  gerrit_url: string | null;
  /** The Gerrit change number; null before the first push. */
  number: number | null;
  /** The pushed patchset's votes while this commit is ahead of it with other code (its own votes are then null). */
  last_pushed_votes: { verified: number | null; code_review: number | null } | null;
  submittable: boolean;
  work_in_progress: boolean;
  attention_reasons: string[];
  change_status: string | null;
  updated: string | null;
}

export interface StackCommit extends CommitStatus {
  sha: string;
  abandoned: boolean;
  reviewers: string[];
  is_head: boolean;
  held_back: boolean;
  needs_edit: boolean;
}

export interface Stack {
  branch: string | null;
  head: string | null;
  rebase_in_progress: boolean;
  stack: { project: string; target_branch: string; push_branch: string };
  summary: { commits: number; ready: number; attention: number; needs_edit: number; not_on_gerrit: number };
  commits: StackCommit[];
}

/** Why two neighbours of the local stack cannot swap places. */
export interface MoveConflict {
  sha: string;
  summary: string;
  files: string[];
}

/** The conflict preview: one entry per adjacent pair of the stack, oldest first. */
export interface StackMoves {
  head: string | null;
  /** False when this git cannot simulate the moves: nothing is known. */
  available: boolean;
  swaps: { lower: string; upper: string; conflict: MoveConflict | null }[];
}

export interface Overview {
  host: string;
  generated?: string;
  headline?: Headline | null;
  sections?: Section[];
  /** Set instead of `sections` when the inbox could not be loaded. */
  error?: ApiError;
  /** `needs_upstream`: the stack could not be shown because the branch has no upstream. */
  local: Stack | { error: ApiError; needs_upstream?: boolean };
}

/** The working branch's upstream, and which to offer: the best guess first. */
export interface UpstreamChoice {
  branch: string | null;
  upstream: string | null;
  suggested: string | null;
  why: string | null;
  candidates: string[];
}

/** What a new comment becomes: a draft on Gerrit, or kept here (a local draft or a private note). */
export type NewCommentState = "draft" | "local-draft" | "note";

export interface Comment {
  path: string;
  line: number | null;
  message: string;
  author?: string;
  comment_id?: string;
  patch_set: number | null;
  state: "note" | "local-draft" | "draft" | "published";
  url: string | null;
}

export interface CommentChain {
  root_id: string;
  path: string;
  line: number | null;
  start_line: number | null;
  url: string | null;
  resolved: boolean;
  resolved_after_publish: boolean;
  outdated: boolean;
  /** On hold: you keep this chain for yourself, the agent runbook leaves it alone. */
  held: boolean;
  comments: Comment[];
}

export interface CiFinding {
  tool: string;
  kind: string;
  path: string | null;
  line: number | null;
  column: number | null;
  message: string;
  detail: string | null;
  fingerprint: string;
}

export interface CiContent {
  label: string;
  url: string;
  provenance: {
    url: string;
    provider: string | null;
    build: string | null;
    fetched_at: string | null;
    truncated: boolean;
    cached: boolean;
    error: string | null;
  };
  findings: CiFinding[];
}

export interface ShownCommit extends CommitStatus {
  local_commit: boolean;
  comment_chains: CommentChain[];
  ci_content?: CiContent[];
  /** Fingerprints of the findings this change introduced; null when that cannot be told. */
  ci_introduced?: string[] | null;
  /** What a new comment on it becomes by default: a draft where Gerrit has this commit, else a local draft. */
  new_comment?: "draft" | "local-draft";
}

export interface FileChange {
  path: string;
  status: string;
  old_path: string | null;
  insertions: number | null;
  deletions: number | null;
}

export interface Diff {
  commit: string;
  /** Null only in a review delta with nothing to compare against. */
  base: string | null;
  subject?: string;
  local_commit: boolean;
  files: FileChange[];
  /** Present in a review delta: the patchset you last reviewed against the current one. */
  since?: { patch_set: number | null; current_patch_set: number; up_to_date: boolean; rebased: boolean };
}

export interface ReviewState {
  number: number;
  patch_set: number;
  subject: string;
  staged: boolean;
  worktree: string;
  branch: string;
}

/** The attention walk as work for an agent: Markdown to follow, and what is in it. */
export interface Runbook {
  branch: string | null;
  commits: {
    sha: string;
    short_sha: string;
    summary: string;
    comments: number;
    held: number;
    findings: number;
    inherited: number;
    agent_work: boolean;
  }[];
  totals: { commits: number; comments: number; held: number; findings: number };
  markdown: string;
}

/** A commit id (kept as it is), or what to do with it: fold into the commit below, or drop. */
export type StackStep = string | { sha: string; action: "pick" | "fixup" | "fixup-take-message" | "drop" };

export interface ShowOptions {
  stack?: boolean;
  chain?: boolean;
  ci?: boolean;
  comments?: "unresolved" | "all" | "resolved";
  context?: number;
}

type Params = Record<string, string | string[] | number | boolean | undefined>;

export class GerApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
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

  private async call<T>(method: "GET" | "POST", path: string, params?: Params, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.hello.token}` };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(this.url(path, params), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = (await response.json()) as { error?: ApiError } & T;
    if (!response.ok) {
      const error = payload.error ?? { code: "INTERNAL", message: response.statusText };
      throw new GerApiError(error.code, error.message, response.status);
    }
    return payload;
  }

  health(): Promise<{ name: string; version: string; api: number; repo: string | null; branch: string | null }> {
    return this.call("GET", "/health");
  }

  overview(refresh = false): Promise<Overview> {
    return this.call("GET", "/overview", { refresh });
  }

  chain(number: number): Promise<Chain> {
    return this.call("GET", `/chains/${number}`);
  }

  async show(revs: string[], options: ShowOptions = {}): Promise<ShownCommit[]> {
    const answer = await this.call<{ commits: ShownCommit[] }>("GET", "/show", { rev: revs, ...options });
    return answer.commits;
  }

  diff(rev: string): Promise<Diff> {
    return this.call("GET", "/diff", { rev });
  }

  /** What changed since the patchset you last reviewed. */
  reviewDelta(rev: string): Promise<Diff> {
    return this.call("GET", "/diff", { rev, since: "reviewed" });
  }

  async file(rev: string, path: string): Promise<string | null> {
    return (await this.call<{ content: string | null }>("GET", "/file", { rev, path })).content;
  }

  async review(): Promise<ReviewState | null> {
    return (await this.call<{ review: ReviewState | null }>("GET", "/review")).review;
  }

  async reviewOpen(change: number | string): Promise<ReviewState> {
    return (await this.call<{ review: ReviewState }>("POST", "/review/open", undefined, { change })).review;
  }

  reviewDone(): Promise<{ done: number; deleted: string[]; kept: string[] }> {
    return this.call("POST", "/review/done", undefined, {});
  }

  /** Your draft reply to a chain: created, or the one you had there updated. */
  reply(
    rev: string,
    commentId: string,
    message: string,
    resolved?: boolean,
  ): Promise<{ root_id: string; reply: { action: "created" | "updated"; replaced_message: string | null } }> {
    return this.call("POST", "/comments/reply", undefined, { rev, comment_id: commentId, message, resolved });
  }

  /** The agent runbook for the local stack, or for one of its commits. Fetches CI logs. */
  runbook(rev?: string): Promise<Runbook> {
    return this.call("GET", "/agent/runbook", { rev });
  }

  /** Start a comment chain at `location` (`path`, `path:line`, `path:start-end`, lines from 1). */
  newComment(rev: string, location: string, message: string, state: NewCommentState): Promise<{ state: NewCommentState; id: string | null }> {
    return this.call("POST", "/comments/new", undefined, { rev, location, message, state });
  }

  /** The working branch's upstream and the one to suggest. */
  upstream(): Promise<UpstreamChoice> {
    return this.call("GET", "/stack/upstream");
  }

  /** Set the working branch's upstream (a remote-tracking branch, `origin/main`). */
  setUpstream(upstream: string): Promise<{ branch: string; upstream: string }> {
    return this.call("POST", "/stack/upstream", undefined, { upstream });
  }

  hold(rootId: string, held: boolean): Promise<{ holds: string[] }> {
    return this.call("POST", "/comments/hold", undefined, { root_id: rootId, held });
  }

  /** Which neighbours of the local stack can swap places. Touches nothing. */
  moves(): Promise<StackMoves> {
    return this.call("GET", "/stack/moves");
  }

  /** Rewrite the local stack; `steps` are oldest first. All or nothing. */
  rewrite(steps: StackStep[]): Promise<Stack & { previous_head: string }> {
    return this.call("POST", "/stack/rewrite", undefined, { steps });
  }

  /** Take a rewrite back, while the branch is still where it left it. */
  undo(head: string, previousHead: string): Promise<Stack> {
    return this.call("POST", "/stack/undo", undefined, { head, previous_head: previousHead });
  }
}
