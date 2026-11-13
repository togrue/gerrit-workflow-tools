// What the "Change" view shows, as plain data: what the change needs, its commit message, which
// version is compared with what, the files with their line counts, the comment chains and the
// CI findings. The webview draws it, the end-to-end suite prints it. Pure: no `vscode`, no I/O.

import * as path from "node:path";

import { CiContent, CiFinding, CommentChain, Diff, Patchsets, ShownCommit } from "./api";
import { Severity, commitVotes, fileState, plural, reasonWords, sentence, severity } from "./format";
import { Tone } from "./stackRows";
import { Waiting } from "./state";

/** Which version of the change is looked at: your commit, Gerrit's current patchset, or patchset `n`. */
export type Version = "local" | "current" | number;
/** What it is compared with: its parent, the patchset you last reviewed, or (your commit only) Gerrit's current patchset. */
export type Mode = "base" | "reviewed" | "gerrit";

export const MODES: Record<Mode, string> = {
  base: "Against its base commit",
  reviewed: "Since my last review",
  gerrit: "Gerrit vs. local",
};

export const NOT_FILES = ["/COMMIT_MSG", "/PATCHSET_LEVEL", "/MERGE_LIST"];

export type CiView =
  | { state: "closed" | "loading" }
  | { state: "error"; error: string }
  | { state: "loaded"; contents: CiContent[]; introduced: Set<string> | null };

/** Everything the view is built from. */
export interface ChangeState {
  /** A commit of your local stack. */
  local: boolean;
  shown: ShownCommit;
  /** Comment chains, in the order shown. */
  chains: CommentChain[];
  /** The version looked at against its parent; it carries the commit message. */
  base: Diff | undefined;
  /** The review delta: for the mode chosen, or (someone's change) since your review. */
  delta: Diff | undefined;
  patchsets: Patchsets | undefined;
  version: Version;
  mode: Mode;
  /** Why the commit message cannot be edited; empty when it can. */
  readOnly: string;
  ci: CiView;
}

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
}

export interface ReplyRow {
  author: string;
  /** `draft`, `local-draft`, `note`; empty for a published comment. */
  state: string;
  text: string;
}

export interface ThreadRow extends Icon {
  id: string;
  text: string;
  notes: string[];
  tooltip: string;
  /** Somebody's chain on Gerrit: it can be answered. */
  reply: boolean;
  /** It is still open: "Done" resolves it, and it can be kept from the agent. */
  open: boolean;
  held: boolean;
  resolved: boolean;
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

export interface Option {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface ChangeVm {
  waiting?: Waiting;
  /** Nothing is shown: what to do to see a change. */
  empty?: string;
  problem?: string;
  head?: Icon & { text: string; detail: string; tooltip: string };
  notes?: (Icon & { text: string; detail: string })[];
  message?: { key: string; text: string; author: string; readOnly: string };
  versions?: { options: Option[]; selected: string };
  modes?: { options: Option[]; selected: Mode };
  /** Someone's change you reviewed before: what is new, one click away. */
  hint?: { text: string; show: boolean };
  files?: { title: string; summary: string; added: number; deleted: number; note: string; tooltip: string; rows: FileRow[] };
  comments?: { summary: string; open: boolean; threads: ThreadRow[] };
  ci?: { summary: string; state: CiView["state"]; error: string; links: CiLinkRow[] };
}

const SEVERITY_ICON: Record<Severity, Icon> = {
  error: { icon: "error", tone: "bad" },
  warning: { icon: "comment-unresolved", tone: "warn" },
  pending: { icon: "clock" },
  ok: { icon: "pass", tone: "good" },
};

const YOURS: Record<string, string> = { note: "your private note", "local-draft": "your local draft", draft: "your draft" };

/** A chain you started and have not published: a draft, a local draft or a private note. */
export function isYours(chain: CommentChain): boolean {
  return (chain.comments[0]?.state ?? "published") !== "published";
}

function versions(state: ChangeState): { options: Option[]; selected: string } {
  const { shown, patchsets } = state;
  const options: Option[] = [];
  if (state.local) {
    options.push({ value: "local", label: `Local commit ${shown.short_sha}` });
  }
  if (shown.pushed && patchsets) {
    for (const patchset of [...patchsets.patchsets].reverse()) {
      const marks = [patchset.number === patchsets.current ? "current" : "", patchset.number === patchsets.reviewed ? "you reviewed it" : ""].filter(Boolean);
      options.push({ value: String(patchset.number), label: `Patchset ${patchset.number}${marks.length ? ` (${marks.join(", ")})` : ""}` });
    }
  } else if (shown.pushed) {
    options.push({ value: "current", label: "Gerrit's current patchset" });
  }
  const current = state.version === "current" && patchsets?.current ? patchsets.current : state.version;
  const wanted = String(current);
  return { options, selected: options.some((option) => option.value === wanted) ? wanted : (options[0]?.value ?? "") };
}

function modes(state: ChangeState): { options: Option[]; selected: Mode } {
  const pushed = state.shown.pushed;
  const never = state.patchsets !== undefined && state.patchsets.reviewed === null;
  return {
    options: [
      { value: "base", label: MODES.base },
      { value: "reviewed", label: never ? `${MODES.reviewed} (you have not reviewed it)` : MODES.reviewed, disabled: !pushed || never },
      { value: "gerrit", label: MODES.gerrit, disabled: !pushed || !state.local },
    ],
    selected: state.mode,
  };
}

function files(state: ChangeState): NonNullable<ChangeVm["files"]> {
  const diff = state.mode === "base" ? state.base : state.delta;
  const since = diff?.since;
  const to = since?.to_patch_set ? `patchset ${since.to_patch_set}` : "local";
  let [title, note, tooltip] = ["Files", "", "What the change does: its files against the parent commit."];
  if (state.mode !== "base") {
    title = state.mode === "gerrit" ? "Gerrit vs. local" : "Since your review";
    tooltip = since?.rebased
      ? "The two versions sit on different parents. Only files the change touches are listed; inside them, edits that came in from underneath show too."
      : state.mode === "gerrit"
        ? "What your commit changes compared with the patchset Gerrit has: what a push would upload on top."
        : "What changed after the patchset you last voted or commented on.";
    if (!diff) {
      note = "could not be compared";
    } else if (since?.patch_set === null || since?.patch_set === undefined) {
      note = "you have not reviewed this change yet";
    } else if (since.up_to_date) {
      note = `nothing new: you reviewed patchset ${since.patch_set}`;
    } else {
      title = `${title}: patchset ${since.patch_set} → ${to}${since.rebased ? " (rebased)" : ""}`;
    }
  } else if (!diff) {
    note = "the commit is not available here";
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
    };
  });
  const sum = (pick: (row: FileRow) => number | null) => rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
  const [added, deleted] = [sum((row) => row.added), sum((row) => row.deleted)];
  return { title, summary: note || plural(rows.length, "file"), added, deleted, note, tooltip, rows };
}

function thread(chain: CommentChain): ThreadRow {
  const first = chain.comments[0];
  const where = NOT_FILES.includes(chain.path) ? "whole change" : `${path.posix.basename(chain.path)}${chain.line ? `:${chain.line}` : ""}`;
  const replies = chain.comments.slice(1);
  const mine = isYours(chain) ? YOURS[first?.state ?? ""] : undefined;
  const answered = !chain.resolved && chain.resolved_after_publish;
  const held = chain.held && !chain.resolved;
  const notes = [mine ?? "", held ? "kept for you" : "", answered ? "your draft resolves it" : "", where, mine ? "" : (first?.author ?? "")];
  const icon: Icon = mine
    ? { icon: first?.state === "note" ? "note" : "edit" }
    : chain.resolved
      ? { icon: "pass", tone: "dim" }
      : answered
        ? { icon: "edit", tone: "good" }
        : held
          ? { icon: "person", tone: "warn" }
          : { icon: "comment-unresolved", tone: "warn" };
  return {
    ...icon,
    id: chain.root_id,
    text: first?.message.split("\n")[0] ?? where,
    notes: notes.filter(Boolean),
    tooltip: chain.comments.map((c) => `${c.author ?? "you"}${c.state === "published" ? "" : ` (${c.state})`}:\n${c.message}`).join("\n\n"),
    // Your own unpublished chain: nobody to reply to, nothing for the agent to keep from.
    reply: !mine,
    open: !mine && !chain.resolved,
    held: Boolean(chain.held),
    resolved: chain.resolved,
    replies: replies.map((c) => ({ author: c.author ?? (c.state === "published" ? "?" : "you"), state: c.state === "published" ? "" : c.state, text: c.message })),
  };
}

function finding(item: CiFinding, introduced: boolean | null): FindingRow {
  const where = item.path ? `${item.path}${item.line ? `:${item.line}` : ""}` : "";
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
    const rows = content.findings.map((item) => ({ item, introduced: ci.introduced ? ci.introduced.has(item.fingerprint) : null }));
    // What this change broke comes first; what it inherited from below follows. Errors before warnings.
    const rank = (row: (typeof rows)[number]) => (row.introduced ? 0 : 2) + (row.item.severity === "warning" ? 1 : 0);
    rows.sort((a, b) => rank(a) - rank(b));
    const collapsed = (row: (typeof rows)[number]) => Boolean(content.collapse_warnings) && row.item.severity === "warning";
    const leading = rows.filter((row) => !collapsed(row));
    const warnings = rows.filter(collapsed);
    const counted = [plural(leading.length, "finding"), warnings.length ? plural(warnings.length, "warning") : ""];
    return {
      icon: failed ? "warning" : "beaker",
      tone: failed ? "warn" : undefined,
      text: content.provenance.build ?? content.label,
      detail: failed ? `could not be read: ${content.provenance.error}` : counted.filter(Boolean).join(" · "),
      url: content.provenance.url,
      tooltip: `${content.provenance.url}${content.provenance.truncated ? "\nThe log was cut: findings may be incomplete." : ""}`,
      findings: leading.map(({ item, introduced }) => finding(item, introduced)),
      warnings: warnings.map(({ item, introduced }) => finding(item, introduced)),
    };
  });
}

export function changeVm(state: ChangeState): ChangeVm {
  const { shown: commit, chains } = state;
  const notes: NonNullable<ChangeVm["notes"]> = [];
  if (commit.patchset_status === "newer") {
    notes.push({
      icon: "history",
      tone: "warn",
      text: "This commit is ahead of Gerrit",
      // Only rebased, Gerrit keeps the votes on the push; with new code it may not.
      detail: commit.last_pushed_votes
        ? "its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push"
        : "only rebased: the votes stay when you push",
    });
  } else if (commit.patchset_status === "outdated") {
    notes.push({ icon: "history", tone: "warn", text: "Gerrit has a newer patchset than this commit", detail: "comments and CI below are about Gerrit's version" });
  }
  const since = state.delta?.since;
  const reviewed = !state.local && state.mode === "base" && since && since.patch_set !== null;
  const open = chains.filter((chain) => !chain.resolved && !isYours(chain)).length;
  const fromGerrit = chains.filter((chain) => !isYours(chain)).length;
  const yours = chains.length - fromGerrit;
  const counts = [fromGerrit ? `${open} unresolved of ${fromGerrit}` : "", yours ? `${yours} of yours, not published` : ""];
  const hasCi = commit.ci_failures.length > 0 || commit.ci_links.length > 0 || commit.verified === -1;
  return {
    head: {
      ...SEVERITY_ICON[severity(commit.attention_reasons)],
      text: sentence(reasonWords(commit.attention_reasons)) || (commit.submittable ? "Ready to submit" : "Nothing to do"),
      detail: commit.pushed ? commitVotes(commit) : "not on Gerrit",
      tooltip: commit.summary,
    },
    notes,
    message: state.base?.message
      ? { key: commit.change_id ?? commit.sha ?? "", text: state.base.message, author: state.base.author ?? "", readOnly: state.readOnly }
      : undefined,
    versions: versions(state),
    modes: modes(state),
    hint: reviewed
      ? {
          text: since.up_to_date
            ? `Nothing new since your review of patchset ${since.patch_set}`
            : `Since your review: patchset ${since.patch_set} → ${since.current_patch_set} · ${plural(state.delta?.files.length ?? 0, "file")}${since.rebased ? " · rebased" : ""}`,
          show: !since.up_to_date,
        }
      : undefined,
    files: files(state),
    comments: { summary: counts.filter(Boolean).join(" · ") || "none", open: open > 0 || yours > 0, threads: chains.map(thread) },
    ci: hasCi
      ? {
          summary: commit.ci_failures.join(", ") || "failed",
          state: state.ci.state,
          error: state.ci.state === "error" ? state.ci.error : "",
          links: ciLinks(state.ci),
        }
      : undefined,
  };
}

/** The view as text, one line per row: what the end-to-end transcript shows of it. */
export function changeLines(vm: ChangeVm): string[] {
  if (vm.waiting || vm.empty || vm.problem) {
    return [vm.waiting?.text ?? vm.problem ?? vm.empty ?? ""];
  }
  const lines: string[] = [];
  const add = (depth: number, text: string, detail = "") => lines.push(`${"  ".repeat(depth)}${text}${detail ? ` — ${detail}` : ""}`);
  if (vm.head) {
    add(0, vm.head.text, vm.head.detail);
  }
  vm.notes?.forEach((note) => add(0, note.text, note.detail));
  if (vm.message) {
    add(0, "Commit message", `${vm.message.text.split("\n")[0]}${vm.message.readOnly ? "  (read-only)" : "  (editable)"}`);
  }
  const version = vm.versions?.options.find((option) => option.value === vm.versions?.selected);
  if (version && vm.modes) {
    add(0, "Showing", `${version.label} · ${MODES[vm.modes.selected]}`);
  }
  if (vm.hint) {
    add(0, vm.hint.text);
  }
  if (vm.files) {
    add(0, vm.files.title, vm.files.note || `${vm.files.rows.length} · +${vm.files.added} −${vm.files.deleted}`);
    for (const file of vm.files.rows) {
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
    if (vm.ci.state === "loaded" && !vm.ci.links.length) {
      add(1, "No CI link was found for the failed build");
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
