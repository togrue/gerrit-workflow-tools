// A report of an issue: what the views show at one moment, anonymized (src/anonymize.ts), with the
// versions it was taken on. It is a snapshot file (src/snapshot.ts) that says more, so it replays in
// the gallery (`npm run gallery -- report.json`) and can become a test fixture. Not in it: the log,
// settings, folder names, the user's name, and anything else that says where it was taken.
// Pure: no `vscode` import, unit-tested.

import { anonymize } from "./anonymize";
import { EventsStatus } from "./api";
import { ChangeVm } from "./changeRows";
import { Random } from "./fakes";
import { SNAPSHOT_FORMAT, Snapshot } from "./snapshot";
import { StackVm } from "./stackRows";
import { GerState } from "./state";

/** A row of Review chains as data: what the tree shows of it, and the rows under it. */
export interface TreeRow {
  label: string;
  description: string;
  /** The codicon in front, when it has one. */
  icon: string | null;
  /** What its context menu goes by (src/rows.ts). */
  contextValue: string | null;
  children: TreeRow[];
}

/** Where a report was taken; none of it comes from the repository. */
export interface Versions {
  /** The extension's version; the ger release of the same version came with it. */
  extension: string;
  /** VS Code's version. */
  editor: string;
  /** `process.platform` and `os.release()`, e.g. `win32 10.0.26200`. */
  os: string;
}

export interface ReportInput extends Versions {
  /** The `ger.state` the views' welcome texts follow. */
  state: GerState;
  /** Where Gerrit's event stream is, which decides between events and the poll; null when the service has not said. */
  events: EventsStatus["state"] | null;
  /** The last refresh failed: the views show the answer before it. */
  outOfDate: boolean;
  /** What the views show, as they are: null for a view that shows nothing because the extension did not start. */
  stack: StackVm | null;
  change: ChangeVm | null;
  chains: TreeRow[] | null;
}

/** What a report says of itself, first, to whoever reads it before sharing it. */
export const ABOUT =
  "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.";

export interface Report extends Snapshot {
  about: string;
  extension: string;
  vscode: string;
  os: string;
  state: GerState;
  events: EventsStatus["state"] | null;
  outOfDate: boolean;
  /** The rows of Review chains. */
  chains: TreeRow[] | null;
}

/** The report of `input`, as the JSON text that is shown, copied and saved. Each call replaces names and ids by other ones. */
export function reportText(input: ReportInput, random?: Random): string {
  const views = anonymize({ stack: input.stack, change: input.change, chains: input.chains }, random);
  const report: Report = {
    about: ABOUT,
    format: SNAPSHOT_FORMAT,
    name: "Report",
    extension: input.extension,
    vscode: input.editor,
    os: input.os,
    state: input.state,
    events: input.events,
    outOfDate: input.outOfDate,
    chains: views.chains as TreeRow[] | null,
    stack: views.stack as StackVm | null,
    change: views.change as ChangeVm | null,
  };
  return JSON.stringify(report, null, 2);
}

/**
 * A new-issue page on `repository` (the URL in package.json). A prefilled body is sent to GitHub
 * as the page opens, before the user decides anything: so it carries the label and the versions,
 * never the report, and says to paste that from the clipboard.
 */
export function issueUrl(repository: string, versions: Versions): string {
  const body = [
    "<!-- What happened, and what did you expect? -->",
    "",
    "",
    "<details><summary>Report (names, paths, text and ids replaced)</summary>",
    "",
    "```json",
    "PASTE THE REPORT HERE: it is on your clipboard",
    "```",
    "",
    "</details>",
    "",
    "---",
    `ger extension ${versions.extension} · VS Code ${versions.editor} · ${versions.os}`,
  ].join("\n");
  return `${repository.replace(/(\.git)?\/*$/, "")}/issues/new?labels=vscode&body=${encodeURIComponent(body)}`;
}
