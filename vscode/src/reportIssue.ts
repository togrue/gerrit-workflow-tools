// ger: Report Issue…: the report (src/report.ts) is shown before anything leaves the machine, as an
// untitled document to read and edit; then it is copied for a new GitHub issue, saved, or dropped.

import * as os from "node:os";
import * as vscode from "vscode";

import { ChangeView } from "./changeView";
import { EventFollower } from "./events";
import { Model } from "./model";
import { OverviewView } from "./overviewView";
import { TreeRow, Versions, issueUrl, reportText } from "./report";
import { StackView } from "./stackView";
import { GerState } from "./state";
import { feedback, ui } from "./ui";

/** The running extension a report is taken from. */
export interface Reported {
  model: Model;
  events: EventFollower;
  overviewView: OverviewView;
  stackView: StackView;
  changeView: ChangeView;
}

/** The parts of package.json a report needs. */
interface Manifest {
  version: string;
  repository: { url: string };
}

const COPY = "Copy and Open GitHub Issue";
const SAVE = "Save to File…";

/** A tree's rows as data, the collapsed ones too. */
async function treeRows<T>(provider: vscode.TreeDataProvider<T>, node?: T): Promise<TreeRow[]> {
  const rows: TreeRow[] = [];
  for (const child of (await provider.getChildren(node)) ?? []) {
    const item = await provider.getTreeItem(child);
    rows.push({
      label: typeof item.label === "string" ? item.label : (item.label?.label ?? ""),
      description: typeof item.description === "string" ? item.description : "",
      icon: item.iconPath instanceof vscode.ThemeIcon ? item.iconPath.id : null,
      contextValue: item.contextValue ?? null,
      children: await treeRows(provider, child),
    });
  }
  return rows;
}

/**
 * Take a report and ask what to do with it. `running` is undefined (and `state` says why) when the
 * extension did not start: the report then has the versions and the state, and no views.
 */
export async function reportIssue(manifest: Manifest, running: Reported | undefined, state: GerState): Promise<void> {
  const versions: Versions = { extension: manifest.version, editor: vscode.version, os: `${process.platform} ${os.release()}` };
  const text = reportText({
    ...versions,
    state,
    events: running?.events.events?.state ?? null,
    outOfDate: Boolean(running?.model.stale),
    stack: running ? running.stackView.vm() : null,
    change: running ? running.changeView.vm() : null,
    chains: running ? await treeRows(running.overviewView) : null,
  });
  // What is copied or saved is the document as it stands then: what the user read, and took out of it.
  const shown = await ui().document(text, "json");
  const what = "This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.";
  const choice = await ui().message("info", what, {}, [COPY, SAVE]);
  if (choice !== COPY && choice !== SAVE) {
    return;
  }
  const report = shown();
  if (report === undefined) {
    // Never the original instead: the user may have taken things out of it.
    await ui().message("info", "The report was closed without saving it, so nothing was shared. Run ger: Report Issue… again for a new one.", {}, []);
  } else if (choice === COPY) {
    await ui().copy(report);
    await ui().openExternal(vscode.Uri.parse(issueUrl(manifest.repository.url, versions), true));
    feedback("Report copied: paste it into the issue that opens in the browser", { milliseconds: 8000 });
  } else {
    const file = await ui().save("ger-report.json", report);
    if (file) {
      feedback(`Report saved to ${file}`);
    }
  }
}
