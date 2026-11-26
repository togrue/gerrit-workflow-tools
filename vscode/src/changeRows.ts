// What the "Change" view shows, as plain data: what the change needs, its commit message, which
// version is compared with what, the files with their line counts, the comment chains and the
// CI findings. The webview draws it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import * as path from "node:path";

import { Attended, CiContent, CiFinding, Comment, CommentChain, Diff, RebaseAction, Severity, ShownCommit } from "./api";
import { attentionIcon, commitVotes, fileState, plural, reasonWords, sentence } from "./format";
import type { Commented } from "./commentState";
import { Badge, SYNC_WORDS, StackAction, StackInfo, Tone, actionsText, badges, sync, conflictRow, rebaseButtons } from "./stackRows";
import { Waiting } from "./state";
import { latestSuggestion } from "./suggestions";
import { Problem, gerritProblem, problemLines } from "./problemRows";
import { CompareState, CompareVm, MODES, Option, compareVm, deltaTitle } from "./compareRows";
import type { TaskMode, TaskVm } from "./taskRows";


export const NOT_FILES = ["/COMMIT_MSG", "/PATCHSET_LEVEL", "/MERGE_LIST"];

export type CiView =
  | { state: "closed" | "loading" }
  | { state: "error"; error: string }
  | { state: "loaded"; contents: CiContent[]; introduced: Set<string> | null };

/** What the view still waits for; until each comes, it is drawn as loading. */
export interface Loading {
  /** `/show`: the comment chains, and whatever the clicked row could not say. */
  commit: boolean;
  /** The version against its parent: the commit message and the files. */
  base: boolean;
  /** The review delta. */
  delta: boolean;
  /** Whether it is known if the change is ready to submit: a Review chains row does not say. */
  ready: boolean;
}

/** A rebase stopped at the commit shown, before applying it (`stoppedAt`). */
export interface Stopped {
  stop: "conflict" | "resolved";
  unmerged: string[];
  /** The ways on core allows from there (`rebase.actions`), as on Local stack's banner. */
  actions: RebaseAction[];
}

/** Everything the view is built from. `shown`: as `/show` answered, or (while `loading.commit`) as the clicked row said. */
export interface ChangeState extends CompareState {
  /** Comment chains, in the order shown. */
  chains: CommentChain[];
  /** The version looked at against its parent; it carries the commit message. */
  base: Diff | undefined;
  /** The review delta: From against To, or (someone's change, From its base) since your review. */
  delta: Diff | undefined;
  /** Why the commit message cannot be edited; empty when it can. */
  readOnly: string;
  ci: CiView;
  /**
   * The rebase underway stopped at this commit and has not applied it: on a conflict (`unmerged`,
   * the files still conflicted), or with the conflicts resolved. Core's verdict, from the stack.
   */
  stopped?: Stopped;
  /**
   * The change a staged review holds here (core's `staged_review`): read as somebody else's change,
   * with what is new since your review; `behind` when Gerrit has a newer patchset than the one
   * checked out, `newest` its number when known.
   */
  reviewing?: { behind: boolean; newest: number | null };
  /** The files of the comparison shown that were seen (Gerrit's reviewed flag, or ticked here). */
  seen?: ReadonlySet<string>;
  /** What has not come yet; absent once everything has. */
  loading?: Loading;
  /**
   * The task header drawn above the change (taskRows.ts). Reworking, it says where the rebase
   * stands and holds its buttons: the change does not say it again (P4).
   */
  task?: TaskMode;
  /** The change is under review in the review worktree's own window: its path. */
  elsewhere?: string;
  /** Files whose uncommitted changes stand in the way of a review checked out here (core's `review_blocked`). */
  blocked?: string[];
  /** The patchset whose lines are shown (`ChangeView.shownPatchset`): a comment written on another is marked so. */
  patchset?: number;
  /** The comment chains already gone to on this change (Next comment, a click): the count says how many. */
  visited?: ReadonlySet<string>;
}

/** The way on from a staged review behind Gerrit: `ger review` checks the newest patchset out. */
export const REVIEW_UPDATE = { command: "ger.reviewUpdate", title: "ger review: check Gerrit's newest patchset out for review, staged, in place of this one" };

export interface Icon {
  icon: string;
  tone?: Tone | "dim";
}

export interface FileRow {
  path: string;
  name: string;
  dir: string;
  state: string;
  /** Null for a binary file. */
  added: number | null;
  deleted: number | null;
  tooltip: string;
  /** Seen: shown to its end, or ticked. */
  seen: boolean;
}

export interface ReplyRow {
  author: string;
  /** `draft`, `local-draft`, `note`; empty for a published comment. */
  state: string;
  text: string;
}

export interface ThreadRow extends Icon {
  id: string;
  /** The first line of the first comment; `body` is all of it, by `author`. Expanding the row shows each comment in full. */
  text: string;
  body: string;
  author: string;
  /** It sits on a line of a file: a click opens it there, in its editor thread. Else (the whole change, the commit message) a click expands it here. */
  file: boolean;
  notes: string[];
  tooltip: string;
  /** Somebody's chain on Gerrit: it can be answered. */
  reply: boolean;
  /** It is still open: "Done" resolves it, and it can be kept from the agent. */
  open: boolean;
  /** Open, and your published comment is its last word: it waits on the other side, not on you. */
  waiting: boolean;
  held: boolean;
  resolved: boolean;
  /** Where it stands once your drafts are published: Gerrit's Resolved checkbox starts there. */
  resolving: boolean;
  /** Your draft reply is there: Save with no text still sets whether it resolves the chain. */
  draft: boolean;
  /** A chain you started and have not published: its text can be edited. */
  edit: boolean;
  /** What deleting your unpublished comment in it is called; empty when you have none there. */
  discard: string;
  /** A comment of it suggests an edit, and it is your commit's: Apply writes it into your file. */
  apply: boolean;
  replies: ReplyRow[];
}

export interface FindingRow extends Icon {
  text: string;
  notes: string[];
  tooltip: string;
  path: string | null;
  line: number | null;
}

export interface CiLinkRow extends Icon {
  text: string;
  detail: string;
  url: string;
  tooltip: string;
  /** The findings to read first: errors, and warnings when nothing else explains the failure. */
  findings: FindingRow[];
  /** Warnings below the errors, collapsed by default. */
  warnings: FindingRow[];
}


export interface ChangeVm {
  waiting?: Waiting;
  /** Nothing is shown: what to do to see a change. */
  empty?: string;
  /** The change could not be read: what state the view is in, git's or Gerrit's words, the ways on (problemRows.ts). */
  problem?: Problem;
  /** Which change is drawn (its Change-Id, else its commit): the page keeps what you opened and typed per change. */
  key?: string;
  /** What is on its way: the page holds the commit message's place, its read-only note and plainness (as `message` will say) included. */
  loading?: { message: boolean; readOnly: string; plain: boolean };
  /** `badges` as the Local stack draws them; `detail` says them in words (or that the change is not on Gerrit). `actions`: the way on from a rebase stopped here, as Local stack's banner offers it. */
  head?: Icon & { text: string; detail: string; badges: Badge[]; actions?: StackAction[] };
  /** `raw`: the words of git or Gerrit (or a path) the note rests on, as they came, drawn under it. */
  notes?: (Icon & { text: string; detail: string; raw?: string; actions?: StackAction[] })[];
  /**
   * `text` without the Change-Id footer, which `changeId` names (empty when there is none): the page
   * shows it read-only under the message, and core puts it back on Apply (`keep_change_id`). `plain`: a message
   * that cannot be reworded here at all (someone's change, a patchset on Gerrit), drawn as text, not a box.
   */
  message?: { key: string; text: string; changeId: string; author: string; readOnly: string; plain: boolean };
  /** The From and To pickers, and the toggle of the default mode (compareRows.ts). */
  compare?: CompareVm;
  /** Someone's change you reviewed before: what is new, one click away. */
  hint?: { text: string; show: boolean };
  /** A rebase stopped here on a conflict: the files still conflicted, as Local stack lists them under its banner. */
  conflicts?: { summary: string; rows: StackInfo[] };
  files?: { title: string; summary: string; added: number; deleted: number; note: string; tooltip: string; rows: FileRow[]; markAll?: string };
  comments?: { summary: string; open: boolean; threads: ThreadRow[] };
  /** Next: the one thing to do now, its button's text and why (nextAction.ts). */
  next?: { label: string; title: string };
  /** Something under way the view waits on (a checkout of the change shown), said in place of Next. */
  busy?: string;
  /** Reviewing: the task header (taskRows.ts), drawn above the change. */
  task?: TaskVm;
  /** `noLinks`: what to say when no build log was found; empty when that is not to be said. */
  ci?: { summary: string; state: CiView["state"]; error: string; noLinks: string; links: CiLinkRow[] };
}

/** A failed CI vote without a log to read. */
export const NO_CI_LINK = "No CI link was found for the failed build";

/** What the view says while no change is chosen. */
export const NOTHING_SHOWN = "Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.";

/** Why a commit message cannot be edited: someone's change, a patchset on Gerrit, a rebase underway. */
export const READ_ONLY = {
  remote: "Only a commit of your local stack can be reworded here.",
  patchset: "This is a patchset on Gerrit: choose the local commit to edit its message.",
  rebase: "The message cannot be edited during a rebase: reword it once the rebase is finished or aborted.",
  review: "Somebody else's change, checked out for review: its message is theirs.",
};

export const LOADING = "loading…";

/** The head of the commit a rebase stopped at, reworking, when Gerrit has nothing for you on it. */
export const NOTHING_FROM_GERRIT = "Nothing from Gerrit to answer";

/** Never reworded here: a box would only say so. A rebase underway passes; the message stays a box. */
function isPlain(readOnly: string): boolean {
  return readOnly === READ_ONLY.remote || readOnly === READ_ONLY.patchset || readOnly === READ_ONLY.review;
}

/** The Change view of the commit a rebase stopped at before applying it. */
export const STOPPED_WORDS = {
  conflict: (file: string) => `Conflict in ${file} — resolve it, then Continue`,
  conflicts: (count: number) => `Conflicts in ${count} files — resolve them, then Continue`,
  resolved: "Conflicts resolved — Continue",
  files: "Files, as before the rebase",
  filesTooltip: "The rebase has not applied this commit yet: these are its files as they were before the rebase, against its old parent. What is left to resolve is under Conflicts.",
};

/** The head of a commit a rebase stopped at before applying it: what is left to do, and the buttons to go on. */
function stoppedHead(stopped: Stopped): Icon & { text: string; actions: StackAction[] } {
  const [one, ...more] = stopped.unmerged;
  const text =
    stopped.stop === "resolved" || one === undefined ? STOPPED_WORDS.resolved : more.length ? STOPPED_WORDS.conflicts(stopped.unmerged.length) : STOPPED_WORDS.conflict(one);
  const icon: Icon = stopped.stop === "resolved" ? { icon: "debug-continue", tone: "warn" } : { icon: "warning", tone: "bad" };
  return { ...icon, text, actions: rebaseButtons(stopped) };
}

const SEVERITY_TONE: Record<Severity, Tone | undefined> = { error: "bad", warning: "warn", pending: undefined, ok: "good" };

/** The head's icon: the one the change wears in Review chains (`attentionIcon`). */
function headIcon(commit: Readonly<Attended>): Icon {
  const { icon, severity } = attentionIcon(commit);
  const tone = SEVERITY_TONE[severity];
  return tone ? { icon, tone } : { icon };
}

export const YOURS: Record<string, string> = { note: "your private note", "local-draft": "your local draft", draft: "your draft" };

/** A chain you started and have not published: a draft, a local draft or a private note. */
export function isYours(chain: CommentChain): boolean {
  return (chain.comments[0]?.state ?? "published") !== "published";
}

/** A chain somebody started on Gerrit that your draft reply answers (or resolves): published, it is no longer yours to answer. */
export function answeredByDraft(chain: CommentChain): boolean {
  return !isYours(chain) && chain.comments.slice(1).some((comment) => comment.state === "draft");
}

/** Left for you to answer: open, somebody started it on Gerrit, your draft does not answer it, and your published comment is not its last word. */
export function toAnswer(chain: CommentChain): boolean {
  return !chain.resolved && !isYours(chain) && !answeredByDraft(chain) && chain.turn !== "theirs";
}

/** Your unpublished comment in `chain` that can be edited or deleted: the chain itself when you started it, else your draft reply. */
export function ownComment(chain: CommentChain): Comment | undefined {
  const own = isYours(chain) ? chain.comments[0] : chain.comments.slice(1).find((comment) => comment.state === "draft");
  return own?.comment_id ? own : undefined;
}

/** Whom a reply in `chain` answers: the last who spoke in it on Gerrit. */
export function answers(chain: CommentChain): string | undefined {
  return chain.comments.filter((comment) => comment.state === "published").at(-1)?.author;
}

/**
 * Whom the reply box of an editor thread names. VS Code has one box text for all of an extension's
 * threads, so a name is said only where it is sure: `at`, the chain gone to or the cursor is in;
 * else the one person every chain that can be answered answers; else none ("Reply…").
 */
export function replyName(chains: readonly CommentChain[], at?: CommentChain): string | undefined {
  if (at) {
    return answers(at);
  }
  const names = new Set(chains.filter((chain) => !isYours(chain)).map(answers));
  return names.size === 1 ? [...names][0] : undefined;
}

/** How many chains hold an unpublished comment of yours: chains you started, and your draft replies. */
export function draftsIn(chains: readonly CommentChain[]): number {
  return chains.filter((chain) => ownComment(chain) !== undefined).length;
}

export const DISCARD: Record<string, string> = { note: "Delete Your Private Note…", "local-draft": "Delete Your Local Draft…", draft: "Delete Your Draft…" };


/** The Files header's way to tick every file not seen yet (the combined diff ticks none on its own). */
export const MARK_ALL_SEEN = "Mark all seen";

function files(state: ChangeState): NonNullable<ChangeVm["files"]> {
  const compared = state.from !== "base";
  const diff = compared ? state.delta : state.base;
  const since = diff?.since;
  const to = since?.to_patch_set ? `patchset ${since.to_patch_set}` : "local";
  let [title, note, tooltip] = ["Files", "", "What the change does: its files against the parent commit."];
  if (compared) {
    const kind = deltaTitle(state);
    title = kind;
    tooltip = since?.rebased
      ? "The two versions sit on different parents. Only files the change touches are listed; inside them, edits that came in from underneath show too."
      : kind === "Gerrit vs. local"
        ? "What your commit changes compared with the patchset Gerrit has: what a push would upload on top."
        : kind === "Since your review"
          ? "What changed after the patchset you last voted or commented on."
          : "What changed from the version under From to the one under To.";
    if (!diff) {
      note = state.loading?.delta ? LOADING : "could not be compared";
    } else if (since?.patch_set === null || since?.patch_set === undefined) {
      note = "you have not reviewed this change yet";
    } else if (since.identical && kind === "Gerrit vs. local") {
      note = `identical to patchset ${since.patch_set} — nothing to push`;
    } else if (since.identical && kind !== "Since your review") {
      note = `identical to patchset ${since.patch_set}`;
    } else if (since.up_to_date || since.identical) {
      note = `nothing new: you reviewed patchset ${since.patch_set}`;
    } else {
      title = `${title}: patchset ${since.patch_set} → ${to}${since.rebased ? " (rebased)" : ""}`;
    }
  } else if (!diff) {
    note = state.loading?.base ? LOADING : "the commit is not available here";
  }
  if (state.stopped && !compared && state.version === "local") {
    [title, tooltip] = [STOPPED_WORDS.files, STOPPED_WORDS.filesTooltip];
  }
  const rows = (diff?.files ?? []).map((file): FileRow => {
    const dir = path.posix.dirname(file.path);
    return {
      path: file.path,
      name: path.posix.basename(file.path),
      dir: dir === "." ? "" : dir,
      state: fileState(file.status),
      added: file.insertions,
      deleted: file.deletions,
      tooltip: file.old_path ? `${file.old_path} → ${file.path}` : file.path,
      seen: Boolean(state.seen?.has(file.path)),
    };
  });
  const sum = (pick: (row: FileRow) => number | null) => rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
  const [added, deleted] = [sum((row) => row.added), sum((row) => row.deleted)];
  const seen = rows.filter((row) => row.seen).length;
  // How much is left on somebody's change: `2 of 4 files seen`; on yours only once you tick one.
  const tracked = rows.length > 0 && (seen > 0 || state.shown?.own === false);
  const summary = note || (tracked ? `${seen} of ${plural(rows.length, "file")} seen` : plural(rows.length, "file"));
  // Read in the combined diff, which ticks none: the header ticks the rest in one go.
  const markAll = !note && tracked && seen < rows.length ? MARK_ALL_SEEN : undefined;
  return { title, summary, added, deleted, note, tooltip, rows, ...(markAll ? { markAll } : {}) };
}

/** `applies`: an edit a comment suggests may be written into your file (your own local commit). */
function thread(chain: CommentChain, applies: boolean, shownPatchset?: number): ThreadRow {
  const first = chain.comments[0];
  // Written on another patchset than the one shown: said, so its line is not read as this one's (W10).
  const elsewhere = first?.patch_set != null && shownPatchset !== undefined && first.patch_set !== shownPatchset ? ` (patchset ${first.patch_set})` : "";
  const where = NOT_FILES.includes(chain.path) ? "whole change" : `${path.posix.basename(chain.path)}${chain.line ? `:${chain.line}` : ""}${elsewhere}`;
  const replies = chain.comments.slice(1);
  const mine = isYours(chain) ? YOURS[first?.state ?? ""] : undefined;
  const answered = !chain.resolved && chain.resolved_after_publish;
  const held = chain.held && !chain.resolved;
  // Your published comment is its last word (core's turn): open, but waiting on the other side.
  const waiting = !mine && !chain.resolved && chain.turn === "theirs";
  const own = ownComment(chain);
  // In your file its lines may sit elsewhere now (core's `worktree`): said beside Gerrit's line.
  const now = chain.worktree;
  const moved = now ? (now.outdated ? "outdated: changed since" : now.line !== chain.line ? `line ${now.line} in your file` : "") : "";
  const suggests = latestSuggestion(chain.comments) !== undefined;
  const notes = [mine ?? "", held ? "kept for you" : "", answered ? "your draft resolves it" : "", waiting ? "waits on a reply" : "", suggests ? "suggested edit" : "", where, moved, mine ? "" : (first?.author ?? "")];
  const icon: Icon = mine
    ? { icon: first?.state === "note" ? "note" : "edit" }
    : chain.resolved
      ? { icon: "pass", tone: "dim" }
      : answered
        ? { icon: "edit", tone: "good" }
        : held
          ? { icon: "person", tone: "warn" }
          : waiting
            ? { icon: "comment", tone: "dim" }
            : { icon: "comment-unresolved", tone: "warn" };
  return {
    ...icon,
    id: chain.root_id,
    text: first?.message.split("\n")[0] ?? where,
    body: first?.text ?? "",
    author: first ? (first.author ?? "you") : "",
    file: !NOT_FILES.includes(chain.path),
    notes: notes.filter(Boolean),
    tooltip: chain.comments.map((c) => `${c.author ?? "you"}${c.state === "published" ? "" : ` (${c.state})`}:\n${c.text}`).join("\n\n"),
    // Your own unpublished chain: nobody to reply to, nothing for the agent to keep from.
    reply: !mine,
    open: !mine && !chain.resolved,
    waiting,
    held: Boolean(chain.held),
    resolved: chain.resolved,
    resolving: chain.resolved_after_publish,
    draft: Boolean(!mine && own),
    edit: Boolean(mine && own),
    discard: own ? (mine ? DISCARD[own.state] : "Delete Your Draft Reply…") : "",
    apply: applies && suggests && !mine && !chain.resolved,
    replies: replies.map((c) => ({ author: c.author ?? (c.state === "published" ? "?" : "you"), state: c.state === "published" ? "" : c.state, text: c.text })),
  };
}

function finding(item: CiFinding, introduced: boolean | null, patchSet: number | null = null): FindingRow {
  // A last pushed patchset's finding sits where its line is now; core says where CI saw it.
  const moved = patchSet && item.patchset_line ? ` (line ${item.patchset_line} on patchset ${patchSet}${item.line_changed ? ", changed since" : ""})` : "";
  const where = item.path ? `${item.path}${item.line ? `:${item.line}` : ""}${moved}` : "";
  const origin = introduced === null ? "" : introduced ? "introduced here" : "also fails on the parent";
  const icon: Icon = introduced === null ? { icon: "question" } : introduced ? { icon: "flame", tone: "bad" } : { icon: "circle-outline", tone: "dim" };
  return {
    ...icon,
    text: item.message,
    notes: [item.tool, item.severity === "warning" ? "warning" : "", where, origin].filter(Boolean),
    tooltip: [item.message, item.detail ?? "", origin].filter(Boolean).join("\n\n"),
    path: item.path,
    line: item.line,
  };
}

function ciLinks(ci: CiView): CiLinkRow[] {
  if (ci.state !== "loaded") {
    return [];
  }
  return ci.contents.map((content) => {
    const failed = content.provenance.error !== null;
    // An unstable build (core's verdict) is read like a failed one, and says it is unstable: a warning, not an error.
    const unstable = content.verdict === "unstable";
    const rows = content.findings.map((item) => ({ item, introduced: ci.introduced ? ci.introduced.has(item.fingerprint) : null }));
    // What this change broke comes first; what it inherited from below follows. Errors before warnings.
    const rank = (row: (typeof rows)[number]) => (row.introduced ? 0 : 2) + (row.item.severity === "warning" ? 1 : 0);
    rows.sort((a, b) => rank(a) - rank(b));
    const collapsed = (row: (typeof rows)[number]) => Boolean(content.collapse_warnings) && row.item.severity === "warning";
    const leading = rows.filter((row) => !collapsed(row));
    const warnings = rows.filter(collapsed);
    const counted = [plural(leading.length, "finding"), warnings.length ? plural(warnings.length, "warning") : ""];
    return {
      // The last pushed patchset's build is dim: not about the local code.
      icon: failed || unstable ? "warning" : content.patch_set ? "history" : "beaker",
      tone: failed || unstable ? "warn" : content.patch_set ? "dim" : undefined,
      text: content.provenance.build ?? content.label,
      detail: [
        content.patch_set ? `from patchset ${content.patch_set}, before your local changes` : "",
        ...(failed ? [`could not be read: ${content.provenance.error} — click to open it in the browser`] : [unstable ? "unstable" : "", ...counted]),
      ]
        .filter(Boolean)
        .join(" · "),
      url: content.provenance.url,
      tooltip: `${content.provenance.url}${content.provenance.truncated ? "\nThe log was cut: findings may be incomplete." : ""}`,
      findings: leading.map(({ item, introduced }) => finding(item, introduced, content.patch_set ?? null)),
      warnings: warnings.map(({ item, introduced }) => finding(item, introduced, content.patch_set ?? null)),
    };
  });
}

/** Core's counts of a row (`Commented`), every one known: what `chainCounts` gives. */
export type ChainCounts = { [K in keyof Commented]-?: number };

/**
 * What a change's comment chains from Gerrit leave for you, from the chains themselves: left to
 * answer (`toAnswer`), answered by your drafts, waiting on a reply, all of them, as core's counts
 * of a row are shaped (`Commented`). The one count of the change shown: the Change view says it,
 * and the task header takes it for that change over the counts served with the inbox and the stack.
 */
export function chainCounts(chains: readonly CommentChain[]): ChainCounts {
  const fromGerrit = chains.filter((chain) => !isYours(chain));
  const open = fromGerrit.filter((chain) => !chain.resolved);
  return {
    comments_unresolved: open.length,
    comments_to_answer: open.filter(toAnswer).length,
    comments_drafted: open.filter(answeredByDraft).length,
    comments_awaiting_reply: open.filter((chain) => !answeredByDraft(chain) && chain.turn === "theirs").length,
    comments_total: fromGerrit.length,
  };
}

/**
 * What the comments from Gerrit leave for you: left to answer (of them, how many you went to
 * already, so the count moves as you walk them), what your drafts answer (and what publishes
 * them), and what waits on a reply. Unresolved on Gerrit, all three, until the drafts are published.
 * "To answer" is the one word for it, on your change and on one you review alike (CONTEXT.md: turn).
 */
function commentCounts(commit: ShownCommit, counts: ChainCounts, goneTo = 0): string {
  const { comments_to_answer: left, comments_drafted: answered, comments_awaiting_reply: waiting } = counts;
  const publish = commit.drafts_published_by === "push" ? "push publishes them" : "Vote and Publish sends them";
  const parts = [
    left ? `${left} to answer${goneTo ? `, ${Math.min(goneTo, left)} gone to` : ""}` : "",
    answered ? `${answered} answered by your drafts (${publish})` : "",
    waiting ? `${waiting} waiting on a reply` : "",
  ];
  return parts.filter(Boolean).join(" · ") || `all ${counts.comments_total} resolved`;
}

export function changeVm(state: ChangeState): ChangeVm {
  const { shown: commit, chains } = state;
  const notes: NonNullable<ChangeVm["notes"]> = [];
  // Stopped before this commit is applied, what a push of it would do is not known yet: "ahead of
  // Gerrit" and what its votes would do are not said. That Gerrit has a newer patchset still holds.
  const stopped = state.stopped;
  const status = commit.patchset_status;
  // Reworking, every commit replayed is ahead of Gerrit: the push says what it sends, not each stop.
  const reworking = state.task === "rework";
  if (status === "newer" && !stopped && !reworking) {
    notes.push({
      icon: "history",
      tone: "warn",
      text: "This commit is ahead of Gerrit",
      // Only rebased, Gerrit keeps the votes on the push; with new code it may not. The row says the same.
      detail: SYNC_WORDS[sync({ ...commit, diverged: false })],
    });
  } else if (status === "outdated" && state.reviewing) {
    // Checked out for review, and the author has pushed since: the one obvious thing to do.
    const newest = state.reviewing.newest;
    const label = newest ? `Update to patchset ${newest}` : "Update to the newest patchset";
    notes.push({
      icon: "history",
      tone: "warn",
      text: "Gerrit has a newer patchset than the one checked out",
      detail: "comments and CI below are about Gerrit's version",
      actions: [{ label, title: REVIEW_UPDATE.title, command: { id: REVIEW_UPDATE.command, args: [] }, primary: true }],
    });
  } else if (status === "outdated") {
    notes.push({ icon: "history", tone: "warn", text: "Gerrit has a newer patchset than this commit", detail: "comments and CI below are about Gerrit's version" });
  }
  if (state.blocked?.length && !state.elsewhere && !state.local && !state.reviewing && commit.own === false) {
    // Said before Review Change is pressed (P6): what would stand in its way here, and the way around it.
    const files = state.blocked.join(", ");
    notes.push({ icon: "warning", tone: "warn", text: "Uncommitted changes here", detail: "a review in this checkout needs them committed or stashed; Review Change offers the review worktree instead", raw: files });
  }
  if (state.elsewhere) {
    notes.push({ icon: "multiple-windows", tone: "info", text: "Under review in its own window", detail: "the review goes on there: Go to Review Window", raw: state.elsewhere });
  }
  // Closed while you look at it: no vote, no published comment; a draft of yours stays one, said with what can be done.
  const closed = commit.change_status === "ABANDONED" ? "abandoned" : commit.change_status === "MERGED" ? "merged" : undefined;
  const stranded = draftsIn(chains);
  if (closed && (state.reviewing || stranded)) {
    const kept = stranded ? `${stranded === 1 ? "your draft stays a draft" : `your ${stranded} drafts stay drafts`}: publishable once the change is restored, or delete ${stranded === 1 ? "it" : "them"}` : "";
    notes.push({ icon: "circle-slash", tone: "warn", text: `This change is ${closed}: Gerrit takes no vote and no comment on it`, detail: kept });
  }
  // Gerrit could not be asked: its state, votes and comments are unknown, said once with why and the ways on.
  const away = commit.gerrit_unavailable;
  if (away) {
    const problem = gerritProblem(away);
    notes.push({ icon: "warning", tone: "warn", text: UNKNOWN_WORDS.note, detail: "", raw: away.message, actions: problem.actions.map(({ primary: _primary, ...action }) => action) });
  }
  const since = state.delta?.since;
  // Someone's change, also one checked out for review (unless it is behind: the update says it).
  const theirs = !state.local || (state.reviewing !== undefined && !state.reviewing.behind);
  // What is new since your review, when something is (P4: no line that says nothing). Core's
  // `up_to_date` holds only when a patchset is compared; a staged review compares your checkout,
  // so the patchset you reviewed being the current one, or no file differing, says it too.
  const reviewed = theirs && state.from === "base" && since && since.patch_set !== null;
  const somethingNew = reviewed && !since.up_to_date && !since.identical && since.patch_set! < since.current_patch_set && (state.delta?.files.length ?? 0) > 0;
  const left = chainCounts(chains);
  const [open, fromGerrit] = [left.comments_unresolved, left.comments_total];
  const yours = chains.length - fromGerrit;
  const goneTo = chains.filter((chain) => toAnswer(chain) && state.visited?.has(chain.root_id)).length;
  const counts = [fromGerrit ? commentCounts(commit, left, goneTo) : "", yours ? `${yours} of yours, not published` : ""];
  const hasCi = commit.ci_failures.length > 0 || commit.ci_links.length > 0 || commit.attention_reasons.includes("ci-failed");
  const loading = state.loading;
  // Stopped before it is applied, what a push of it would do is not known yet. Reworking, the task
  // header and the Conflicts list say where the rebase stands: the head says what Gerrit has to say.
  const known = stopped ? commit.attention_reasons.filter((reason) => reason !== "ahead-of-gerrit") : commit.attention_reasons;
  const words = sentence(reasonWords(known));
  const quietStop = stopped && reworking;
  // Nothing calls for attention, and whether it is ready to submit is not known yet: no green check meanwhile.
  const unsure = !words && !commit.submittable && loading?.commit && !loading.ready;
  return {
    key: commit.change_id ?? commit.sha ?? "",
    loading: loading?.base && !state.base ? { message: true, readOnly: state.readOnly, plain: isPlain(state.readOnly) } : undefined,
    head: {
      ...(unsure ? { icon: "loading" } : headIcon(commit)),
      text: words || (commit.submittable ? "Ready to submit" : unsure ? "Loading…" : quietStop ? NOTHING_FROM_GERRIT : "Nothing to do"),
      // Unknown is not "not on Gerrit": the note under the head says why it is unknown.
      detail: commit.pushed ? commitVotes(commit) : status === "unknown" ? "" : "not on Gerrit",
      badges: commit.pushed ? badges(commit, loading?.commit ? 0 : draftsIn(chains)).filter((badge): badge is Badge => badge !== null) : [],
      ...(stopped && !quietStop ? stoppedHead(stopped) : {}),
    },
    conflicts: stopped?.unmerged.length ? { summary: plural(stopped.unmerged.length, "file"), rows: stopped.unmerged.map(conflictRow) } : undefined,
    notes,
    message: state.base?.message
      ? {
          key: commit.change_id ?? commit.sha ?? "",
          // Core takes the Change-Id footer out (`/diff`'s `message_text`) and puts it back on Apply.
          text: state.base.message_text ?? state.base.message,
          changeId: state.base.change_id ?? "",
          author: state.base.author ?? "",
          readOnly: state.readOnly,
          plain: isPlain(state.readOnly),
        }
      : undefined,
    compare: compareVm(state),
    hint: somethingNew
      ? { text: `Since your review: patchset ${since.patch_set} → ${since.current_patch_set} · ${plural(state.delta?.files.length ?? 0, "file")}${since.rebased ? " · rebased" : ""}`, show: true }
      : undefined,
    files: files(state),
    comments: loading?.commit
      ? { summary: LOADING, open: false, threads: [] }
      : { summary: counts.filter(Boolean).join(" · ") || (away ? UNKNOWN_WORDS.comments : "none"), open: open > 0 || yours > 0, threads: chains.map((chain) => thread(chain, state.local && commit.own !== false && !state.reviewing, state.patchset)) },
    ci: hasCi
      ? {
          // The head already says "CI failed": the section names the failing builds, or nothing.
          summary: commit.ci_failures
            .map((label) => (commit.ci_links.some((link) => link.label === label && link.verdict === "unstable") ? `${label} (unstable)` : label))
            .join(", "),
          state: state.ci.state,
          error: state.ci.state === "error" ? state.ci.error : "",
          // The builds and logs are the last pushed patchset's, true while stopped too; why there is
          // no log to read is about this commit's build, which there is not yet.
          noLinks: stopped ? "" : NO_CI_LINK,
          links: ciLinks(state.ci),
        }
      : undefined,
  };
}

/** What the Change view says while Gerrit cannot be asked: unknown, never none. */
export const UNKNOWN_WORDS = {
  note: "Gerrit could not be asked: what it says of this change (state, votes, comments) is unknown",
  comments: "unknown: Gerrit could not be asked",
};

/** The view's title bar: `Change <number>`. The subject is not repeated there: the commit message says it. */
export function changeHeading(number: number | null | undefined): string {
  return number ? `Change ${number}` : "Change";
}

/** The view as text, one line per row: what the end-to-end transcript shows of it. */
export function changeLines(vm: ChangeVm): string[] {
  if (vm.problem) {
    return problemLines(vm.problem);
  }
  if (vm.waiting || vm.empty) {
    return [vm.waiting?.text ?? vm.empty ?? ""];
  }
  const lines: string[] = [];
  const add = (depth: number, text: string, detail = "") => lines.push(`${"  ".repeat(depth)}${text}${detail ? ` — ${detail}` : ""}`);
  if (vm.head) {
    const buttons = actionsText(vm.head.actions);
    add(0, vm.head.text, `${vm.head.detail}${buttons ? `  ${buttons}` : ""}`);
  }
  if (vm.next) {
    add(0, `[${vm.next.label}]`, vm.next.title);
  }
  vm.notes?.forEach((note) => {
    const buttons = actionsText(note.actions);
    add(0, note.text, `${note.detail}${note.raw ? `  (${note.raw})` : ""}${buttons ? `  ${buttons}` : ""}`);
  });
  if (vm.message) {
    add(0, "Commit message", `${vm.message.text.split("\n")[0]}${vm.message.readOnly ? "  (read-only)" : "  (editable)"}`);
  } else if (vm.loading?.message) {
    add(0, "Commit message", LOADING);
  }
  if (vm.compare) {
    const label = (picker: { options: Option[]; selected: string }) => picker.options.find((option) => option.value === picker.selected)?.label ?? "";
    const { from, to, mode } = vm.compare;
    add(0, "Comparing", `${label(from)} → ${label(to)} · default: ${MODES[mode.mode]}${mode.active ? "" : " (picked by hand)"}`);
  }
  if (vm.hint) {
    add(0, vm.hint.text);
  }
  if (vm.conflicts) {
    add(0, "Conflicts", vm.conflicts.summary);
    vm.conflicts.rows.forEach((row) => add(1, `⚠ ${row.text}`, row.detail));
  }
  if (vm.files) {
    add(0, vm.files.title, vm.files.note || `${vm.files.rows.length} · +${vm.files.added} −${vm.files.deleted}`);
    for (const file of vm.files.rows) {
      // Not whether it was seen: that comes a moment after its diff is laid out, and the transcript would race it.
      add(1, file.name, `${file.dir ? `${file.dir} · ` : ""}${file.state} ${file.added === null ? "binary" : `+${file.added} −${file.deleted}`}`);
    }
  }
  if (vm.comments) {
    add(0, "Comments", vm.comments.summary);
    for (const row of vm.comments.threads) {
      const state = row.resolved ? "resolved" : row.open ? (row.held ? "open, kept for you" : "open") : "yours";
      add(1, row.text, `${[...row.notes, row.replies.length ? plural(row.replies.length, "reply").replace("replys", "replies") : ""].filter(Boolean).join(" · ")}  [${state}]`);
      row.replies.forEach((reply) => add(2, reply.author, `${reply.state ? `[${reply.state}] ` : ""}${reply.text.split("\n")[0]}`));
    }
  }
  if (vm.ci) {
    add(0, "CI", vm.ci.state === "error" ? vm.ci.error : vm.ci.summary);
    if (vm.ci.state === "loaded" && !vm.ci.links.length && vm.ci.noLinks) {
      add(1, vm.ci.noLinks);
    }
    for (const link of vm.ci.links) {
      add(1, link.text, link.detail);
      link.findings.forEach((row) => add(2, row.text, row.notes.join(" · ")));
      if (link.warnings.length) {
        add(2, plural(link.warnings.length, "warning"), "collapsed");
      }
    }
  }
  return lines;
}

// --- Files seen (filesSeen.ts keeps the ticks) ------------------------------------------

/** Which version's files: Gerrit's change and patchset when it has them, else only a key here. */
export interface SeenWhere {
  key: string;
  number?: number;
  patchSet?: number;
}

/**
 * Where the ticks of the version looked at are kept: Gerrit's patchset when the version is one
 * (the change a staged review holds is its patchset), else only here, under the change and version.
 */
export function seenWhere(
  target: { number?: number; changeId?: string | null; rev: string; local: boolean } | undefined,
  version: string | number,
  patchSet: { staged?: number; current?: number | null },
): SeenWhere | undefined {
  if (!target) {
    return undefined;
  }
  const number = target.number;
  const ps = typeof version === "number" ? version : patchSet.staged ?? (target.local ? undefined : (patchSet.current ?? undefined));
  return number && ps ? { key: `${number}/${ps}`, number, patchSet: ps } : { key: `${target.changeId ?? target.rev}/${version}` };
}

/**
 * Where a thread was written, when that is not where it is drawn (W10): on an earlier patchset, at
 * its line there; placed by its lines when ger could map them (`worktree`), else said to be outdated.
 */
export function placeWords(chain: Pick<CommentChain, "line" | "comments" | "worktree">, outdated: boolean, shownPatchset?: number): string {
  const written = chain.comments[0]?.patch_set;
  const older = written != null && shownPatchset !== undefined && written !== shownPatchset;
  const at = chain.line ? `, line ${chain.line}` : "";
  if (older && (outdated || !chain.worktree)) {
    return ` · written on patchset ${written}${at}: outdated, that line may be gone or moved`;
  }
  if (older) {
    return ` · written on patchset ${written}${at}, placed where those lines are now`;
  }
  // Its lines changed in your file since the version it was written on: drawn where they were.
  return outdated ? ` · outdated: line ${chain.line} has changed since` : "";
}
