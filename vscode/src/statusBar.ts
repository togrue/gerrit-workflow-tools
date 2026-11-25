// The status bar item: the headline, and a bell with a count when chains had activity. While a
// rebase is underway core makes it the headline; the item says where it stopped and what is next.

import * as vscode from "vscode";

import { EventFollower } from "./events";
import { headlineIcon, markdownText, plural, rebaseItem, refreshMode } from "./format";
import { Model } from "./model";
import { pollSeconds } from "./poller";
import { waitsOnYou } from "./rows";

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
      tooltip.appendMarkdown(markdownText(why));
      label = `Gerrit: ${why}`;
      item.backgroundColor = undefined;
    } else {
      const headline = overview.headline;
      const rebase = headline?.section === "local" ? rebaseItem(headline, model.stack?.rebase) : undefined;
      const text = rebase ? rebase.text : headline ? headline.text : overview.error ? overview.error.message : "nothing needs you";
      const glyph = `$(${headlineIcon(headline)})`;
      // Out of date: the last refresh failed, and this is the answer before it.
      const old = model.stale ? " $(warning)" : "";
      // Failed CI is the red item: kept short, the change's subject is in the tooltip.
      const shown = headline?.reason === "ci-failed" ? `CI failed: ${headline.number}` : text.length > 60 ? `${text.slice(0, 59)}…` : text;
      item.text = `${glyph} ${shown}${news}${old}`;
      label = `Gerrit: ${text}${model.active.size ? `. New activity on ${plural(model.active.size, "chain")}` : ""}${model.stale ? ". Out of date: the last refresh failed" : ""}`;
      if (model.stale) {
        tooltip.appendMarkdown("$(warning) ").appendMarkdown(markdownText(`Out of date: the last refresh failed (${model.stale}). This is the last answer; the next refresh that works replaces it.`)).appendMarkdown("\n\n");
      }
      if (headline) {
        tooltip.appendMarkdown(markdownText(headline.text)).appendMarkdown("\n\n");
      }
      if (rebase) {
        tooltip.appendMarkdown(markdownText(rebase.next)).appendMarkdown("\n\n");
      }
      // How urgent, core says: failed CI and a conflict are errors, another rebase stop a warning.
      item.backgroundColor =
        headline?.severity === "error"
          ? new vscode.ThemeColor("statusBarItem.errorBackground")
          : rebase
            ? new vscode.ThemeColor("statusBarItem.warningBackground")
            : undefined;
      for (const section of overview.sections ?? []) {
        const title = section.name === "outgoing" ? "Outgoing" : "To review";
        // To review counts the chains whose turn is yours; those waiting on their author come after.
        tooltip.appendMarkdown(`**${title}** — ${plural(section.chains.filter((chain) => waitsOnYou(chain, section.name)).length, "chain")}`);
        if (section.summary.waiting_on_author) {
          tooltip.appendMarkdown(`, ${section.summary.waiting_on_author} waiting on the author`);
        }
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
      tooltip.appendMarkdown(rebase ? "Click to open Local stack." : headline ? "Click to open it." : "Click to open the overview.");
      // How the views stay current: unobtrusive, only here and in the log.
      tooltip.appendMarkdown("\n\n").appendMarkdown(markdownText(refreshMode(events.events, pollSeconds(events.live))));
    }
    if (note) {
      tooltip.appendMarkdown("\n\n").appendMarkdown(markdownText(note));
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
