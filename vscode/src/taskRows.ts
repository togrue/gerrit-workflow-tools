// The task header: while you review a chain (a staged review here) or rework your own (a rebase
// underway), a compact block at the top of the side bar says the mode in words, where you are in
// the chain and how much is left, and names the ways out in words. Reviewing, it lists the chain's
// changes in reading order (oldest first), each one's state by an icon and a word; reworking,
// Local stack right under it is that list. Pure: no `vscode`, no I/O; task.ts holds the state,
// common.js draws it in the Change view (review) and Local stack (rework).

import { Member, Overview, Stack } from "./api";
import { commentState, commentTurns } from "./commentState";
import { plural } from "./format";
import { reviewFocus } from "./reviewFocus";
import { REBASE_ACTIONS, Tone } from "./stackRows";

export type TaskMode = "review" | "rework";

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
  marks: TaskMark[];
  tooltip: string;
}

export interface TaskAction {
  label: string;
  /** What it does, in words, beside the button: no need to hover. */
  detail: string;
  command: string;
  /** Why it would not run now (core's words): drawn disabled, with the reason as its detail. */
  notNow?: string;
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
}

/** What the header knows beyond the served state. */
export interface TaskOptions {
  /** The other lists are shown (All chains): the header offers to hide them again. */
  all: boolean;
  /** A step of the review is under way: what it says while it runs. */
  busy?: string;
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

const VIEWS = {
  all: { label: "All chains", detail: "keep going; show the inbox and Local stack too", command: "ger.showAllChains" },
  only: { label: "Only this task", detail: "hide the inbox and Local stack again", command: "ger.focusReview" },
};

/** The comment state of a change as a mark: the ring, and the open count in words. */
function commentMark(item: Parameters<typeof commentState>[0]): TaskMark | undefined {
  const state = commentState(item);
  if (!state || state.tone === "resolved") {
    return undefined;
  }
  const words: Record<string, string> = { open: `${state.toAnswer} to answer`, draft: "your drafts", waiting: "waits on a reply" };
  return { icon: "comment", text: words[state.tone] ?? state.label, tone: state.tone === "open" ? "warn" : state.tone === "draft" ? "info" : "plain", ring: { share: state.share, shape: state.shape } };
}

const failedCi = (item: { attention_reasons: string[]; verified: number | null; ci_failures?: string[] }) =>
  item.attention_reasons.includes("ci-failed") || (item.ci_failures?.length ?? 0) > 0 || (item.verified ?? 0) < 0;

function voteMark(member: Member): TaskMark | undefined {
  const review = member.your_votes?.["Code-Review"];
  if (review) {
    return { icon: review > 0 ? "thumbsup" : "thumbsdown", text: `you voted ${review > 0 ? "+" : ""}${review}`, tone: review > 0 ? "good" : "bad" };
  }
  if (member.not_ready) {
    return { icon: "circle-slash", text: `not ready: ${member.not_ready}`, tone: "dim" };
  }
  return member.your_turn ? { icon: "circle-large-outline", text: "to review" } : undefined;
}

function reviewTask(overview: Overview | undefined, stack: Stack, options: TaskOptions): TaskVm {
  const review = stack.staged_review!;
  const focus = reviewFocus(overview, stack);
  const members = focus?.chain.members ?? [];
  const at = members.findIndex((member) => member.number === review.number);
  const steps: TaskStep[] = members.map((member, index) => {
    const current = index === at;
    const marks = [
      current ? { icon: "eye", text: "reviewing", tone: "info" as const } : undefined,
      voteMark(member),
      commentMark(member),
      failedCi(member) ? { icon: "error", text: "CI failed", tone: "bad" as const } : undefined,
    ].filter((mark): mark is TaskMark => Boolean(mark));
    return { key: String(member.number), position: index + 1, title: member.subject, current, marks, tooltip: `${member.number}: ${member.subject}\n${marks.map((m) => m.text).join(" · ") || "nothing to say"}` };
  });
  if (!steps.length) {
    // Not in your inbox (any more): the change alone.
    steps.push({ key: String(review.number), position: 1, title: review.subject, current: true, marks: [{ icon: "eye", text: "reviewing", tone: "info" }], tooltip: `${review.number}: ${review.subject}` });
  }
  const voted = members.filter((member) => member.your_votes?.["Code-Review"]).length;
  const toVote = members.filter((member) => !member.your_votes?.["Code-Review"] && !member.not_ready).length;
  const notReady = members.filter((member) => !member.your_votes?.["Code-Review"] && member.not_ready).length;
  const toAnswer = members.reduce((sum, member) => sum + commentTurns(member).left, 0);
  const left = [
    members.length ? (toVote ? `${toVote} of ${members.length} to vote on` : `you voted on ${voted} of ${members.length}`) : "its chain is not in your inbox",
    notReady ? `${notReady} not ready for review` : "",
    toAnswer ? `${plural(toAnswer, "comment")} to read` : "",
  ].filter(Boolean);
  const owner = focus?.chain.owner.name;
  const back = review.return_to;
  return {
    mode: "review",
    heading: owner ? `Reviewing ${owner}'s chain` : `Reviewing ${review.number}`,
    title: focus?.chain.top.subject ?? review.subject,
    position: `change ${at >= 0 ? at + 1 : 1} of ${Math.max(members.length, 1)}`,
    left: left.join(" · "),
    steps,
    views: options.all ? VIEWS.only : VIEWS.all,
    actions: [
      { label: "Leave review", detail: back ? `back to ${back}; the review's branches go` : "end the review; its branches go", command: "ger.reviewDone" },
    ],
    busy: options.busy,
  };
}

function reworkTask(stack: Stack, options: TaskOptions): TaskVm {
  const rebase = stack.rebase;
  const commits = stack.commits;
  const at = commits.findIndex((commit) => commit.rebase === "current");
  const toAnswer = commits.reduce((sum, commit) => sum + commentTurns(commit).left, 0);
  const failed = commits.filter(failedCi).length;
  const edits = commits.filter((commit) => commit.needs_edit).length;
  const left = [toAnswer ? `${plural(toAnswer, "comment")} to answer` : "", failed ? `${plural(failed, "CI failure")}` : "", edits ? `${plural(edits, "commit")} need${edits === 1 ? "s" : ""} edits` : ""].filter(Boolean);
  const above = at >= 0 ? commits.length - at - 1 : commits.filter((commit) => commit.rebase === "todo").length;
  const unmerged = rebase?.unmerged.length ?? 0;
  const go = (action: "continue" | "abort" | "skip", detail: string): TaskAction => ({
    label: REBASE_ACTIONS[action].label,
    detail,
    command: REBASE_ACTIONS[action].command,
    notNow: rebase && !rebase.actions.includes(action) ? (rebase.refused?.[action] ?? "not now") : undefined,
  });
  const actions = [
    go("continue", unmerged ? `once the ${plural(unmerged, "conflict")} ${unmerged === 1 ? "is" : "are"} resolved` : above ? `commit this stop as it is, then replay the ${plural(above, "commit")} above` : "commit this stop as it is and finish the rebase"),
    go("abort", "put the branch back as it was before the rebase"),
    ...(rebase?.actions.includes("skip") ? [go("skip", "leave this commit out and go on")] : []),
  ];
  const title = commits.at(-1)?.summary ?? stack.branch ?? "your stack";
  return {
    mode: "rework",
    heading: `Reworking your chain${stack.branch ? ` on ${stack.branch}` : ""}`,
    title,
    position: at >= 0 ? `change ${at + 1} of ${commits.length}` : `between changes, ${plural(above, "commit")} to come`,
    left: left.join(" · ") || "nothing left to answer or fix",
    // Local stack, right under the header, is the chain: its rows mark what is replayed and what is to come.
    steps: [],
    actions,
    views: options.all ? VIEWS.only : VIEWS.all,
    busy: options.busy,
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
    ...vm.steps.map((step) => `${step.current ? "▸" : " "} ${step.position}. ${step.title} — ${step.marks.map((mark) => mark.text).join(" · ")}`),
    ...vm.actions.map((action) => `[${action.label}] ${action.notNow ? `(not now: ${action.notNow})` : action.detail}`),
  ];
}
