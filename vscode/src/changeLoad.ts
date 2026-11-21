// Which change the Change view shows, and what of it has arrived. A click draws the view at once
// from what the clicked row knows; each answer of ger serve fills in its part as it comes, and an
// answer for a change selected before is dropped. Pure: no `vscode`, no I/O.

import type { Chain, CommitStatus, Diff, Member, ShownCommit, Stack } from "./api";
import type { ChangeState, Loading, Stopped } from "./changeRows";
import type { ChangeTarget } from "./model";
import { stoppedAt } from "./stackRows";

/** What a row of Local stack or Review chains already says about its change. Review chains do not say whether it is ready to submit. */
export type RowStatus = Omit<CommitStatus, "submittable"> & { submittable?: boolean };

/**
 * What a Review chains row says about a change only on Gerrit: its votes, unresolved comments
 * and attention. The rest is what being in a review chain means: an open change, on Gerrit,
 * looked at as its current patchset. What the row does not say (CI links, reviewers, whether
 * it is ready to submit) is empty until `/show` answers, and the view says it is loading. A
 * member that is a commit of your local stack is drawn from that commit instead (`memberTarget`).
 */
export function memberStatus(member: Member): RowStatus {
  const sha = member.sha;
  return {
    sha,
    short_sha: sha?.slice(0, 7) ?? "",
    change_id: member.change_id,
    summary: member.subject,
    pushed: true,
    patchset_status: "active",
    verified: member.verified,
    code_review: member.code_review,
    comments_unresolved: member.comments_unresolved,
    ci_failures: [],
    ci_links: [],
    gerrit_url: member.url,
    number: member.number,
    last_pushed_votes: null,
    code_change: null,
    work_in_progress: member.attention_reasons.includes("wip"),
    attention_reasons: member.attention_reasons,
    severity: member.severity,
    change_status: "NEW",
    updated: null,
  };
}

function memberRev(member: Member): string {
  return member.local_sha ?? `change:${member.number}`;
}

/**
 * What a click on `member` opens. A member that is a commit of your local stack is drawn from
 * that commit as `stack` (ger serve's) says it, ahead of Gerrit or not; any other from its row.
 */
export function memberTarget(member: Member, chain: Chain, stack: Stack | undefined): ChangeTarget {
  const local = member.local_sha ? stack?.commits.find((commit) => commit.sha === member.local_sha) : undefined;
  return {
    rev: memberRev(member),
    title: `${member.number}: ${member.subject}`,
    number: member.number,
    url: member.url,
    local: member.local_sha !== null,
    changeId: member.change_id,
    sha: member.local_sha ?? member.sha,
    chainRevs: chain.members.map(memberRev),
    chainKey: chain.key,
    // A commit of your stack the stack does not list (it could not be read): nothing to draw from until /show answers.
    known: member.local_sha ? local : memberStatus(member),
  };
}

/** One answer of a load: the commit (`/show`), its files against its parent, the review delta. */
export type Part = "commit" | "base" | "delta";

/** The commit as the row said it, for the view to draw until `/show` answers. */
export function standIn(known: RowStatus, local: boolean): ShownCommit {
  return { ...known, submittable: known.submittable ?? false, local_commit: local, comment_chains: [] };
}

/**
 * Whether two targets name the same change, seen the same way (your commit, or Gerrit's): every
 * click builds a new target, so the change already shown is recognised by its Change-Id or
 * number, not by the object.
 */
export function sameChange(a: ChangeTarget, b: ChangeTarget): boolean {
  if (a.local !== b.local) {
    return false;
  }
  if (a.changeId && b.changeId) {
    return a.changeId === b.changeId;
  }
  return a.number !== undefined && b.number !== undefined ? a.number === b.number : a.rev === b.rev;
}

/**
 * The commits `/show` is asked for a change's CI findings, base first: the change and the commit
 * below it, as a chain. Whether a finding was introduced turns on the parent's CI alone, and every
 * other member would cost its build logs and its comments (issue #40). The change is last. A
 * commit of your local stack that the stack held here does not list (not loaded yet, or from before
 * an amend) is looked up in the live stack instead (`stack`), slower but right. Anything else with
 * no chain to place it in is asked alone, and nothing is said about what it introduced.
 */
export function ciRevs(target: ChangeTarget, stack: Stack | undefined): { revs: string[]; chain: boolean; stack: boolean } {
  const below = target.chainRevs?.includes(target.rev) ? target.chainRevs : target.local ? stack?.commits.map((c) => c.sha) : undefined;
  const index = below ? below.indexOf(target.rev) : -1;
  if (below && index >= 0) {
    return { revs: below.slice(Math.max(0, index - 1), index + 1), chain: true, stack: false };
  }
  return target.local ? { revs: [], chain: false, stack: true } : { revs: [target.rev], chain: false, stack: false };
}

/**
 * The rebase underway stopped at the commit `target` names before applying it: on a conflict, or
 * with the conflicts resolved. Core's verdict, read from the stack the extension already holds
 * (the commit's `rebase` step, the stack's `stop` and `unmerged`), as Local stack reads it for the
 * row; undefined for anything else.
 */
export function stoppedHere(target: ChangeTarget, stack: Stack | undefined): Stopped | undefined {
  const commit = target.local ? stack?.commits.find((c) => c.sha === target.rev) : undefined;
  const stop = commit && stack ? stoppedAt(commit, stack) : null;
  return stop === "conflict" || stop === "resolved" ? { stop, unmerged: stack?.rebase?.unmerged ?? [], actions: stack?.rebase?.actions ?? [] } : undefined;
}

export class ChangeDetail {
  target: ChangeTarget | undefined;
  /** `/show`'s answer; undefined until it has come. */
  shown: ShownCommit | undefined;
  /** The version looked at against its parent: its files, and the commit whose lines comments sit on. */
  diff: Diff | undefined;
  /** The review delta: for the mode chosen, or (someone's change) what is new since your review. */
  delta: Diff | undefined;
  problem: string | undefined;
  /** Asked for, not answered yet, and nothing shown in their place. */
  private readonly waiting = new Set<Part>();
  /** The selection token: each select and clear takes a new one, and an answer carries the one it was asked under. */
  private token = 0;

  /**
   * Show `target`; the token its answers must carry. Another change starts empty, drawn from
   * what its row knows. The change already shown (`sameChange`), or with `keep` the same change
   * under a new commit id, keeps what is drawn until the answers replace it: no loading frame.
   */
  select(target: ChangeTarget, keep: boolean): number {
    if (!keep && !(this.target && sameChange(this.target, target))) {
      [this.shown, this.diff, this.delta, this.problem] = [undefined, undefined, undefined, undefined];
      this.waiting.clear();
      (["commit", "base", "delta"] as const).forEach((part) => this.waiting.add(part));
    }
    this.target = target;
    return ++this.token;
  }

  /** Show nothing; answers still on their way are dropped. */
  clear(): number {
    [this.target, this.shown, this.diff, this.delta, this.problem] = [undefined, undefined, undefined, undefined, undefined];
    this.waiting.clear();
    return ++this.token;
  }

  /** The token of the selection now: for an answer asked outside a load (the CI findings). */
  selection(): number {
    return this.token;
  }

  /** Whether `token` is the selection's: what was asked under an older one is not shown. */
  current(token: number): boolean {
    return token === this.token;
  }

  /** The answer for `part`, taken only when it is for the change selected now; whether it was taken. */
  arrive(token: number, part: "commit", value: ShownCommit): boolean;
  arrive(token: number, part: "base" | "delta", value: Diff | undefined): boolean;
  arrive(token: number, part: Part, value: ShownCommit | Diff | undefined): boolean {
    if (!this.current(token)) {
      return false;
    }
    if (part === "commit") {
      [this.shown, this.problem] = [value as ShownCommit, undefined];
    } else if (part === "base") {
      this.diff = value as Diff | undefined;
    } else {
      this.delta = value as Diff | undefined;
    }
    this.waiting.delete(part);
    return true;
  }

  /** The change cannot be shown: why. Taken, as an answer is, only for the change selected now. */
  fail(token: number, problem: string): boolean {
    if (!this.current(token)) {
      return false;
    }
    this.problem = problem;
    this.waiting.clear();
    return true;
  }

  /** The commit to draw: as `/show` answered, else as its row said; undefined with neither. */
  commit(): ShownCommit | undefined {
    const known = this.target?.known;
    return this.shown ?? (known && this.target ? standIn(known, this.target.local) : undefined);
  }

  /** What the view draws for the selection, `view` saying how it is looked at; undefined with nothing to draw. */
  state(view: Omit<ChangeState, "local" | "shown" | "base" | "delta" | "loading">): ChangeState | undefined {
    const shown = this.commit();
    if (!this.target || !shown) {
      return undefined;
    }
    return { ...view, local: this.target.local, shown, base: this.diff, delta: this.delta, loading: this.loading() };
  }

  /** What is still on its way; undefined once everything has come. */
  loading(): Loading | undefined {
    if (!this.waiting.size) {
      return undefined;
    }
    const ready = this.shown !== undefined || this.target?.known?.submittable !== undefined;
    return { commit: this.waiting.has("commit"), base: this.waiting.has("base"), delta: this.waiting.has("delta"), ready };
  }
}
