// "Review chains": the headline, then both inbox sections. A chain row shows its top change;
// expanding it lists the chain's changes, base first. While a staged review is open here, only the
// chain under review: one row per change, the one reviewed marked (`reviewFocus`), until you ask
// for all chains or the review ends. A change row is coloured by its comment state, and its hover
// lists the comments (commentHover.ts).

import * as vscode from "vscode";

import { Chain, Headline, Member, Section, Stack } from "./api";
import { ChangeComments, commentResource, commentTooltip } from "./commentHover";
import { commentState } from "./commentState";
import { ReviewFocus, reviewFocus } from "./reviewFocus";
import { memberTarget } from "./changeLoad";
import { CHAIN_SORTS, ChainSort, chainDescription, plural, reasonWords, sentence, sortedChains, votes } from "./format";
import { attentionThemeIcon, icon } from "./icons";
import { ChangeTarget, Model } from "./model";
import { SECTION_TITLES, TREE_WORDS, changeRowContext, waitsOnYou } from "./rows";
import { feedback, ui } from "./ui";

type Node =
  | { kind: "headline"; headline: Headline }
  | { kind: "section"; section: Section }
  | { kind: "waiting"; section: Section; chains: Chain[] }
  | { kind: "chain"; chain: Chain; section: Section }
  | { kind: "member"; member: Member; chain: Chain; section: Section; reviewed?: boolean }
  | { kind: "info"; text: string; detail?: string; icon?: vscode.ThemeIcon; command?: vscode.Command };

const SORT_KEY = "ger.chainSort";

/** The change a chain row stands for: its top one. */
function chainTop(chain: Chain): Member | undefined {
  return chain.members.find((m) => m.number === chain.top.number) ?? chain.members.at(-1);
}

/** The change a headline points at, found again in the overview it came from. */
export function headlineTarget(model: Model): ChangeTarget | undefined {
  const headline = model.overview?.headline;
  for (const section of model.overview?.sections ?? []) {
    for (const chain of section.chains) {
      const member = chain.members.find((m) => m.number === headline?.number);
      if (headline && chain.key === headline.chain && member) {
        return memberTarget(member, chain, model.stack);
      }
    }
  }
  return undefined;
}

export class OverviewView implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;

  /** The review you asked to see all chains during (its chain key and change): its focus stays off until another review. */
  private unfocused: string | undefined;
  /** Where hovers get the comments from. Set by the extension. */
  comments: ChangeComments = { chains: () => Promise.resolve(undefined), drafts: () => 0 };

  constructor(
    private readonly model: Model,
    private readonly memento: vscode.Memento,
  ) {
    model.onDidChange(() => this.refocus());
  }

  /** The review the list is narrowed to, unless you asked for all chains during it. */
  get focus(): ReviewFocus | undefined {
    const focus = reviewFocus(this.model.overview, this.model.stack);
    return focus && focusKey(focus) !== this.unfocused ? focus : undefined;
  }

  /** Redraw, and say in the title bar which way back there is: All Chains while focused, Focus on the Review otherwise during one. */
  refocus(): void {
    const available = reviewFocus(this.model.overview, this.model.stack);
    if (!available) {
      this.unfocused = undefined; // the review ended: the next one is focused again
    }
    void vscode.commands.executeCommand("setContext", "ger.reviewFocus", Boolean(this.focus));
    void vscode.commands.executeCommand("setContext", "ger.reviewFocusOff", Boolean(available) && !this.focus);
    this.changed.fire();
  }

  /** All Chains: the full list again, for the rest of this review. */
  showAll(): void {
    const focus = this.focus;
    if (focus) {
      this.unfocused = focusKey(focus);
      this.refocus();
    }
  }

  /** Back to the chain under review. */
  focusReview(): void {
    this.unfocused = undefined;
    this.refocus();
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
      feedback(`Chains sorted ${picked.label.charAt(0).toLowerCase()}${picked.label.slice(1)}`);
    }
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      return this.roots();
    }
    if (node.kind === "section") {
      const all = sortedChains(node.section.chains, this.sort);
      if (!all.length) {
        return [{ kind: "info", text: node.section.name === "to-review" ? TREE_WORDS.nothingToReview : TREE_WORDS.noChanges }];
      }
      // To review lists what waits on you; the chains you reviewed wait on their author, folded below.
      const yours = all.filter((chain) => waitsOnYou(chain, node.section.name));
      const waiting = all.filter((chain) => !yours.includes(chain));
      const rows: Node[] = yours.length ? yours.map((chain) => ({ kind: "chain", chain, section: node.section })) : [{ kind: "info", text: TREE_WORDS.nothingWaitsOnYou }];
      return waiting.length ? [...rows, { kind: "waiting", section: node.section, chains: waiting }] : rows;
    }
    if (node.kind === "waiting") {
      return node.chains.map((chain) => ({ kind: "chain", chain, section: node.section }));
    }
    if (node.kind === "chain" && node.chain.members.length > 1) {
      // Base first is push order; the list reads top-down like `git log`, newest first.
      return [...node.chain.members].reverse().map((member) => ({ kind: "member", member, chain: node.chain, section: node.section }));
    }
    return [];
  }

  private roots(): Node[] {
    const overview = this.model.overview;
    if (!overview) {
      return []; // the welcome text for the model's state says why, and what to do
    }
    const focus = this.focus;
    if (focus) {
      return this.focused(focus);
    }
    const nodes: Node[] = [];
    if (overview.headline) {
      // A rebase underway still leads when the inbox cannot be read.
      nodes.push({ kind: "headline", headline: overview.headline });
    }
    if (overview.error) {
      return nodes.concat({ kind: "info", text: overview.error.message, icon: icon("error", "list.errorForeground") });
    }
    if (!overview.headline) {
      nodes.push({ kind: "info", text: TREE_WORDS.nothingNeedsYou, icon: icon("pass", "testing.iconPassed") });
    }
    // What waits on your review first: somebody cannot go on without you. Your own chains below.
    const order = ["to-review", "outgoing"];
    const sections = [...(overview.sections ?? [])].sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
    return nodes.concat(sections.map((section) => ({ kind: "section", section })));
  }

  /**
   * The chain under review alone, newest first as an expanded chain reads, and above it the way back
   * to every chain. No headline: it is about the others.
   */
  private focused({ chain, section, number }: ReviewFocus): Node[] {
    const others = (this.model.overview?.sections ?? []).reduce((count, s) => count + s.chains.length, 0) - 1;
    const back: Node = {
      kind: "info",
      text: "All chains",
      detail: others > 0 ? `back to the full list: ${plural(others, "more chain")}` : "back to the full list",
      icon: icon("arrow-left"),
      command: { command: "ger.showAllChains", title: "All Chains" },
    };
    const members = [...chain.members].reverse().map((member): Node => ({ kind: "member", member, chain, section, reviewed: member.number === number }));
    return [back, ...members];
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.kind) {
      case "headline": {
        const item = new vscode.TreeItem(node.headline.text);
        item.iconPath = icon("target", node.headline.severity === "error" ? "list.errorForeground" : "list.warningForeground");
        item.tooltip = "The most important thing right now. Click to open it.";
        item.command = { command: "ger.openHeadline", title: "Open" };
        return item;
      }
      case "section": {
        const { name, chains, summary } = node.section;
        const item = new vscode.TreeItem(SECTION_TITLES[name] ?? name, vscode.TreeItemCollapsibleState.Expanded);
        const notes = [plural(chains.filter((chain) => waitsOnYou(chain, name)).length, "chain")];
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
      case "waiting": {
        const item = new vscode.TreeItem(TREE_WORDS.waitingOnAuthor, vscode.TreeItemCollapsibleState.Collapsed);
        item.id = `${node.section.name}/waiting-on-author`;
        item.description = plural(node.chains.length, "chain");
        item.iconPath = icon("clock");
        item.tooltip = "Chains you reviewed: their author's turn, as Gerrit's attention set says. A new patchset or a reply to you brings one back up.";
        return item;
      }
      case "chain":
        return this.chainItem(node.chain, node.section);
      case "member":
        return this.memberItem(node.member, node.chain, node.section, node.reviewed);
      case "info": {
        const item = new vscode.TreeItem(node.text);
        item.description = node.detail;
        item.iconPath = node.icon;
        item.command = node.command;
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
      // Nothing to expand: the row is the change, coloured by its comments.
      const target = memberTarget(chain.members[0], chain, this.model.stack);
      item.command = { command: "ger.openChange", title: "Show Change", arguments: [target] };
      item.resourceUri = commentResource(chain.members[0], this.comments.drafts(target));
    }
    item.description = `${active ? "● new activity · " : ""}${chainDescription(chain, section.name)}`;
    // To review, a clean chain whose turn is yours is what wants you, one you reviewed waits on its author;
    // outgoing, a clean chain is waiting on others.
    const toReview = section.name === "to-review";
    item.iconPath = toReview && chain.severity === "ok" ? icon(waitsOnYou(chain, section.name) ? "eye" : "clock") : attentionThemeIcon(chain);
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
    item.contextValue = changeRowContext({ kind: single ? "change" : "chain", url: Boolean(chain.top.url), local: Boolean(top?.local_sha), toReview });
    return single ? this.withComments(item, chain.members[0], chain) : item;
  }

  /** What a row's tooltip said before its comments, by row: VS Code resolves a tooltip only when it is left unset. */
  private readonly heads = new WeakMap<vscode.TreeItem, string>();

  /** A change row with comments gets its tooltip on hover (`resolveTreeItem`), the comments listed in it. */
  private withComments(item: vscode.TreeItem, member: Member, chain: Chain): vscode.TreeItem {
    if (commentState(member, this.comments.drafts(memberTarget(member, chain, this.model.stack)))) {
      this.heads.set(item, item.tooltip instanceof vscode.MarkdownString ? item.tooltip.value : typeof item.tooltip === "string" ? item.tooltip : "");
      item.tooltip = undefined;
    }
    return item;
  }

  private memberItem(member: Member, chain: Chain, section: Section, reviewed = false): vscode.TreeItem {
    // The change under review stands out: its subject highlighted, and it says so first.
    const item = new vscode.TreeItem(reviewed ? { label: member.subject, highlights: [[0, member.subject.length]] } : member.subject);
    item.id = `${chain.key}/${member.number}`;
    const notReady = member.not_ready ? ` · ${member.not_ready}` : "";
    item.description = `${reviewed ? "reviewing · " : ""}${member.number} · ${votes(member)}${notReady}${member.local_sha ? " · in your stack" : ""}`;
    // Under To review, a change with nothing wrong shows whose turn it is: an eye while it is yours. One
    // not ready yet is part of the chain, not of your queue: greyed out.
    const toReview = section.name === "to-review";
    item.iconPath = member.not_ready
      ? icon("circle-slash", "disabledForeground")
      : toReview && member.severity === "ok" && member.your_turn
        ? icon("eye")
        : attentionThemeIcon(member);
    const words = reasonWords(member.attention_reasons) || (member.your_turn ? "waiting for your review" : "no open issues");
    item.tooltip = member.not_ready ? `Not ready for review: ${member.not_ready}. ${sentence(words)}.` : words;
    if (reviewed) {
      item.iconPath = icon("eye", "focusBorder");
    }
    item.contextValue = changeRowContext({ kind: "change", url: Boolean(member.url), local: member.local_sha !== null });
    const target = memberTarget(member, chain, this.model.stack);
    item.command = { command: "ger.openChange", title: "Show Change", arguments: [target] };
    item.resourceUri = commentResource(member, this.comments.drafts(target));
    return this.withComments(item, member, chain);
  }

  /** On hover, a change row with comments lists them below what its tooltip said: read once, then kept with the row. */
  async resolveTreeItem(item: vscode.TreeItem, node: Node): Promise<vscode.TreeItem> {
    const member = node.kind === "member" ? node.member : node.kind === "chain" && node.chain.members.length === 1 ? node.chain.members[0] : undefined;
    const chain = node.kind === "member" || node.kind === "chain" ? node.chain : undefined;
    if (!member || !chain) {
      return item;
    }
    const target = memberTarget(member, chain, this.model.stack);
    const state = commentState(member, this.comments.drafts(target));
    if (!state) {
      return item;
    }
    const chains = await this.comments.chains(target).catch(() => undefined);
    const head = this.heads.get(item) ?? "";
    // Apply writes into your file: only on your own commit of the local stack, outside a review.
    const applies = target.local && section(node) === "outgoing" && !this.model.stack?.staged_review;
    item.tooltip = commentTooltip(head, state, chains ?? [], target, applies);
    return item;
  }

  /** What a context-menu command on `node` acts on; `stack` is the local stack it was drawn with. */
  static target(node: Node | undefined, stack: Stack | undefined): ChangeTarget | undefined {
    if (node?.kind === "member") {
      return memberTarget(node.member, node.chain, stack);
    }
    if (node?.kind === "chain") {
      const top = chainTop(node.chain);
      return top ? memberTarget(top, node.chain, stack) : undefined;
    }
    return undefined;
  }
}

/** Which review a focus is about: the chain and the change it holds. */
const focusKey = (focus: ReviewFocus) => `${focus.chain.key}#${focus.number}`;

/** The inbox section a row sits in. */
function section(node: Node): string | undefined {
  return "section" in node ? node.section.name : undefined;
}

