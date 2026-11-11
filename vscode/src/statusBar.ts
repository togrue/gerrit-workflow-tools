// The status bar item: the headline, and a bell with a count when chains had activity.

import * as vscode from "vscode";

import { EventFollower } from "./events";
import { plural, refreshMode } from "./format";
import { Model } from "./model";
import { pollSeconds } from "./poller";

/** `note` says which repository it is about, when the window has several folders. */
export function statusBar(model: Model, events: EventFollower, note: string | undefined): vscode.StatusBarItem {
  const item = vscode.window.createStatusBarItem("ger.headline", vscode.StatusBarAlignment.Left, 50);
  // How it is listed where status bar items are hidden or shown.
  item.name = "Gerrit Headline";
  item.command = "ger.openHeadline";
  const render = () => {
    const overview = model.overview;
    const news = model.active.size ? `  $(bell-dot) ${model.active.size}` : "";
    const tooltip = new vscode.MarkdownString(undefined, true);
    // What a screen reader says: the words of the item, without its icons.
    let label: string;
    if (!overview) {
      const untrusted = model.state === "untrusted";
      const why = untrusted ? "ger starts once you trust this folder." : (model.failure ?? "Starting ger serve…");
      item.text = untrusted ? "$(shield) Gerrit" : model.failure ? "$(warning) Gerrit" : "$(sync~spin) Gerrit";
      tooltip.appendText(why);
      label = `Gerrit: ${why}`;
      item.backgroundColor = undefined;
    } else {
      const headline = overview.headline;
      const text = headline ? headline.text : overview.error ? overview.error.message : "nothing needs you";
      const glyph = !headline ? "$(pass)" : headline.reason === "ci-failed" ? "$(error)" : headline.section === "to-review" ? "$(eye)" : "$(comment-unresolved)";
      // Out of date: the last refresh failed, and this is the answer before it.
      const old = model.stale ? " $(warning)" : "";
      item.text = `${glyph} ${text.length > 60 ? `${text.slice(0, 59)}…` : text}${news}${old}`;
      label = `Gerrit: ${text}${model.active.size ? `. New activity on ${plural(model.active.size, "chain")}` : ""}${model.stale ? ". Out of date: the last refresh failed" : ""}`;
      if (model.stale) {
        tooltip.appendMarkdown("$(warning) ").appendText(`Out of date: the last refresh failed (${model.stale}). This is the last answer; the next refresh that works replaces it.`).appendMarkdown("\n\n");
      }
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
      // How the views stay current: unobtrusive, only here and in the log.
      tooltip.appendMarkdown("\n\n").appendText(refreshMode(events.events, pollSeconds(events.live)));
    }
    if (note) {
      tooltip.appendMarkdown("\n\n").appendText(note);
    }
    item.tooltip = tooltip;
    item.accessibilityInformation = { label, role: "button" };
    item.show();
  };
  model.onDidChange(render);
  events.onDidChange(render);
  render();
  return item;
}
