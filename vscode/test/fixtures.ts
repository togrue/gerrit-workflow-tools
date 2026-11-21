// What `ger serve` answers, in the shapes of src/api.ts: a commit, a stack, a diff, a comment chain,
// a CI log. The unit tests of the view models and the gallery's scenarios (test/gallery) build on
// the same ones, so neither can drift from the API types.

import * as fs from "node:fs";
import * as path from "node:path";

import { Attended, CiContent, CiFinding, Comment, CommentChain, Diff, Patchsets, Severity, ShownCommit, Stack, StackCommit } from "../src/api";
import { ChangeState } from "../src/changeRows";

/** What core says each attention reason weighs (`REASON_SEVERITY`); tests/test_attention_severity.py holds attention.json to it. */
const REASON_SEVERITY: Record<string, Severity> = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "test", "attention.json"), "utf8"));
const ORDER: Severity[] = ["error", "warning", "pending", "ok"];

/** `reasons` with the severity `ger serve` gives them (`attention_severity`): a reason core does not know is a warning. */
export function attended(reasons: string[]): Attended {
  const found = reasons.map((reason) => ORDER.indexOf(REASON_SEVERITY[reason] ?? "warning"));
  return { attention_reasons: reasons, severity: ORDER[Math.min(ORDER.length - 1, ...found)] };
}

export function commit(summary: string, extra: Partial<StackCommit> = {}): StackCommit {
  const sha = summary.toLowerCase().replace(/\W/g, "").padEnd(40, "0");
  return {
    sha,
    short_sha: sha.slice(0, 7),
    change_id: `I${sha}`,
    summary,
    pushed: true,
    patchset_status: "active",
    verified: null,
    code_review: null,
    comments_unresolved: 0,
    ci_failures: [],
    ci_links: [],
    gerrit_url: "https://g/c/1",
    number: 1,
    last_pushed_votes: null,
    submittable: false,
    work_in_progress: false,
    change_status: "NEW",
    updated: null,
    abandoned: false,
    reviewers: [],
    is_head: false,
    held_back: false,
    needs_edit: false,
    ...extra,
    ...attended(extra.attention_reasons ?? []),
  };
}

/** A local stack of `commits`, oldest first. */
export function stack(commits: StackCommit[], extra: Partial<Stack> = {}): Stack {
  return {
    branch: "feature",
    head: commits.at(-1)?.sha ?? null,
    rebase_in_progress: false,
    stack: { project: "p", target_branch: "origin/main", push_branch: "main" },
    summary: { commits: commits.length, ready: 0, attention: 0, needs_edit: 0, not_on_gerrit: 0 },
    commits,
    ...extra,
  };
}

/** The conflict preview when no two neighbours conflict. */
export const never = () => undefined;

export const shown = (extra: Partial<ShownCommit> = {}): ShownCommit => ({
  ...commit("Extract routing"),
  local_commit: true,
  comment_chains: [],
  ...extra,
  ...attended(extra.attention_reasons ?? []),
});

export function diff(extra: Partial<Diff> = {}): Diff {
  return {
    commit: "c".repeat(40),
    base: "b".repeat(40),
    message: "Extract routing\n\nChange-Id: I1",
    author: "Me <me@example.com>",
    local_commit: true,
    files: [
      { path: "src/route.py", status: "M", old_path: null, insertions: 12, deletions: 3 },
      { path: "logo.png", status: "A", old_path: null, insertions: null, deletions: null },
    ],
    ...extra,
  };
}

/** What the Change view is built from: a commit of your stack, everything arrived, nothing compared but its parent. */
export function state(extra: Partial<ChangeState> = {}): ChangeState {
  return { local: true, shown: shown(), chains: [], base: diff(), delta: undefined, patchsets: undefined, version: "local", mode: "base", readOnly: "", ci: { state: "closed" }, ...extra };
}

export const patchsets: Patchsets = { number: 1, current: 3, reviewed: 2, patchsets: [1, 2, 3].map((number) => ({ number, commit: String(number).repeat(40), created: null })) };

/** A published comment by Alice on `a.py:4`. */
export function comment(message: string, extra: Partial<Comment> = {}): Comment {
  return { path: "a.py", line: 4, message, text: message, markdown: message, patch_set: 1, state: "published", url: null, author: "Alice", ...extra };
}

/** An open comment chain on `a.py:4`. */
export function chain(root_id: string, comments: Comment[], extra: Partial<CommentChain> = {}): CommentChain {
  return {
    root_id,
    path: "a.py",
    line: 4,
    start_line: null,
    url: null,
    resolved: false,
    resolved_after_publish: false,
    outdated: false,
    held: false,
    comments,
    ...extra,
  };
}

export function finding(message: string, severity: "error" | "warning", fingerprint: string, extra: Partial<CiFinding> = {}): CiFinding {
  return { tool: "cc", kind: severity, path: "src/main.c", line: 3, column: 1, message, detail: null, fingerprint, severity, ...extra };
}

/** One build's log, read. */
export function content(collapse_warnings: boolean, findings: CiFinding[], extra: Partial<CiContent["provenance"]> = {}): CiContent {
  const url = "https://ci.example.org/job/Widget/55/console";
  return {
    label: "jenkins",
    url,
    provenance: { url, provider: "jenkins", build: "Widget #55", fetched_at: null, truncated: false, cached: false, error: null, ...extra },
    findings,
    collapse_warnings,
  };
}
