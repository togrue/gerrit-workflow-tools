// Local stack at a glance, as `ger log` heads and splits it: one line saying how much of the stack
// is where (`6 on Gerrit · 1 to upload · 2 held back · 1 failed CI`, its words and order those of
// `ger log`'s summary), and the ready boundary drawn between the commits a push sends and those it
// holds back. Counted from core's verdict on each commit (`held_back`, `patchset_status`,
// `attention_reasons`); nothing is judged here. Pure: no `vscode`, no I/O.

import { StackCommit } from "./api";

/** The ready boundary's line, as `ger log` draws it; Local stack lists the newest first, so push sends what is below it. */
export const BOUNDARY_WORDS = {
  text: "ready boundary",
  tooltip: "Ready boundary: Push sends the commits below it. Those above are held back (a WIP or stop-pattern subject, a commit not ready yet): ger push does not send them.",
};

const MERGED = new Set(["merged", "merged-same", "merged-drift", "merged-unknown"]);

/** `ger log`'s summary line, without the `summary:` label: empty for an empty stack. */
export function stackSummary(commits: readonly StackCommit[]): string {
  const merged = (commit: StackCommit) => MERGED.has(commit.patchset_status);
  const has = (commit: StackCommit, reason: string) => commit.attention_reasons.includes(reason);
  const count = (keep: (commit: StackCommit) => boolean) => commits.filter(keep).length;
  const commented = count((c) => has(c, "unresolved-comments"));
  const drafted = count((c) => has(c, "drafts-to-publish"));
  const parts: [number, string][] = [
    [count((c) => c.pushed && !c.abandoned && !merged(c)), "on Gerrit"],
    [count((c) => !c.held_back && (c.patchset_status === "absent" || c.patchset_status === "newer")), "to upload"],
    [count((c) => c.held_back), "held back"],
    [count(merged), "merged"],
    [count((c) => c.abandoned), "abandoned"],
    [count((c) => c.pushed && has(c, "ci-failed")), "failed CI"],
    [commented, `${commented === 1 ? "commit" : "commits"} with comments`],
    [drafted, `${drafted === 1 ? "commit" : "commits"} with drafts to publish`],
  ];
  return parts
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`)
    .join(" · ");
}

/**
 * The row the ready boundary is drawn under, Local stack's rows being newest first: the oldest
 * commit held back. None when nothing is held back. `keys` are the rows' keys, newest first.
 */
export function boundaryUnder(commits: readonly StackCommit[], keys: readonly string[]): string | undefined {
  // Oldest first, as core lists them: the first held back is where the boundary falls.
  const at = commits.findIndex((commit) => commit.held_back);
  return at < 0 ? undefined : keys[commits.length - 1 - at];
}
