// What the Gerrit views can show, as the `ger.state` context key their welcome texts follow
// (package.json, viewsWelcome). No `vscode` import: unit-tested.

import { GerApiError } from "./api";
import { ExecutableNotFound, ServeFailed } from "./server";

export type GerState =
  /** `ger serve` is starting, or answering its first request. */
  | "starting"
  | "ready"
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
