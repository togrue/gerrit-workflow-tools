// The task header: while you review a chain (a staged review here) or rework your own (a rebase
// underway), a compact block at the top of the side bar says the mode in words, where you are in
// the chain and how much is left, and names the ways out in words. It lists the chain's changes in
// reading order (oldest first), each one's state by an icon and a word: reviewing, your vote and
// the comments; reworking, where the rebase stands on each commit. Pure: no `vscode`, no I/O;
// task.ts holds the state, common.js draws it at the top of the Change view.

import { Overview, Stack, StackCommit } from "./api";
import { Commented, commentState, commentTurns } from "./commentState";
import { plural } from "./format";
import { reviewFocus } from "./reviewFocus";
import { reviewTask } from "./reviewTask";
import { REBASE_ACTIONS, Tone } from "./stackRows";

export type TaskMode = "review" | "rework";

/** A change of the chain under review, as Gerrit's related changes have it: closed ones too. */
export interface ChainMemberStatus {
  number: number;
  subject: string;
  status: "NEW" | "MERGED" | "ABANDONED";
}

/** One state of a change: an icon and a word, never the colour alone. */
export interface TaskMark {
  icon: string;
  text: string;
  tone?: Tone | "dim";
  /** Comments: the ring of commentState.ts instead of an icon. */
  ring?: { share: number; shape: string };
}

export interface TaskStep {
  /** What a click sends back: the change number (review) or the commit (rework). */
  key: string;
  position: number;
  title: string;
  current: boolean;
  /** Reworking, where the rebase stands on it: `todo` is drawn dimmed, besides its word. */
  phase?: "done" | "current" | "todo";
  marks: TaskMark[];
  tooltip: string;
}

export interface TaskAction {
  label: string;
  /** What it does, in words, beside the button: no need to hover. */
  detail: string;
  command: string;
  /** Why it would not run now, in one line: drawn disabled, with the reason as its detail. */
  notNow?: string;
  /** The whole of it on hover: core's refusal, with the commands (`git add`, `ger rebase --continue`). */
  tooltip?: string;
  /** The next step on the screen (changeScreen.ts): drawn as the one primary button. */
  primary?: boolean;
}

export interface TaskVm {
  mode: TaskMode;
  /** "Reviewing Alice's chain", "Reworking your chain". */
  heading: string;
  /** The chain's title: its top change's subject. */
  title: string;
  /** "change 2 of 4". */
  position: string;
  /** How much is left, in words. */
  left: string;
  steps: TaskStep[];
  actions: TaskAction[];
  /** The way to the other lists and back: a link beside the heading. */
  views: TaskAction;
  /** Something under way that the screen is waiting on: "Checking out 2156…". */
  busy?: string;
  /** How the last step ended, in ger's words, until the next action (core's step outcome, or a refusal said here). */
  outcome?: Outcome;
  /** What the change shown stands on that the user must know, in words: its base is an abandoned change. */
  notice?: string;
  /**
   * A long chain in a narrow side bar: the steps from `from` to `to` (around the current one) are
   * shown, those before and after fold into one line each that says how many and what they are
   * (`2 replayed`, `3 to come`), opened on a click. Undefined for a chain short enough to list.
   */
  fold?: TaskFold;
}

export interface TaskFold {
  from: number;
  to: number;
  before: string;
  after: string;
}

/** A chain longer than this folds in a narrow side bar (views.css): the current step and one each side stay. */
export const FOLD_OVER = 5;

/**
 * Which steps a narrow side bar shows of a long chain, and what the folded ones are, in words:
 * by where the rebase stands on them while reworking, earlier and later while reviewing.
 */
export function taskFold(steps: readonly TaskStep[], mode: TaskMode): TaskFold | undefined {
  if (steps.length <= FOLD_OVER) {
    return undefined;
  }
  const current = steps.findIndex((step) => step.current);
  const at = current >= 0 ? current : Math.max(0, steps.findIndex((step) => step.phase === "todo"));
  let [from, to] = [Math.max(0, Math.min(at - 1, steps.length - 3)), Math.min(steps.length - 1, Math.max(at + 1, 2))];
  // A fold line for one step saves no line: that step is shown instead.
  [from, to] = [from === 1 ? 0 : from, to === steps.length - 2 ? steps.length - 1 : to];
  const words = (folded: readonly TaskStep[], side: "earlier" | "later") => {
    if (!folded.length) {
      return "";
    }
    if (mode === "review") {
      return `${folded.length} ${side}`;
    }
    const counts = new Map<string, number>();
    folded.forEach((step) => {
      const word = step.phase ? PHASE_MARKS[step.phase].text : side;
      counts.set(word, (counts.get(word) ?? 0) + 1);
    });
    return [...counts].map(([word, count]) => `${count} ${word}`).join(" · ");
  };
  return { from, to, before: words(steps.slice(0, from), "earlier"), after: words(steps.slice(to + 1), "later") };
}

/** A finished step's one line, and the lines after it (what was kept, what is gone, the next step) for the tooltip. */
export interface Outcome {
  text: string;
  ok: boolean;
  more: string[];
}

/** What the header knows beyond the served state. */
export interface TaskOptions {
  /** The other lists are shown (All chains): the header offers to hide them again. */
  all: boolean;
  /** A step of the review is under way: what it says while it runs. */
  busy?: string;
  /** How the last step ended: said until the next action. */
  outcome?: Outcome;
  /**
   * The change the Change view shows, with its comments counted from its chains (`chainCounts`):
   * taken over the served counts for that change, so the header and the view say the same number.
   * `key` is its change number (reviewing) or its commit (reworking).
   */
  shown?: { key: string; counts: Commented };
  /**
   * The review chain at the patchset under review, base first, as core says it (`/review?chain=1`):
   * a change of it the inbox no longer lists (a new patchset dropped its Verified vote, or split the
   * chain) stays in the header, marked so, instead of dropping out without a word.
   */
  chain?: number[];
  /**
   * Every change of that chain, base first, closed ones in their place with Gerrit's status
   * (`/review?chain=1`'s `members`, API 1.10): an abandoned or merged one stays as a row marked in
   * words, is no work left, and a change built on an abandoned one says so.
   */
  members?: ChainMemberStatus[];
  /** The inbox could not be read (Gerrit away): what is left is unknown, never "not in your inbox". */
  inboxUnknown?: boolean;
  /** Subjects of changes as the inbox last listed them: a change it does not list now keeps its name. */
  subjects?: ReadonlyMap<number, string>;
}

/** Which mode the checkout is in: a staged review here, else a rebase on your own stack. */
export function taskMode(stack: Stack | undefined): TaskMode | undefined {
  if (stack?.staged_review) {
    return "review";
  }
  return stack?.rebase_in_progress ? "rework" : undefined;
}

/** What identifies the task, so "All chains" holds for its whole length: the chain reviewed, or the rebase. */
export function taskKey(overview: Overview | undefined, stack: Stack | undefined): string | undefined {
  const mode = taskMode(stack);
  if (mode === "review") {
    return `review:${reviewFocus(overview, stack)?.chain.key ?? stack?.staged_review?.number}`;
  }
  return mode === "rework" ? `rework:${stack?.rebase?.orig_head ?? stack?.branch ?? ""}` : undefined;
}

export const VIEWS = {
  all: { label: "All chains", detail: "keep going; show the inbox and Local stack too", command: "ger.showAllChains" },
  only: { label: "Only this task", detail: "hide the inbox and Local stack again", command: "ger.focusReview" },
};

/** The comment state of a change as a mark: the ring, and the open count in words. */
export function commentMark(item: Parameters<typeof commentState>[0]): TaskMark | undefined {
  const state = commentState(item);
  if (!state || state.tone === "resolved") {
    return undefined;
  }
  const words: Record<string, string> = { open: `${state.toAnswer} to answer`, draft: state.toAnswer ? `${state.toAnswer} to answer · your drafts` : "your drafts", waiting: "waits on a reply" };
  return { icon: "comment", text: words[state.tone] ?? state.label, tone: state.tone === "open" ? "warn" : state.tone === "draft" ? "info" : "plain", ring: { share: state.share, shape: state.shape } };
}

export const failedCi = (item: { attention_reasons: string[]; verified: number | null; ci_failures?: string[] }) =>
  item.attention_reasons.includes("ci-failed") || (item.ci_failures?.length ?? 0) > 0 || (item.verified ?? 0) < 0;

/** A change's comment counts: the Change view's, when it shows that change; else what was served with it. */
export function counted<T extends Commented>(item: T, key: string, options: TaskOptions): Commented {
  return options.shown?.key === key ? options.shown.counts : item;
}

function reworkTask(stack: Stack, options: TaskOptions): TaskVm {
  const rebase = stack.rebase;
  const commits = stack.commits;
  const at = commits.findIndex((commit) => commit.rebase === "current");
  const toAnswer = commits.reduce((sum, commit) => sum + commentTurns(counted(commit, commit.sha ?? "", options)).left, 0);
  // Counted by commit, as the rows show it: the findings are known only once a log is fetched.
  const failed = commits.filter(failedCi).length;
  const edits = commits.filter((commit) => commit.needs_edit).length;
  const above = at >= 0 ? commits.length - at - 1 : commits.filter((commit) => commit.rebase === "todo").length;
  const unmerged = rebase?.unmerged.length ?? 0;
  const uncommitted = rebase?.uncommitted ?? [];
  // What the rebase itself still needs first: the conflict, the fix not amended, then the commits to replay.
  const left = [
    unmerged ? `${plural(unmerged, "conflict")} to resolve` : "",
    uncommitted.length ? `${uncommitted.join(", ")} not amended` : "",
    toAnswer ? `${plural(toAnswer, "comment")} to answer` : "",
    failed ? `CI failed on ${plural(failed, "commit")}` : "",
    edits ? `${plural(edits, "commit")} need${edits === 1 ? "s" : ""} edits` : "",
    above ? `${plural(above, "commit")} to replay` : "",
  ].filter(Boolean);
  const go = (action: "continue" | "abort" | "skip", detail: string): TaskAction => {
    const refused = rebase && !rebase.actions.includes(action) ? (rebase.refused?.[action] ?? "not now") : undefined;
    // While a step runs in the terminal, a second press would run another over it.
    const notNow = options.busy ? "the step before it is still running" : refused ? notNowLine(rebase, action, refused) : undefined;
    return { label: REBASE_ACTIONS[action].label, detail, command: REBASE_ACTIONS[action].command, ...(notNow ? { notNow } : {}), ...(refused && !options.busy ? { tooltip: refused } : {}) };
  };
  // Staged files go into the commit on Continue: its words say so, as the Change view lists them.
  const staged = rebase?.applied ? (rebase.staged ?? []) : [];
  const commitIt = staged.length ? `amend the ${plural(staged.length, "staged file")} into this commit` : "commit this stop as it is";
  const actions = [
    go("continue", unmerged ? `once the ${plural(unmerged, "conflict")} ${unmerged === 1 ? "is" : "are"} resolved` : above ? `${commitIt}, then replay the ${plural(above, "commit")} above` : `${commitIt} and finish the rebase`),
    go("abort", "put the branch back as it was before the rebase"),
    ...(rebase?.actions.includes("skip") ? [go("skip", "leave this commit out and go on")] : []),
  ];
  const title = commits.at(-1)?.summary ?? stack.branch ?? "your stack";
  const steps = commits.map((commit, index) => reworkStep(commit, index, options));
  return {
    mode: "rework",
    heading: `Reworking your chain${stack.branch ? ` on ${stack.branch}` : ""}`,
    title,
    position: at >= 0 ? `change ${at + 1} of ${commits.length}` : `between changes, ${plural(above, "commit")} to come`,
    left: left.join(" · ") || "nothing left to answer or fix",
    ...(options.outcome ? { outcome: options.outcome } : {}),
    // The chain, oldest first as while reviewing: Local stack steps aside, so the Change view has the room.
    steps,
    actions,
    views: options.all ? VIEWS.only : VIEWS.all,
    busy: options.busy,
    ...folded(steps, "rework"),
  };
}

/**
 * Why a way on is not to run now, in one line beside its button: the files in the way, by core's
 * structured verdict (`unmerged`, `uncommitted`); else core's refusal up to its first command. The
 * whole refusal, commands and all, is the tooltip.
 */
export function notNowLine(rebase: Stack["rebase"], action: "continue" | "abort" | "skip", refused: string): string {
  if (action === "continue" && rebase?.unmerged.length) {
    return `resolve ${rebase.unmerged.join(", ")} first`;
  }
  if (action === "continue" && rebase?.uncommitted?.length) {
    return `${rebase.uncommitted.join(", ")} changed, not amended: amend or stash ${rebase.uncommitted.length === 1 ? "it" : "them"} first`;
  }
  return refused.split(/; |: `| \(`/)[0];
}

export const folded = (steps: readonly TaskStep[], mode: TaskMode) => {
  const fold = taskFold(steps, mode);
  return fold ? { fold } : {};
};

const PHASE_MARKS: Record<"done" | "current" | "todo", TaskMark> = {
  done: { icon: "check", text: "replayed", tone: "good" },
  current: { icon: "debug-stackframe", text: "stopped here", tone: "warn" },
  todo: { icon: "circle-large-outline", text: "to come", tone: "dim" },
};

/** A commit of the stack reworked: where the rebase stands on it, what is left on it in words, a click shows it. */
function reworkStep(commit: StackCommit, index: number, options: TaskOptions): TaskStep {
  const phase = commit.rebase ?? undefined;
  const marks = [
    phase ? PHASE_MARKS[phase] : undefined,
    commentMark(counted(commit, commit.sha, options)),
    commit.needs_edit && phase !== "done" ? { icon: "edit", text: "needs edits", tone: "warn" as const } : undefined,
    failedCi(commit) ? { icon: "error", text: "CI failed", tone: "bad" as const } : undefined,
  ].filter((mark): mark is TaskMark => Boolean(mark));
  const name = commit.number ? String(commit.number) : commit.sha.slice(0, 8);
  return {
    key: commit.sha,
    position: index + 1,
    title: commit.summary,
    current: phase === "current",
    ...(phase ? { phase } : {}),
    marks,
    tooltip: `${name}: ${commit.summary}\n${marks.map((m) => m.text).join(" · ") || "nothing to say"}`,
  };
}

/** The header for the mode the checkout is in; undefined outside both. */
export function taskVm(overview: Overview | undefined, stack: Stack | undefined, options: TaskOptions): TaskVm | undefined {
  const mode = taskMode(stack);
  if (!stack || !mode) {
    return undefined;
  }
  return mode === "review" ? reviewTask(overview, stack, options) : reworkTask(stack, options);
}

/** The header as text, for the end-to-end transcript and the gallery's checks. */
export function taskLines(vm: TaskVm | undefined): string[] {
  if (!vm) {
    return [];
  }
  return [
    `${vm.heading}: ${vm.title} — ${vm.position}  [${vm.views.label}]`,
    `Left: ${vm.left}`,
    ...(vm.busy ? [`… ${vm.busy}`] : []),
    ...(vm.outcome ? [`${vm.outcome.ok ? "✓" : "⚠"} ${vm.outcome.text}`, ...vm.outcome.more.map((line) => `  ${line}`)] : []),
    ...(vm.notice ? [`⚠ ${vm.notice}`] : []),
    ...vm.steps.map((step) => `${step.current ? "▸" : " "} ${step.position}. ${step.title} — ${step.marks.map((mark) => mark.text).join(" · ")}`),
    ...vm.actions.map((action) => `${action.primary ? `[**${action.label}**]` : `[${action.label}]`} ${action.notNow ? `(not now: ${action.notNow})` : action.detail}`),
  ];
}
