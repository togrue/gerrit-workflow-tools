// An error state of a webview view, as the view draws it: a sentence in ger's own words saying
// what state the view is in, the raw words of git, Gerrit or ger serve it rests on put under it as
// they came, and the options as buttons (Q8 of docu/plans/extension-vision.md: when a limit is hit,
// the user is told the state and the options). Every error state of the Local stack and Change
// views is built here, so none is left with a message and nothing to do. Pure: no `vscode`, no I/O.

import type { StackAction } from "./stackRows";

export interface Problem {
  /** What state the view is in, in ger's words. */
  text: string;
  /** The words of git, Gerrit or ger serve it rests on, as they came; empty when there are none. */
  raw: string;
  /** The ways on, as buttons; the first is the primary one when it is the clear next step. */
  actions: StackAction[];
}

/** The commands an error state offers, and their words. */
export const PROBLEM_ACTIONS = {
  showAgain: { label: "Show Again", title: "Ask ger serve for this change again", command: { id: "ger.change.reload", args: [] } },
  showStack: { label: "Show Local Stack", title: "Pick the commit from Local stack as it is now: a rebase may have rewritten it", command: { id: "ger.stack.focus", args: [] } },
  showChains: { label: "Show Review Chains", title: "Pick the change again from Review chains", command: { id: "ger.overview.focus", args: [] } },
  reload: { label: "Reload", title: "Read the local stack and Gerrit's state again (Refresh from Gerrit)", command: { id: "ger.refresh", args: [] } },
  upstream: { label: "Choose an Upstream Branch…", title: "Pick the branch your stack is built on: the stack is the commits above it", command: { id: "ger.setUpstream", args: [] } },
  log: { label: "Show Log", title: "What ger serve said, in the extension's log", command: { id: "ger.showLog", args: [] } },
} satisfies Record<string, StackAction>;

/** The sentences, in ger's words. */
export const PROBLEM_WORDS = {
  change: "This change could not be shown: nothing of it is drawn here.",
  stack: "Your local stack could not be read, so it is not shown.",
};

/**
 * The Change view when the change selected could not be read. Show Again asks once more (Gerrit
 * may have been away); a commit of your stack may be gone (rewritten by a rebase), so Local stack
 * shows it as it is now; somebody's change is picked again from Review chains.
 */
export function changeProblem(raw: string, local: boolean): Problem {
  const [again, pick, log] = [PROBLEM_ACTIONS.showAgain, local ? PROBLEM_ACTIONS.showStack : PROBLEM_ACTIONS.showChains, PROBLEM_ACTIONS.log];
  return { text: PROBLEM_WORDS.change, raw, actions: [{ ...again, primary: true }, pick, log] };
}

/**
 * Local stack when git could not read it (an upstream that is gone, a bad revision): read it
 * again, give the branch another upstream, or read the log. Which one helps is not known here:
 * core does not say what kind of failure it was, so no button is drawn as the one to press.
 */
export function stackProblem(raw: string): Problem {
  return { text: PROBLEM_WORDS.stack, raw, actions: [PROBLEM_ACTIONS.reload, PROBLEM_ACTIONS.upstream, PROBLEM_ACTIONS.log] };
}

/** A problem as text: what the end-to-end transcript shows of it. */
export function problemLines(problem: Problem): string[] {
  const buttons = problem.actions.map((action) => (action.primary ? `[**${action.label}**]` : `[${action.label}]`)).join(" ");
  return [problem.text, ...problem.raw.split("\n").filter(Boolean).map((line) => `  ${line}`), ...(buttons ? [`  ${buttons}`] : [])];
}
