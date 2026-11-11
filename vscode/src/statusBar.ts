// The status bar item: the headline, and a bell with a count when chains had activity.

import * as vscode from "vscode";

import { plural } from "./format";
import { Model } from "./model";

/** `note` says which repository it is about, when the window has several folders. */
export function statusBar(model: Model, note: string | undefined): vscode.StatusBarItem {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  item.command = "ger.openHeadline";
  const render = () => {
    const overview = model.overview;
    const news = model.active.size ? `  $(bell-dot) ${model.active.size}` : "";
    const tooltip = new vscode.MarkdownString(undefined, true);
    if (!overview) {
      item.text = model.failure ? "$(warning) ger" : "$(sync~spin) ger";
      tooltip.appendText(model.failure ?? "Starting ger serve…");
      item.backgroundColor = undefined;
    } else {
      const headline = overview.headline;
      const text = headline ? headline.text : overview.error ? overview.error.message : "nothing needs you";
      const glyph = !headline ? "$(pass)" : headline.reason === "ci-failed" ? "$(error)" : headline.section === "to-review" ? "$(eye)" : "$(comment-unresolved)";
      item.text = `${glyph} ${text.length > 60 ? `${text.slice(0, 59)}…` : text}${news}`;
      item.backgroundColor = headline?.reason === "ci-failed" ? new vscode.ThemeColor("statusBarItem.errorBackground") : undefined;
      for (const section of overview.sections ?? []) {
        const title = section.name === "outgoing" ? "Outgoing" : "To review";
        tooltip.appendMarkdown(`**${title}** — ${plural(section.chains.length, "chain")}`);
        if (section.summary.ci_failures) {
          tooltip.appendMarkdown(`, ${section.summary.ci_failures} with failed CI`);
        }
        if (section.summary.comments) {
          tooltip.appendMarkdown(`, ${section.summary.comments} with unresolved comments`);
        }
        tooltip.appendMarkdown("\n\n");
      }
      const stack = model.stack;
      if (stack) {
        tooltip.appendMarkdown(`**Local stack** — ${plural(stack.summary.commits, "commit")}, ${stack.summary.not_on_gerrit} not on Gerrit\n\n`);
      }
      if (model.active.size) {
        tooltip.appendMarkdown(`$(bell-dot) New activity on ${plural(model.active.size, "chain")}\n\n`);
      }
      tooltip.appendMarkdown(headline ? "Click to open it." : "Click to open the overview.");
    }
    if (note) {
      tooltip.appendMarkdown("\n\n").appendText(note);
    }
    item.tooltip = tooltip;
    item.show();
  };
  model.onDidChange(render);
  render();
  return item;
}
