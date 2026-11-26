// The `ger serve` API as this extension uses it. Contract: docu/spec/commands/serve.md.
// No `vscode` import here: the client is plain Node, so it can be tested without an editor.

import { activeTracer } from "./trace";

export interface Hello {
  url: string;
  api: string;
  token: string;
  repo: string;
  pid: number;
}

/** `GET /health`: who answers. `api` is the serve API version, `MAJOR.MINOR.PATCH` (a bare number from an older ger). */
export type Health = {
  name: string;
  version: string;
  api: string | number;
  repo: string | null;
  branch: string | null;
  /** Seconds Gerrit is given to answer a connection before ger says it does not answer (API 1.8). */
  gerrit_wait?: number;
  /** What ger warns about in this repository's configuration (old key names), and the ger command that fixes it (`ger` left out). */
  warnings?: { message: string; argv: string[] | null }[];
} & Activity;

export interface ApiError {
  code: string;
  message: string;
  /** A word for a refusal the client can do something about (`uncommitted-changes`). */
  reason?: string;
}

/** Where Gerrit's event stream is, as `ger serve` follows it. */
export interface EventsStatus {
  state: "off" | "connecting" | "live" | "retrying" | "unavailable";
  /** Why it is not live; null while it is. */
  reason: string | null;
  /** `ssh://user@host:port`, or null when there is nothing to connect to. */
  source: string | null;
}

/** The activity sequence: it goes up whenever a view should look again. */
export interface Activity {
  activity: number;
  events: EventsStatus;
}

/** How urgent a change's attention reasons are, as core says (`core/attention.py`, `REASON_SEVERITY`). */
export type Severity = "error" | "warning" | "pending" | "ok";

/** Anything that carries attention reasons, and the severity core gave them. */
export interface Attended {
  attention_reasons: string[];
  severity: Severity;
}

export interface Member {
  number: number;
  change_id: string;
  subject: string;
  verified: number | null;
  code_review: number | null;
  comments_unresolved: number;
  /** Of `comments_unresolved`, the chains whose last published comment is yours: they wait on the other side. */
  comments_awaiting_reply?: number;
  /** Of `comments_unresolved`, the chains a draft of yours answers: they wait on your push (API 1.4). */
  comments_drafted?: number;
  /** Of `comments_unresolved`, what is left to answer: neither waiting on the other side nor drafted (API 1.4). */
  comments_to_answer?: number;
  /** Every comment chain, resolved or not: the share `comments_unresolved` is of; null when there are some, all resolved, not counted (API 1.5). */
  comments_total?: number | null;
  attention_reasons: string[];
  severity: Severity;
  url: string | null;
  /** The local stack commit carrying this Change-Id, if there is one. */
  local_sha: string | null;
  /** The commit of the current patch set on Gerrit. */
  sha: string | null;
  /** Somebody else's change whose turn is yours: Gerrit's attention set holds you (else: not reviewed since its last patchset). */
  your_turn?: boolean;
  /** Under To review, a change of the chain the section's query left out, and why: `not verified yet`, `WIP`, `private`, `not asked of you`. */
  not_ready?: string | null;
  /** Your non-zero votes on its current patchset, by label (API 1.6). */
  your_votes?: Record<string, number>;
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
  /** That last activity was yours (your upload or your vote, as Gerrit tells it): not news. */
  last_activity_yours?: boolean;
  verified: number | null;
  code_review: number | null;
  comments_unresolved: number;
  comments_awaiting_reply?: number;
  comments_drafted?: number;
  comments_to_answer?: number;
  attention_reasons: string[];
  severity: Severity;
  partial_chain: boolean;
  /** Per label, the worst vote cast on a member (`verified`, `code_review` above count a missing one as worse), and how many members have none. */
  votes_cast?: { verified: number | null; code_review: number | null };
  votes_missing?: { verified: number; code_review: number };
  /** Under To review: a member's turn is yours. False: you reviewed it, and it waits on its author. */
  your_turn?: boolean;
  members: Member[];
  local?: boolean;
}

export type SectionName = "to-review" | "outgoing";

export interface Section {
  name: SectionName;
  query: string;
  /** Counts over the chains that want you (To review: those whose turn is yours); To review adds `waiting_on_author`. */
  summary: Record<string, number>;
  chains: Chain[];
}

/** The headline: the one thing that most wants you, as core picks it. */
export type Headline = ChainHeadline | RebaseHeadline;

/** A change of a review chain in an inbox section. */
export interface ChainHeadline {
  section: SectionName;
  reason: string;
  /** The reason's severity; `pending` for one waiting on your review. */
  severity: Severity;
  chain: string;
  number: number;
  subject: string;
  url: string | null;
  text: string;
}

/** A rebase underway in the checkout: while there is one, core makes it the headline. */
export interface RebaseHeadline {
  section: "local";
  reason: "rebase";
  /** `error` for a conflict, else `warning`. */
  severity: Severity;
  /** As `rebase.stop` of `GET /stack`. */
  stop: RebaseStop | null;
  chain: null;
  /** The commit it stopped at: its change number, null when it is not on Gerrit or there is no such commit. */
  number: number | null;
  subject: string | null;
  sha: string | null;
  url: string | null;
  /** Stopped on a fixup folding into that commit: the stop is about it (API 1.2). */
  folding?: boolean;
  text: string;
}

export interface CiLink {
  label: string;
  url: string;
  source: string;
  /** What the build reported: an unstable build is fetched and parsed like a failed one. */
  verdict?: "failed" | "unstable";
}

/** Someone Gerrit lists on a change: a reviewer, or (`cc`) only copied. */
export interface Reviewer {
  name: string;
  cc: boolean;
}

export type CodeChange = "rebased" | "changed" | "unknown";

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
  /** Of `comments_unresolved`, the chains whose last published comment is yours: they wait on the other side. */
  comments_awaiting_reply?: number;
  /** Of `comments_unresolved`, the chains a draft of yours answers: they wait on your push (API 1.4). */
  comments_drafted?: number;
  /** Of `comments_unresolved`, what is left to answer: neither waiting on the other side nor drafted (API 1.4). */
  comments_to_answer?: number;
  /** Every comment chain, resolved or not: the share `comments_unresolved` is of; null when there are some, all resolved, not counted (API 1.5). */
  comments_total?: number | null;
  ci_failures: string[];
  ci_links: CiLink[];
  gerrit_url: string | null;
  /** The Gerrit change number; null before the first push. */
  number: number | null;
  /** The number of Gerrit's current patchset; null before the first push, absent from an older ger or a Review chains member. */
  current_patch_set?: number | null;
  /** The pushed patchset's votes while this commit is ahead of it with other code (its own votes are then null). */
  last_pushed_votes: { verified: number | null; code_review: number | null } | null;
  /**
   * `newer` only, else null: core's verdict on whether its code changed since the pushed patchset
   * (`changed`), or it is only rebased and keeps its votes (`rebased`); `unknown` when it cannot tell.
   */
  code_change: CodeChange | null;
  submittable: boolean;
  work_in_progress: boolean;
  attention_reasons: string[];
  severity: Severity;
  change_status: string | null;
  updated: string | null;
  /** Who is on the change; absent where the source does not say (a Review chains member). */
  reviewers?: Reviewer[];
}

export interface StackCommit extends CommitStatus {
  sha: string;
  abandoned: boolean;
  reviewers: Reviewer[];
  is_head: boolean;
  held_back: boolean;
  /** Below the ready boundary without a valid Change-Id: `ger push` gives it one and sends it as a new change; the local commit changes (API 1.8). */
  adds_change_id?: boolean;
  needs_edit: boolean;
  /** `newer`, and Gerrit's current patchset is a commit this repository never had: it came from elsewhere. */
  diverged?: boolean;
  /** A merge commit: one stack commit, whatever it brings in. */
  merge?: boolean;
  /** The newest merge or below it: git rebase cannot replay a merge, so core leaves it where it is. */
  pinned?: boolean;
  /** Where a rebase underway stands on this commit; null without one, or when it does not replay it. */
  rebase?: "done" | "current" | "todo" | null;
  /** An unsquashed `fixup!`/`squash!` commit: the commit below it that it folds into (core's pairing); null otherwise. */
  fixup_of?: string | null;
}

/** Why a rebase underway is stopped: core's verdict, the shell's words. */
export type RebaseStop = "edit" | "split" | "conflict" | "resolved" | "paused";

/** A way on from where a rebase stopped: `ger rebase --<action>`. */
export type RebaseAction = "continue" | "abort" | "skip";

/** A rebase underway, as `GET /stack` serves it. */
export interface Rebase {
  /** The branch being rebased; null for a detached HEAD's. */
  branch: string | null;
  onto: string | null;
  orig_head: string | null;
  /** Null when core cannot tell what `git rebase --continue` would do from here. */
  stop: RebaseStop | null;
  /** False while the commit it stopped at is only partly there (a conflict, resolved or not). */
  applied: boolean;
  /** The commit it stopped at, as the stack shows it. */
  current: string | null;
  /** Stopped on a fixup or squash: the commit it folds into (API 1.2). */
  folding_into?: string | null;
  /** Paths git rerere resolved at this stop from a resolution recorded earlier: unchecked (API 1.2). */
  reused?: string[];
  /** Paths the index still holds as unresolved conflicts. */
  unmerged: string[];
  /** At a stop with the commit applied: tracked files changed and not staged (a fix saved, not amended). Continue waits for them (API 1.8). */
  uncommitted?: string[];
  /** The stack is the branch as it was before the rebase (git's `am` backend keeps no todo): no commit has a step. */
  as_before: boolean;
  /** The ways on core allows from this stop, in order: the only ones to offer. */
  actions: RebaseAction[];
  /** For each way on `actions` leaves out, the refusal `ger rebase --<action>` prints: what to say when it is asked for anyway. */
  refused: Partial<Record<RebaseAction, string>>;
}

/** The step outcome: how the last `ger rebase --continue|--skip|--abort`, `ger edit -n` or `ger push` ended, in ger's words. */
export interface StepOutcome {
  /** `rebase --abort`, `push`: the command after `ger`. */
  command: string;
  /** False for a refusal, a failure or a cancel. */
  ok: boolean;
  /** The one line of what happened. */
  text: string;
  /** The lines after it: what was kept and where, what is gone, the `next:` step. */
  more: string[];
  /** When it ended, in seconds since the epoch. */
  at: number;
  /** HEAD then: once it moved on, the outcome is history. */
  head: string | null;
}

/** What `ger rebase --abort` would throw away from the stop (`GET /rebase/refusal?action=abort`, API 1.8). */
export interface AbortDiscards {
  /** Files not committed: ger keeps them in a stash. */
  uncommitted: string[];
  /** Commits made in this rebase the branch before it lacks: gone from the branch. */
  rewritten: { sha: string; short_sha: string; summary: string }[];
  /** A conflict resolved and added: its resolution goes. */
  resolution: boolean;
  /** Something no stash keeps: ask before aborting. */
  asks: boolean;
}

/** What is underway that no Gerrit push may run over: `GET /stack`'s `push_blocked`. */
export interface PushBlocked {
  reason: "rebase" | "am";
  /** The refusal `ger push` prints. */
  message: string;
}

export interface Stack {
  /** The branch checked out: null while a rebase runs (see `rebase.branch`). */
  branch: string | null;
  head: string | null;
  rebase_in_progress: boolean;
  /** The rebase underway; the stack is then as the rebase has it so far, the rest as it was. */
  rebase?: Rebase | null;
  /** Why `ger push` would refuse now, whatever the stack holds (core's verdict, and its words); null when it would not. */
  push_blocked?: PushBlocked | null;
  /**
   * The staged review in this checkout (core's fact, the one `ger push`, `ger edit` and `ger reword`
   * refuse over): somebody else's change, its content in the working files. Null when there is none.
   */
  staged_review?: ReviewState | null;
  /**
   * Why `ger review` would refuse to check a change out here now (core's verdict, API 1.8): tracked
   * files with uncommitted changes, while the review goes into this checkout. Null when it would not.
   */
  review_blocked?: { reason: string; message: string; files: string[] } | null;
  /** How far the upstream moved on past the stack, and the rebase onto it previewed; null without one, and during a rebase. */
  upstream?: UpstreamMoved | null;
  /** What the last rebase step or push run in a terminal said at its end (core's step outcome, API 1.8); null when none left one. */
  last_outcome?: StepOutcome | null;
  /** The branches are null without a working branch (a detached HEAD's rebase). */
  stack: { project: string; target_branch: string | null; push_branch: string | null };
  summary: { commits: number; ready: number; attention: number; needs_edit: number; not_on_gerrit: number };
  commits: StackCommit[];
  /** Why Gerrit could not be asked at all (its `reason`: `credentials`, `unreachable`, `no-answer`): every commit's Gerrit state is unknown. */
  gerrit_unavailable?: ApiError;
  /** Answered from git alone (`local=1`): Gerrit was not asked yet, and the full answer follows. */
  local_only?: boolean;
}

/** Why two neighbours of the local stack cannot swap places. */
export interface MoveConflict {
  sha: string;
  summary: string;
  files: string[];
}

/** `GET /stack`'s `upstream`: the commits the upstream has that the branch has not, and what the rebase onto it would meet. */
export interface UpstreamMoved {
  /** `origin/main`, as the branch config names it. */
  name: string;
  behind: number;
  /** The one ger command that rebases onto it, `ger` left out (`["rebase", "origin/main"]`, maybe `--drop-merged-equivalent`): run as it is. */
  argv: string[];
  /** The first commit the rebase would stop on; null when all would apply (or `preview` is false). */
  conflict: UpstreamStop | null;
  /** False when the rebase could not be previewed: nothing is known, and `reason` says why. */
  preview: boolean;
  /** Why there is no preview, in core's words; null when there is one. */
  reason: string | null;
  /** The commits the rebase leaves out, and so does the preview: already upstream, or merged on Gerrit with the same code. */
  dropped: { sha: string; summary: string }[];
}

/** Where the rebase onto the upstream would stop: a conflict in `files`, or a commit that would become empty (its change is upstream already). */
export interface UpstreamStop extends MoveConflict {
  kind: "conflict" | "empty";
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
  /** The Gerrit projects the sections are narrowed to: this repository's. Empty means all. */
  projects?: string[];
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
  /** The upstream it is configured with, when that remote-tracking branch is gone (API 1.8). */
  gone?: string | null;
  suggested: string | null;
  why: string | null;
  candidates: string[];
}

/** What a new comment becomes: a draft on Gerrit, or kept here (a local draft or a private note). */
export type NewCommentState = "draft" | "local-draft" | "note";

/** One replacement of a suggested edit: Gerrit's FixReplacementInfo, and where it applies as `ger comment` writes a location. */
export interface FixReplacement {
  path: string;
  range: { start_line: number; start_character: number; end_line: number; end_character: number };
  replacement: string;
  location: string;
  /** For a comment of a local commit: `range` in your working file, while the lines it covers are there unchanged; null otherwise. */
  worktree?: FixReplacement["range"] | null;
  /** For a comment of a local commit: the text it replaces in the version commented on; null when that version is not here. */
  original?: string | null;
}

/** An edit a comment suggests: a Gerrit fix suggestion, or a ```suggestion block of its text (`in_message`). */
export interface SuggestedEdit {
  fix_id: string | null;
  description: string;
  in_message: boolean;
  replacements: FixReplacement[];
}

export interface Comment {
  path: string;
  line: number | null;
  message: string;
  suggested_edits?: SuggestedEdit[];
  /** `message` with its fix suggestions written out below it, as `ger show` prints them: for the Change view and tooltips. */
  text: string;
  /** The same in Markdown, each replacement fenced: for the editor's comment threads. */
  markdown: string;
  author?: string;
  comment_id?: string;
  patch_set: number | null;
  state: "note" | "local-draft" | "draft" | "published";
  url: string | null;
}

export type ChainTurn = "yours" | "draft" | "theirs";

export interface CommentChain {
  root_id: string;
  path: string;
  line: number | null;
  start_line: number | null;
  /** The characters the chain's first comment was aimed at, Gerrit's range (lines from 1, characters from 0); null for a line comment (API 1.5). */
  range?: { start_line: number; start_character: number; end_line: number; end_character: number } | null;
  url: string | null;
  resolved: boolean;
  resolved_after_publish: boolean;
  /** Whose turn it is, core's verdict: somebody else spoke last (`yours`), and your answer is a draft (`draft`); your published comment is the last word (`theirs`). Null for a local chain. */
  turn?: ChainTurn | null;
  outdated: boolean;
  /**
   * For an open Gerrit chain of a local commit: where its lines are in your working file, mapped from
   * the version it was written on; `outdated` when they changed or are gone there. Null when that
   * cannot be told (absent from an older ger).
   */
  worktree?: { start_line: number; line: number; outdated: boolean } | null;
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
  /** A warning ranks below an error: it is rarely why the build failed. */
  severity: "error" | "warning";
  /** The line CI logged on the last pushed patchset, when `line` is where it sits in the local commit now (API 1.2). */
  patchset_line?: number | null;
  /** That line was edited since CI ran: `line` is next to where it was (API 1.2). */
  line_changed?: boolean;
}

export interface CiContent {
  label: string;
  url: string;
  /** What the build reported, as on its CI link: an unstable one is read like a failed one. */
  verdict?: "failed" | "unstable";
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
  /** Some error explains the failure, so the warnings step back behind a count. */
  collapse_warnings: boolean;
  /** The last pushed patchset CI built, when the local commit moved on with other code (API 1.2). */
  patch_set?: number | null;
}

export interface ShownCommit extends CommitStatus {
  local_commit: boolean;
  comment_chains: CommentChain[];
  ci_content?: CiContent[];
  /** Fingerprints of the findings this change introduced; null when that cannot be told. */
  ci_introduced?: string[] | null;
  /** What a new comment on it becomes by default: a draft where Gerrit has this commit, else a local draft. */
  new_comment?: "draft" | "local-draft";
  /** With `markdown`: the commit as `ger show --ai` prints it, for an agent. */
  markdown?: string;
  /** With `files`: what `/diff` answers for the commit. */
  files?: Diff;
  /** You own the change; null when it is not on Gerrit or Gerrit will not say who you are. */
  own?: boolean | null;
  /** What publishes your drafts on it: your next push (your own commit of the local stack), or a vote; null off Gerrit. */
  drafts_published_by?: "push" | "vote" | null;
  /** Gerrit could not be asked at all: its state, votes and comments of this commit are not known (not none). */
  gerrit_unavailable?: ApiError;
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
  /** The whole commit message, and `Name <email>`; not in a review delta. */
  message?: string;
  /** `message` without its Change-Id footer, for the box; `change_id` is that footer's (null without a valid one). */
  message_text?: string;
  change_id?: string | null;
  author?: string;
  local_commit: boolean;
  files: FileChange[];
  /** Present in a review delta: patchset `patch_set` against `to_patch_set` (null: a local commit). */
  since?: { patch_set: number | null; current_patch_set: number; to_patch_set?: number | null; up_to_date: boolean; identical: boolean; rebased: boolean };
}

/** What `/diff` compares instead of a commit with its parent. */
export interface DiffOptions {
  /** This patchset of the change, not the commit `rev` names. */
  patchset?: number;
  /** A review delta from the patchset you reviewed, Gerrit's current one, or patchset `n`. */
  since?: "reviewed" | "current" | number;
  /** The review delta runs to this local commit. */
  local?: string;
}

/** The patchsets Gerrit has of one change, oldest first. */
export interface Patchsets {
  number: number | null;
  current: number | null;
  /** The newest patchset you posted on. */
  reviewed: number | null;
  patchsets: { number: number; commit: string; created: string | null }[];
}

export interface ReviewState {
  number: number;
  patch_set: number;
  /** The reviewed patchset's commit. */
  commit: string;
  subject: string;
  staged: boolean;
  /** In the review worktree, or in place on a branch (`ger.reviewCheckout`). */
  mode: "worktree" | "branch";
  worktree: string;
  branch: string;
  /** The review chain's open changes, base first; null unless asked for. */
  chain?: number[] | null;
  /** Where Leave Review goes back to: the branch (or commit) checked out before the review. */
  return_to?: string | null;
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

/** One draft of yours a vote publishes: where, its first line, what it does to its chain. */
export interface VoteDraft {
  path: string;
  line: number | null;
  /** `path:line`, in words for the commit message and the patchset level. */
  location: string;
  text: string;
  transition: "resolves" | "reopens" | null;
  /** It suggests an edit (a ```suggestion block, a fix suggestion): said in the dialog, as its first line may not show it. */
  suggested_edit?: boolean;
}

/** One change a vote goes to, as `GET /vote` serves it. */
export interface VoteChange {
  number: number;
  change: string;
  subject: string;
  /** Gerrit's current patchset: what `POST /vote` names back. */
  patch_set: number | null;
  url: string | null;
  owner: string;
  own: boolean;
  permitted: Record<string, number[]>;
  ruled_out: string[];
  /** Your votes on its current patchset, by label. */
  yours?: Record<string, number>;
  drafts: VoteDraft[];
}

/** A vote to offer: what the project says it means, and which changes take it. */
export interface VoteOption {
  label: string;
  value: number;
  meaning: string;
  takes: number[];
  /** A chain vote: of `takes`, the changes that keep your vote on this label; the vote does not go there. */
  keeps?: number[];
}

/** `GET /vote`: what a vote would send. Nothing is sent. */
export interface VotePlan {
  changes: VoteChange[];
  votes: Record<string, number>;
  drafts: number;
  options: VoteOption[];
}

/** `POST /vote`'s answer: where it went, and where it did not. */
export interface VoteSent {
  changes: { change: string; number: number; drafts: number; labels: Record<string, number>; keeps?: Record<string, number>; message: string | null; status: string }[];
  skipped: { change: string; number: number; ruled_out: string[] }[];
}

/** A commit id (kept as it is), or what to do with it: fold into the commit below, or drop. */
export type StackStep = string | { sha: string; action: "pick" | "fixup" | "fixup-take-message" | "drop" };

/** The stack after a rewrite, and what `undo` takes back. */
export type Rewritten = Stack & { previous_head: string; stash_kept?: boolean };

export interface ShowOptions {
  stack?: boolean;
  chain?: boolean;
  ci?: boolean;
  comments?: "unresolved" | "all" | "resolved";
  context?: number;
  /** Add `markdown`: what `ger show --ai` prints. */
  markdown?: boolean;
  /** Add `files`: the commit's files against its parent, as `/diff` answers them, in the same call. */
  files?: boolean;
}

type Params = Record<string, string | string[] | number | boolean | undefined>;

/** What leaving a review answers: the change, the branches that went and stayed, and where its checkout is now (API 1.8). */
export interface ReviewDone {
  done: number;
  deleted: string[];
  kept: string[];
  /** The branch the review's checkout has now; null when detached. */
  branch?: string | null;
  worktree?: string;
  mode?: "worktree" | "branch";
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
  /** What `/health` answered when the service started (set by server.ts once it checked the API). */
  served: Health | undefined;

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

  private async call<T>(method: "GET" | "POST", path: string, params?: Params, body?: unknown, signal?: AbortSignal): Promise<T> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.hello.token}` };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    const span = activeTracer()?.request(method, path);
    let payload: { error?: ApiError } & T;
    let response: Response;
    try {
      response = await fetch(this.url(path, params), {
        method,
        headers: span ? { ...headers, ...span.headers } : headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
      payload = (await response.json()) as { error?: ApiError } & T;
      span?.end(response.status, response.headers.get("X-Ger-Trace"));
    } finally {
      span?.end(0, null);
    }
    if (!response.ok) {
      const error = payload.error ?? { code: "INTERNAL", message: response.statusText };
      throw new GerApiError(error.code, error.message, response.status, error.reason);
    }
    return payload;
  }

  health(): Promise<Health> {
    return this.call("GET", "/health");
  }

  /** The activity sequence once it moves past `since`, or after `wait` seconds; at once without `since`. */
  activity(since: number | undefined, wait: number, signal?: AbortSignal): Promise<Activity> {
    return this.call("GET", "/activity", { since, wait: since === undefined ? undefined : wait }, undefined, signal);
  }

  overview(refresh = false): Promise<Overview> {
    return this.call("GET", "/overview", { refresh });
  }

  /**
   * The local stack alone, as `/overview`'s `local`: what a checkout here changes. `local`: from git
   * alone, without asking Gerrit (API 1.8; an older ger serve asks Gerrit anyway).
   */
  stack(local = false): Promise<Stack> {
    return this.call("GET", "/stack", { local: local || undefined });
  }

  chain(number: number): Promise<Chain> {
    return this.call("GET", `/chains/${number}`);
  }

  async show(revs: string[], options: ShowOptions = {}, signal?: AbortSignal): Promise<ShownCommit[]> {
    const answer = await this.call<{ commits: ShownCommit[] }>("GET", "/show", { rev: revs, ...options }, undefined, signal);
    return answer.commits;
  }

  diff(rev: string, options: DiffOptions = {}, signal?: AbortSignal): Promise<Diff> {
    return this.call("GET", "/diff", { rev, ...options }, undefined, signal);
  }

  patchsets(rev: string, signal?: AbortSignal): Promise<Patchsets> {
    return this.call("GET", "/patchsets", { rev }, undefined, signal);
  }

  /** What changed since the patchset you last reviewed. */
  reviewDelta(rev: string): Promise<Diff> {
    return this.call("GET", "/diff", { rev, since: "reviewed" });
  }

  async file(rev: string, path: string): Promise<string | null> {
    return (await this.call<{ content: string | null }>("GET", "/file", { rev, path })).content;
  }

  /** Why `ger rebase --<action>` (and `ger edit -n`) would refuse now, in its words, the work tree's unstaged changes too; null when it would run. */
  async rebaseRefusal(action: RebaseAction): Promise<string | null> {
    return (await this.call<{ refused: string | null }>("GET", "/rebase/refusal", { action })).refused;
  }

  /** What Abort would throw away now, and whether it refuses; `discards` undefined from a ger before API 1.8. */
  async abortCheck(): Promise<{ refused: string | null; discards?: AbortDiscards }> {
    return this.call<{ refused: string | null; discards?: AbortDiscards }>("GET", "/rebase/refusal", { action: "abort" });
  }

  /** The review going on here or in the review worktree; with `chain`, its review chain too (one Gerrit request). */
  async review(chain = false): Promise<ReviewState | null> {
    return (await this.call<{ review: ReviewState | null }>("GET", "/review", chain ? { chain: "1" } : undefined)).review;
  }

  /** `ger review CHANGE`; `mode` as the review going on has it, else `ger.reviewCheckout` decides. */
  async reviewOpen(change: number | string, mode?: ReviewState["mode"]): Promise<ReviewState> {
    return (await this.call<{ review: ReviewState }>("POST", "/review/open", undefined, { change, mode })).review;
  }

  /** `ger review --next` (1) or `--prev` (-1): the review moves along its chain. At its end a `GerApiError` with reason `chain-end`. */
  async reviewStep(step: 1 | -1): Promise<ReviewState> {
    return (await this.call<{ review: ReviewState }>("POST", "/review/open", undefined, { step })).review;
  }

  /** Gerrit's per-file *reviewed* flag: the files of patchset `patchSet` of change `change` you marked (API 1.6). */
  async reviewedFiles(change: number, patchSet: number, signal?: AbortSignal): Promise<string[]> {
    return (await this.call<{ reviewed: string[] }>("GET", "/reviewed", { change, patchset: patchSet }, undefined, signal)).reviewed;
  }

  /** Mark `path` of that patchset reviewed on Gerrit, or clear the mark. */
  async markReviewed(change: number, patchSet: number, path: string, reviewed: boolean): Promise<void> {
    await this.call("POST", "/reviewed", undefined, { change, patchset: patchSet, path, reviewed });
  }

  /** Mark each of `paths` of that patchset reviewed: one request (API 1.7); a ger serve before it, one per path. */
  async markAllReviewed(change: number, patchSet: number, paths: readonly string[]): Promise<void> {
    try {
      await this.call("POST", "/reviewed", undefined, { change, patchset: patchSet, paths, reviewed: true });
    } catch (error) {
      if (!(error instanceof GerApiError) || error.status !== 400) {
        throw error;
      }
      for (const path of paths) {
        await this.markReviewed(change, patchSet, path, true);
      }
    }
  }

  reviewDone(): Promise<ReviewDone> {
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

  /**
   * Start a comment chain at `location` (`path`, `path:line`, `path:start-end`, lines from 1).
   * `commit` is the version of the change whose lines were shown (`/diff`'s `commit`): the
   * comment goes on it, which is not always the commit `rev` names (an older patchset).
   */
  newComment(rev: string, location: string, message: string, state: NewCommentState, commit?: string): Promise<{ state: NewCommentState; id: string | null }> {
    return this.call("POST", "/comments/new", undefined, { rev, location, message, state, ...(commit ? { commit } : {}) });
  }

  /** Give one of your unpublished comments (a draft, a local draft or a note) a new text; `message` is what it said. */
  editComment(rev: string, commentId: string, message: string): Promise<{ state: NewCommentState; message: string }> {
    return this.call("POST", "/comments/edit", undefined, { rev, comment_id: commentId, message });
  }

  /** Delete one of your unpublished comments; `message` is what it said. */
  discardComment(rev: string, commentId: string): Promise<{ state: NewCommentState; message: string }> {
    return this.call("POST", "/comments/discard", undefined, { rev, comment_id: commentId });
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

  /** Rewrite the local stack; `steps` are oldest first. All or nothing. `autostash` stashes uncommitted changes for it. */
  rewrite(steps: StackStep[], autostash = false): Promise<Rewritten> {
    return this.call("POST", "/stack/rewrite", undefined, { steps, autostash });
  }

  /** Give one commit of the local stack a new commit message. Nothing else changes. */
  reword(rev: string, message: string): Promise<Rewritten> {
    return this.call("POST", "/stack/reword", undefined, { rev, message, keep_change_id: true });
  }

  /** What a vote on change `number` (`chain`: on every open change of its chain) would send. Sends nothing. */
  votePlan(number: number, chain: boolean): Promise<VotePlan> {
    return this.call("GET", "/vote", { change: number, chain });
  }

  /**
   * Publish your drafts with `votes` ({} for none) and `message` on change `number` (or its chain).
   * `patchSets` is what `votePlan` showed: when that moved, nothing is sent (`REFUSED`).
   */
  vote(number: number, chain: boolean, votes: Record<string, number>, message: string | undefined, patchSets: Record<string, number>): Promise<VoteSent> {
    return this.call("POST", "/vote", undefined, { change: number, chain, votes, message, patch_sets: patchSets });
  }

  /** Take a rewrite back, while the branch is still where it left it. */
  undo(head: string, previousHead: string): Promise<Stack> {
    return this.call("POST", "/stack/undo", undefined, { head, previous_head: previousHead });
  }
}
