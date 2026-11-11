// What the Gerrit views can show, as the `ger.state` context key their welcome texts follow
// (package.json, viewsWelcome). No `vscode` import: unit-tested.

import { GerApiError } from "./api";
import { ExecutableNotFound, ServeFailed } from "./server";

export type GerState =
  /** `ger serve` is starting, or answering its first request. */
  | "starting"
  | "ready"
  /** Restricted Mode: ger runs git and the program the settings name, so nothing starts until you trust the folder. */
  | "untrusted"
  /** No workspace folder is in a git checkout. */
  | "no-repo"
  /** The program `ger.executable` names is not there. */
  | "no-executable"
  /** The checkout lacks Gerrit settings (`gerrit.webUrl`, credentials): `ger setup` fixes it. */
  | "setup"
  /** Anything else: `ger serve` crashed, or answered with a bug. Retry, or read the log. */
  | "failed";

/** Which state a failure to load leaves. Gerrit and git conditions come classified from the server, by code. */
export function failureState(error: unknown): GerState {
  if (error instanceof ExecutableNotFound) {
    return "no-executable";
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

/** States only you can end (install ger, run `ger setup`, trust the folder, open a repository): polling them again would only restart `ger serve` to fail again. */
export function waitsForYou(state: GerState): boolean {
  return state === "no-executable" || state === "setup" || state === "untrusted" || state === "no-repo";
}
