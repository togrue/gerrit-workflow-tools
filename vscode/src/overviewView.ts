// "Review chains": the headline, then both inbox sections. A chain row shows its top change;
// expanding it lists the chain's changes, base first.

import * as vscode from "vscode";

import { Chain, Headline, Member, Section } from "./api";
import { CHAIN_SORTS, ChainSort, chainDescription, plural, reasonWords, severity, sortedChains, votes } from "./format";
import { icon, severityIcon } from "./icons";
import { ChangeTarget, Model } from "./model";
import { changeRowContext } from "./rows";
import { ui } from "./ui";

type Node =
  | { kind: "headline"; headline: Headline }
  | { kind: "section"; section: Section }
  | { kind: "chain"; chain: Chain; section: Section }
  | { kind: "member"; member: Member; chain: Chain }
  | { kind: "info"; text: string; detail?: string; icon?: vscode.ThemeIcon };

const SECTION_TITLES: Record<string, string> = { "to-review": "To review", outgoing: "Outgoing" };
const SORT_KEY = "ger.chainSort";

function memberRev(member: Member): string {
  return member.local_sha ?? `change:${member.number}`;
}

/** The change a chain row stands for: its top one. */
function chainTop(chain: Chain): Member | undefined {
  return chain.members.find((m) => m.number === chain.top.number) ?? chain.members.at(-1);
}

export function memberTarget(member: Member, chain: Chain): ChangeTarget {
  return {
    rev: memberRev(member),
    title: `${member.number}: ${member.subject}`,
    number: member.number,
    url: member.url,
    local: member.local_sha !== null,
    chainRevs: chain.members.map(memberRev),
    chainKey: chain.key,
  };
}

/** The change a headline points at, found again in the overview it came from. */
export function headlineTarget(model: Model): ChangeTarget | undefined {
  const headline = model.overview?.headline;
  for (const section of model.overview?.sections ?? []) {
    for (const chain of section.chains) {
      const member = chain.members.find((m) => m.number === headline?.number);
      if (headline && chain.key === headline.chain && member) {
        return memberTarget(member, chain);
      }
    }
  }
  return undefined;
}

export class OverviewView implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(
    private readonly model: Model,
    private readonly memento: vscode.Memento,
  ) {
    model.onDidChange(() => this.changed.fire());
  }

  /** How the chains of each section are ordered; remembered per workspace. */
  get sort(): ChainSort {
    const stored = this.memento.get<string>(SORT_KEY, "state");
    return stored in CHAIN_SORTS ? (stored as ChainSort) : "state";
  }

  /** Ask how to order the chains: by state (what needs you first, as the service orders them) or by last activity. */
  async chooseSort(): Promise<void> {
    const current = this.sort;
    const items = (Object.keys(CHAIN_SORTS) as ChainSort[]).map((sort) => ({
      label: CHAIN_SORTS[sort],
      description: sort === current ? "current" : undefined,
      detail: sort === "state" ? "What needs you most comes first" : undefined,
      sort,
    }));
    const picked = await ui().pick(items, { title: "Sort review chains", placeHolder: "Applies to both sections" });
    if (picked && picked.sort !== current) {
      await this.memento.update(SORT_KEY, picked.sort);
      this.changed.fire();
    }
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      return this.roots();
    }
    if (node.kind === "section") {
      const chains = sortedChains(node.section.chains, this.sort);
      return chains.length
        ? chains.map((chain) => ({ kind: "chain", chain, section: node.section }))
        : [{ kind: "info", text: node.section.name === "to-review" ? "Nothing to review" : "No open changes" }];
    }
    if (node.kind === "chain" && node.chain.members.length > 1) {
      // Base first is push order; the list reads top-down like `git log`, newest first.
      return [...node.chain.members].reverse().map((member) => ({ kind: "member", member, chain: node.chain }));
    }
    return [];
  }

  private roots(): Node[] {
    const overview = this.model.overview;
    if (!overview) {
      return []; // the welcome text for the model's state says why, and what to do
    }
    if (overview.error) {
      return [{ kind: "info", text: overview.error.message, icon: icon("error", "list.errorForeground") }];
    }
    const nodes: Node[] = [];
    if (overview.headline) {
      nodes.push({ kind: "headline", headline: overview.headline });
    } else {
      nodes.push({ kind: "info", text: "Nothing needs you right now", icon: icon("pass", "testing.iconPassed") });
    }
    // Your own chains first: failed CI and comments there are yours to fix.
    const order = ["outgoing", "to-review"];
    const sections = [...(overview.sections ?? [])].sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
    return nodes.concat(sections.map((section) => ({ kind: "section", section })));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.kind) {
      case "headline": {
        const item = new vscode.TreeItem(node.headline.text);
        item.iconPath = icon("target", node.headline.reason === "ci-failed" ? "list.errorForeground" : "list.warningForeground");
        item.tooltip = "The most important thing right now. Click to open it.";
        item.command = { command: "ger.openHeadline", title: "Open" };
        return item;
      }
      case "section": {
        const { name, chains, summary } = node.section;
        const item = new vscode.TreeItem(SECTION_TITLES[name] ?? name, vscode.TreeItemCollapsibleState.Expanded);
        const notes = [plural(chains.length, "chain")];
        if (summary.ci_failures) {
          notes.push(`${summary.ci_failures} with failed CI`);
        }
        if (summary.comments) {
          notes.push(`${summary.comments} with comments`);
        }
        if (this.sort !== "state") {
          notes.push(this.sort === "newest" ? "newest first" : "oldest first");
        }
        item.description = notes.join(" · ");
        item.tooltip = node.section.query;
        return item;
      }
      case "chain":
        return this.chainItem(node.chain, node.section);
      case "member":
        return this.memberItem(node.member, node.chain);
      case "info": {
        const item = new vscode.TreeItem(node.text);
        item.description = node.detail;
        item.iconPath = node.icon;
        return item;
      }
    }
  }

  private chainItem(chain: Chain, section: Section): vscode.TreeItem {
    const active = this.model.active.has(chain.key);
    const single = chain.members.length === 1;
    const item = new vscode.TreeItem(
      chain.top.subject,
      single ? vscode.TreeItemCollapsibleState.None : vscode.TreeItemCollapsibleState.Collapsed,
    );
    item.id = `${section.name}/${chain.key}`;
    if (single) {
      // Nothing to expand: the row is the change.
      item.command = { command: "ger.openChange", title: "Show Change", arguments: [memberTarget(chain.members[0], chain)] };
    }
    item.description = `${active ? "● new activity · " : ""}${chainDescription(chain, section.name)}`;
    // To review, a clean chain is what wants you; outgoing, a clean chain is waiting on others.
    const state = severity(chain.attention_reasons);
    item.iconPath = section.name === "to-review" && state === "ok" ? icon("eye") : severityIcon(state);
    const lines = [
      `**${chain.top.subject}**`,
      `${chain.project} · ${chain.branch} · ${chain.owner.name}`,
      reasonWords(chain.attention_reasons) || "no open issues",
    ];
    if (chain.local) {
      lines.push("Commits of this chain are in your local stack.");
    }
    if (chain.partial_chain) {
      lines.push("Part of this chain sits on an older patchset of its parent.");
    }
    item.tooltip = new vscode.MarkdownString(lines.join("\n\n"));
    const top = chainTop(chain);
    item.contextValue = changeRowContext({ kind: single ? "change" : "chain", url: Boolean(chain.top.url), local: Boolean(top?.local_sha) });
    return item;
  }

  private memberItem(member: Member, chain: Chain): vscode.TreeItem {
    const item = new vscode.TreeItem(member.subject);
    item.id = `${chain.key}/${member.number}`;
    item.description = `${member.number} · ${votes(member)}${member.local_sha ? " · in your stack" : ""}`;
    item.iconPath = severityIcon(severity(member.attention_reasons));
    item.tooltip = reasonWords(member.attention_reasons) || "no open issues";
    item.contextValue = changeRowContext({ kind: "change", url: Boolean(member.url), local: member.local_sha !== null });
    item.command = { command: "ger.openChange", title: "Show Change", arguments: [memberTarget(member, chain)] };
    return item;
  }

  /** What a context-menu command on `node` acts on. */
  static target(node: Node | undefined): ChangeTarget | undefined {
    if (node?.kind === "member") {
      return memberTarget(node.member, node.chain);
    }
    if (node?.kind === "chain") {
      const top = chainTop(node.chain);
      return top ? memberTarget(top, node.chain) : undefined;
    }
    return undefined;
  }
}
