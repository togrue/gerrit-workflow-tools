// Whether this extension can use the `ger serve` that answered: its API version, and ger's
// package version against the extension's. No `vscode` import: unit-tested.

/** The serve API this extension is written against (`api` in `/health`; bump rules in docu/spec/commands/serve.md). */
export const REQUIRED_API = "1.0.0";

/** `[major, minor, patch]` of a version, or undefined when it does not start with one. A bare `1` (ger before semver API versions) is `1.0.0`. */
function parts(version: string | number | undefined): [number, number, number] | undefined {
  const match = /^(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(String(version ?? ""));
  return match ? [Number(match[1]), Number(match[2] ?? 0), Number(match[3] ?? 0)] : undefined;
}

const majorMinor = (version: [number, number, number]) => `${version[0]}.${version[1]}`;

/** `ger serve` speaks an API this extension cannot use. `gerOlder`: updating ger fixes it; otherwise the extension is behind. */
export class IncompatibleServe extends Error {
  constructor(
    readonly served: string,
    readonly required: string,
    readonly gerOlder: boolean,
  ) {
    const fix = gerOlder ? "update ger" : "update the extension, or install the ger release that matches it";
    super(`ger serve speaks API ${served}, the extension needs ${required}: ${fix}.`);
    this.name = "IncompatibleServe";
  }
}

/**
 * Why the served API `served` will not do for an extension that needs `required`; undefined when it will.
 * Refused: another major, or the same major with a lower minor. The patch never matters.
 */
export function apiProblem(served: string | number | undefined, required: string = REQUIRED_API): IncompatibleServe | undefined {
  const want = parts(required);
  if (!want) {
    throw new Error(`not an API version: ${required}`);
  }
  const have = parts(served);
  const shown = have ? majorMinor(have) : String(served ?? "none");
  if (!have || have[0] < want[0] || (have[0] === want[0] && have[1] < want[1])) {
    return new IncompatibleServe(shown, majorMinor(want), true);
  }
  return have[0] > want[0] ? new IncompatibleServe(shown, majorMinor(want), false) : undefined;
}

/** How ger's package version stands to the extension's. `unknown`: either is a dev or local build, or not a release number. */
export type Skew = "same" | "ger-older" | "ger-newer" | "unknown";

const RELEASE = /^\d+\.\d+\.\d+$/;

export function packageSkew(ger: string, extension: string): Skew {
  if (!RELEASE.test(ger) || !RELEASE.test(extension)) {
    return "unknown";
  }
  const [a, b] = [parts(ger)!, parts(extension)!];
  const order = a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  return order === 0 ? "same" : order < 0 ? "ger-older" : "ger-newer";
}
