// The CI findings of the change the Change view shows, as the editor's own problems: a marker at
// the line, a row in Problems, the whole message on hover. What each finding becomes is decided
// here, from what core parsed out of the build log (`ci_content`): its path, line and column, its
// message whole, the build it came from. Nothing here knows a language or a tool: a compiler's
// error, a test's assertion and a linter's warning are all a path, a line and words. Pure: no
// `vscode`; ciProblems.ts hands the result to VS Code.

import { CiContent, CiFinding } from "./api";

/** What the Problems view and the hover name as the finding's source. */
export const CI_SOURCE = "ger CI";

export interface FindingDiagnostic {
  /** The file, relative to the repository's top, as core names it. */
  path: string;
  /** 0-based, as VS Code counts: the finding's line, else the file's first. */
  line: number;
  /** 0-based; 0 when the log named no column. */
  column: number;
  severity: "error" | "warning";
  /** The finding's whole message: the tool's words, then its detail and where it came from. */
  message: string;
  /** The build it came from (`ger-playground-cpp #7154`), and its log: the diagnostic's code, a link. */
  build: string;
  url: string;
  /** Its fingerprint: the same finding is one diagnostic, however often a log repeats it. */
  fingerprint: string;
}

/**
 * Every finding of `contents` that has a place in a file, as a diagnostic. `introduced`: the
 * fingerprints this change introduced (core's `ci_introduced`), null when that cannot be told; a
 * finding that also fails on the parent says so. A finding without a path (a build step failed,
 * `Failed to execute goal …`) has nowhere to be drawn: it stays a row of the CI section only.
 */
export function findingDiagnostics(contents: readonly CiContent[], introduced: ReadonlySet<string> | null): FindingDiagnostic[] {
  const seen = new Set<string>();
  const out: FindingDiagnostic[] = [];
  for (const content of contents) {
    const build = content.provenance.build ?? content.label;
    for (const finding of content.findings) {
      const key = `${finding.fingerprint}\0${finding.path}\0${finding.line}`;
      if (!finding.path || seen.has(key)) {
        continue;
      }
      seen.add(key);
      out.push({
        path: finding.path,
        line: Math.max(0, (finding.line ?? 1) - 1),
        column: Math.max(0, (finding.column ?? 1) - 1),
        severity: finding.severity === "warning" ? "warning" : "error",
        message: messageOf(finding, introduced === null ? null : introduced.has(finding.fingerprint), content.patch_set ?? null),
        build,
        url: content.provenance.url || content.url,
        fingerprint: finding.fingerprint,
      });
    }
  }
  return out;
}

/** The words a hover and Problems show: the message whole (every line of it), the detail, then where it came from. */
function messageOf(finding: CiFinding, introduced: boolean | null, patchSet: number | null): string {
  const moved =
    patchSet && finding.patchset_line
      ? `CI saw it at line ${finding.patchset_line} of patchset ${patchSet}${finding.line_changed ? ", a line changed since" : ""}.`
      : "";
  const origin = introduced === false ? "It also fails on the parent: this change did not bring it in." : "";
  return [wholeMessage(finding), [moved, origin].filter(Boolean).join(" ")].filter(Boolean).join("\n");
}

/** A finding's message and its detail, every line; a detail the message says already (pytest's last line) once. */
export function wholeMessage(finding: Pick<CiFinding, "message" | "detail">): string {
  const detail = finding.detail?.trim() ?? "";
  return detail && !finding.message.includes(detail) ? `${finding.message}\n${detail}` : finding.message;
}
