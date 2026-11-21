// Which change the Change view shows, and what of it has arrived. A click draws the view at once
// from what the clicked row knows; each answer of ger serve fills in its part as it comes, and an
// answer for a change selected before is dropped. Pure: no `vscode`, no I/O.

import type { CommitStatus, Diff, Member, ShownCommit } from "./api";
import type { ChangeState, Loading } from "./changeRows";
import type { ChangeTarget } from "./model";

/** What a row of Local stack or Review chains already says about its change. Review chains do not say whether it is ready to submit. */
export type RowStatus = Omit<CommitStatus, "submittable"> & { submittable?: boolean };

/** What a Review chains row says about a change: its votes, unresolved comments and attention; the commit is your local one when there is one. */
export function memberStatus(member: Member): RowStatus {
  const sha = member.local_sha ?? member.sha;
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
    work_in_progress: member.attention_reasons.includes("wip"),
    attention_reasons: member.attention_reasons,
    severity: member.severity,
    change_status: "NEW",
    updated: null,
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
