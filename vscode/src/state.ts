// What the Gerrit views can show, as the `ger.state` context key their welcome texts follow
// (package.json, viewsWelcome). No `vscode` import: unit-tested.

import { GerApiError } from "./api";
import { IncompatibleServe } from "./compat";
import { ExecutableNotFound, ServeFailed } from "./server";

export type GerState =
  /** `ger serve` is starting. */
  | "starting"
  /** `ger serve` runs, and its first answer, which asks Gerrit, has not come yet: Local stack shows what git knows meanwhile. */
  | "asking"
  | "ready"
  /** Restricted Mode: ger runs git and the program the settings name, so nothing starts until you trust the folder. */
  | "untrusted"
  /** No workspace folder is in a git checkout. */
  | "no-repo"
  /** The program `ger.executable` names is not there. */
  | "no-executable"
  /** The checkout lacks Gerrit settings (`ger.webUrl`, credentials): `ger setup` fixes it. */
  | "setup"
  /** `ger serve` speaks an API version this extension cannot use: ger or the extension needs updating. */
  | "incompatible"
  /** Anything else: `ger serve` crashed, or answered with a bug. Retry, or read the log. */
  | "failed";

/** Which state a failure to load leaves. Gerrit and git conditions come classified from the server, by code. */
export function failureState(error: unknown): GerState {
  if (error instanceof ExecutableNotFound) {
    return "no-executable";
  }
  if (error instanceof IncompatibleServe) {
    return "incompatible";
  }
  const code = error instanceof GerApiError || error instanceof ServeFailed ? error.code : undefined;
  if (code === "CONFIG") {
    return "setup";
  }
  // Before serving, GIT is the only reason it gives: this is not a git checkout.
  return error instanceof ServeFailed && code === "GIT" ? "no-repo" : "failed";
}

/** Why a refresh that failed left the last overview in place: what the served error code says, else its message. */
export function staleReason(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return error instanceof GerApiError && error.code === "GERRIT" ? `Gerrit unreachable: ${message}` : message;
}

/** What the views say while there is no answer yet: ger serve starting, then Gerrit asked (with how long it is given). */
export const WAITING_WORDS = {
  starting: "Starting ger serve…",
  // One wording in every view (F24: "within seconds" in Review chains' welcome text, which cannot hold a number, "within 10 seconds" here).
  asking: (seconds?: number) => `Asking Gerrit…${seconds ? " If it does not answer within seconds, the views say so." : ""}`,
  /** How long, said where there is room for it: the status bar's tooltip. */
  given: (seconds: number) => `Gerrit is given ${seconds} seconds to answer.`,
};

/** What a webview view shows in place of its content while ger is not running. */
export interface Waiting {
  text: string;
  /** A button under the text. */
  link?: { label: string; command: string };
}

/**
 * Why `what` (a view's content, as a noun phrase) is not shown in `state`; undefined when it is.
 * `incompatible`: the refusal, when that is the state; the view then says it and offers the fix.
 * `failure`: ger's own reason, said when setup is wanting (Review chains' welcome text cannot carry it).
 */
export function waitingFor(state: GerState, what: string, incompatible?: IncompatibleServe, failure?: string, gerritWait?: number): Waiting | undefined {
  if (state === "ready") {
    return undefined;
  }
  if (state === "starting") {
    return { text: WAITING_WORDS.starting };
  }
  if (state === "asking") {
    return { text: WAITING_WORDS.asking(gerritWait) };
  }
  if (state === "incompatible" && incompatible) {
    return { text: incompatible.message, link: { label: incompatible.gerOlder ? "Update ger" : "Install Matching ger", command: "ger.installCli" } };
  }
  if (state === "setup" && failure) {
    return { text: `${what} shows here once ger can ask Gerrit. ger says: ${failure}`, link: { label: "Run ger setup", command: "ger.setup" } };
  }
  return {
    text: `${what} shows here once ger runs. Review chains says what is missing and what to do next.`,
    link: { label: "Show Review Chains", command: "ger.overview.focus" },
  };
}

/** States only you can end (install or update ger, run `ger setup`, trust the folder, open a repository): polling them again would only restart `ger serve` to fail again. */
export function waitsForYou(state: GerState): boolean {
  return state === "no-executable" || state === "setup" || state === "untrusted" || state === "no-repo" || state === "incompatible";
}
