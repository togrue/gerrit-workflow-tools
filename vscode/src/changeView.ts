// "Change": one change in detail — what it needs, its files (as diffs), its comment chains and,
// on demand, the CI findings with the ones this change introduced on top.

import * as path from "node:path";
import * as vscode from "vscode";

import { CiContent, CiFinding, Comment, CommentChain, Diff, FileChange, ShownCommit } from "./api";
import { fileState, plural, reasonWords, sentence, severity, votes } from "./format";
import { icon, severityIcon } from "./icons";
import { ChangeTarget, Model, describeError } from "./model";

export const FILE_SCHEME = "ger-file";
const NOT_FILES = ["/COMMIT_MSG", "/PATCHSET_LEVEL", "/MERGE_LIST"];

type Group = "delta" | "files" | "comments" | "ci";

export type Node =
  | { kind: "info"; text: string; detail?: string; icon?: vscode.ThemeIcon; tooltip?: string; command?: vscode.Command }
  | { kind: "group"; group: Group }
  | { kind: "file"; file: FileChange; diff: Diff }
  | { kind: "thread"; chain: CommentChain }
  | { kind: "comment"; comment: Comment; chain: CommentChain }
  | { kind: "link"; content: CiContent }
  | { kind: "finding"; finding: CiFinding; introduced: boolean | null };

export interface Location {
  path: string;
  line: number | null;
}

/** A file as it is in a commit: `ger-file:/<path>?<commit>`. */
export function fileUri(rev: string, file: string): vscode.Uri {
  return vscode.Uri.from({ scheme: FILE_SCHEME, path: `/${file}`, query: rev });
}

interface CiState {
  contents: CiContent[];
  introduced: Set<string> | null;
}

export class ChangeView implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<Node | undefined | void>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly loaded = new vscode.EventEmitter<void>();
  /** Fires when a change (or its reload) has arrived. */
  readonly onDidLoad = this.loaded.event;

  target: ChangeTarget | undefined;
  shown: ShownCommit | undefined;
  diff: Diff | undefined;
  /** What changed since you last reviewed; only asked for changes that are not your local commits. */
  delta: Diff | undefined;
  private problem: string | undefined;
  private ci: Promise<CiState> | undefined;
  private readonly groups = new Map<Group, Node>();
  private threads: Node[] = [];

  constructor(private readonly model: Model) {}

  /** Show `target`; with no argument, reload the change that is shown. */
  async open(target: ChangeTarget | undefined = this.target): Promise<void> {
    if (!target) {
      return;
    }
    this.target = target;
    this.ci = undefined;
    // The title bar offers "Edit Commit" for your own stack commit, "Check Out for Review" otherwise.
    void vscode.commands.executeCommand("setContext", "ger.changeIsLocal", Boolean(target.local));
    await vscode.window.withProgress({ location: { viewId: "ger.change" } }, async () => {
      try {
        const client = await this.model.client();
        const [commits, diff, delta] = await Promise.all([
          client.show([target.rev], { comments: "all", context: 3 }),
          client.diff(target.rev).catch(() => undefined),
          target.local ? undefined : client.reviewDelta(target.rev).catch(() => undefined),
        ]);
        [this.shown, this.diff, this.delta, this.problem] = [commits[0], diff, delta, undefined];
      } catch (error) {
        [this.shown, this.diff, this.delta, this.problem] = [undefined, undefined, undefined, describeError(error)];
      }
    });
    // Which save button a new comment box offers: "Save Draft" or "Save Local Draft" (plus "Save Private Note").
    void vscode.commands.executeCommand("setContext", "ger.newComment", this.shown?.new_comment ?? "none");
    this.changed.fire();
    this.loaded.fire();
  }

  /** Show nothing: the change that was shown is gone. */
  close(): void {
    [this.target, this.shown, this.diff, this.delta, this.problem, this.ci] = [undefined, undefined, undefined, undefined, undefined, undefined];
    void vscode.commands.executeCommand("setContext", "ger.changeIsLocal", false);
    void vscode.commands.executeCommand("setContext", "ger.newComment", "none");
    this.changed.fire();
    this.loaded.fire();
  }

  /** Unresolved comment chains first, then by file and line. */
  chains(): CommentChain[] {
    const chains = [...(this.shown?.comment_chains ?? [])];
    const open = (c: CommentChain) => (c.resolved ? 1 : 0);
    return chains.sort((a, b) => open(a) - open(b) || a.path.localeCompare(b.path) || (a.line ?? 0) - (b.line ?? 0));
  }

  unresolved(): CommentChain[] {
    return this.chains().filter((chain) => !chain.resolved);
  }

  threadNode(chain: CommentChain): Node | undefined {
    return this.threads.find((node) => node.kind === "thread" && node.chain.root_id === chain.root_id);
  }

  // --- Tree -------------------------------------------------------------------------

  getParent(node: Node): Node | undefined {
    if (node.kind === "thread") {
      return this.groups.get("comments");
    }
    if (node.kind === "comment") {
      return this.threadNode(node.chain);
    }
    return undefined;
  }

  async getChildren(node?: Node): Promise<Node[]> {
    if (!node) {
      return this.roots();
    }
    if (node.kind === "group") {
      return this.groupChildren(node.group);
    }
    if (node.kind === "thread") {
      return node.chain.comments.slice(1).map((comment) => ({ kind: "comment", comment, chain: node.chain }));
    }
    if (node.kind === "link") {
      const introduced = (await this.loadCi()).introduced;
      const findings = node.content.findings.map((finding) => ({
        kind: "finding" as const,
        finding,
        introduced: introduced ? introduced.has(finding.fingerprint) : null,
      }));
      // What this change broke comes first; what it inherited from below follows.
      return findings.sort((a, b) => Number(b.introduced ?? false) - Number(a.introduced ?? false));
    }
    return [];
  }

  private group(group: Group): Node {
    let node = this.groups.get(group);
    if (!node) {
      node = { kind: "group", group };
      this.groups.set(group, node);
    }
    return node;
  }

  private roots(): Node[] {
    if (this.problem) {
      return [{ kind: "info", text: this.problem, icon: icon("error", "list.errorForeground") }];
    }
    const commit = this.shown;
    if (!commit) {
      return [];
    }
    const state = severity(commit.attention_reasons);
    const nodes: Node[] = [
      {
        kind: "info",
        text: sentence(reasonWords(commit.attention_reasons)) || (commit.submittable ? "Ready to submit" : "Nothing to do"),
        detail: commit.pushed ? votes(commit) : "not on Gerrit",
        icon: severityIcon(state),
        tooltip: commit.summary,
      },
    ];
    if (commit.patchset_status === "newer") {
      nodes.push({
        kind: "info",
        text: "This commit is ahead of Gerrit",
        detail: "votes and CI are about the pushed patchset and are hidden until you push",
        icon: icon("history", "list.warningForeground"),
      });
    } else if (commit.patchset_status === "outdated") {
      nodes.push({
        kind: "info",
        text: "Gerrit has a newer patchset than this commit",
        detail: "comments and CI below are about Gerrit's version",
        icon: icon("history", "list.warningForeground"),
      });
    }
    // Only once you have reviewed an earlier patchset is there a "since".
    if (this.delta?.since && this.delta.since.patch_set !== null) {
      nodes.push(this.group("delta"));
    }
    nodes.push(this.group("files"), this.group("comments"));
    if (commit.ci_failures.length || commit.ci_links.length || commit.verified === -1) {
      nodes.push(this.group("ci"));
    }
    return nodes;
  }

  private async groupChildren(group: Group): Promise<Node[]> {
    if (group === "files" || group === "delta") {
      const diff = group === "files" ? this.diff : this.delta;
      return diff ? diff.files.map((file) => ({ kind: "file", file, diff })) : [];
    }
    if (group === "comments") {
      this.threads = this.chains().map((chain) => ({ kind: "thread", chain }));
      return this.threads;
    }
    try {
      const { contents } = await this.loadCi();
      if (!contents.length) {
        const names = this.shown?.ci_failures.join(", ");
        return [{ kind: "info", text: "No CI link was found for the failed build", detail: names }];
      }
      return contents.map((content) => ({ kind: "link", content }));
    } catch (error) {
      return [{ kind: "info", text: describeError(error), icon: icon("error", "list.errorForeground") }];
    }
  }

  /** CI content is fetched only when the CI group is opened: it means downloading build logs. */
  private loadCi(): Promise<CiState> {
    if (!this.ci) {
      this.ci = this.fetchCi();
    }
    return this.ci;
  }

  private async fetchCi(): Promise<CiState> {
    const target = this.target;
    if (!target) {
      return { contents: [], introduced: null };
    }
    const client = await this.model.client();
    // Introduced findings need the parent: ask for the whole chain, then pick this change out of it.
    let commits: ShownCommit[];
    let index: number;
    if (target.chainRevs && target.chainRevs.includes(target.rev)) {
      commits = await client.show(target.chainRevs, { ci: true, chain: true });
      index = target.chainRevs.indexOf(target.rev);
    } else if (target.local) {
      commits = await client.show([], { ci: true, stack: true });
      index = commits.findIndex((c) => c.sha === target.rev);
    } else {
      commits = await client.show([target.rev], { ci: true });
      index = 0;
    }
    const commit = commits[index];
    const introduced = commit?.ci_introduced;
    return { contents: commit?.ci_content ?? [], introduced: introduced ? new Set(introduced) : null };
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.kind) {
      case "info": {
        const item = new vscode.TreeItem(node.text);
        item.description = node.detail;
        item.iconPath = node.icon;
        item.tooltip = node.tooltip ?? (node.detail ? `${node.text} — ${node.detail}` : undefined);
        item.command = node.command;
        return item;
      }
      case "group":
        return this.groupItem(node.group);
      case "file":
        return this.fileItem(node.file, node.diff);
      case "thread":
        return this.threadItem(node.chain);
      case "comment": {
        const comment = node.comment;
        const item = new vscode.TreeItem(comment.author ?? (comment.state === "published" ? "?" : "you"));
        item.description = `${comment.state === "published" ? "" : `[${comment.state}] `}${comment.message.split("\n")[0]}`;
        item.tooltip = new vscode.MarkdownString(comment.message);
        item.iconPath = icon(comment.state === "published" ? "account" : "edit");
        return item;
      }
      case "link": {
        const { content } = node;
        const failed = content.provenance.error !== null;
        const item = new vscode.TreeItem(
          content.provenance.build ?? content.label,
          failed ? vscode.TreeItemCollapsibleState.None : vscode.TreeItemCollapsibleState.Expanded,
        );
        item.description = failed ? `could not be read: ${content.provenance.error}` : plural(content.findings.length, "finding");
        item.iconPath = icon(failed ? "warning" : "beaker", failed ? "list.warningForeground" : undefined);
        item.tooltip = `${content.provenance.url}${content.provenance.truncated ? "\nThe log was cut: findings may be incomplete." : ""}`;
        item.command = { command: "vscode.open", title: "Open build", arguments: [vscode.Uri.parse(content.provenance.url)] };
        return item;
      }
      case "finding":
        return this.findingItem(node.finding, node.introduced);
    }
  }

  private groupItem(group: Group): vscode.TreeItem {
    const commit = this.shown;
    if (group === "delta") {
      const since = this.delta?.since;
      const files = this.delta?.files ?? [];
      const item = new vscode.TreeItem(
        "Since your review",
        files.length ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None,
      );
      item.iconPath = icon(since?.up_to_date ? "pass" : "git-compare", since?.up_to_date ? "testing.iconPassed" : undefined);
      item.description = since?.up_to_date
        ? `nothing new — you reviewed patchset ${since.current_patch_set}`
        : `patchset ${since?.patch_set} → ${since?.current_patch_set} · ${plural(files.length, "file")}${since?.rebased ? " · rebased" : ""}`;
      item.tooltip = since?.rebased
        ? "The change was rebased since your review. Only files the change touches are listed; inside them, edits that came in from underneath show too."
        : "What the author changed after the patchset you last voted or commented on.";
      return item;
    }
    if (group === "files") {
      const files = this.diff?.files ?? [];
      const item = new vscode.TreeItem("Files", vscode.TreeItemCollapsibleState.Expanded);
      const added = files.reduce((sum, f) => sum + (f.insertions ?? 0), 0);
      const deleted = files.reduce((sum, f) => sum + (f.deletions ?? 0), 0);
      item.description = this.diff ? `${files.length} · +${added} −${deleted}` : "the commit is not available here";
      return item;
    }
    if (group === "comments") {
      const open = this.unresolved().length;
      const total = commit?.comment_chains.length ?? 0;
      const item = new vscode.TreeItem(
        "Comments",
        open ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed,
      );
      item.description = total ? `${open} unresolved of ${total}` : "none";
      return item;
    }
    const item = new vscode.TreeItem("CI", vscode.TreeItemCollapsibleState.Collapsed);
    item.description = commit?.ci_failures.join(", ") || "failed — open to fetch the findings";
    item.iconPath = icon("error", "list.errorForeground");
    return item;
  }

  private fileItem(file: FileChange, diff: Diff): vscode.TreeItem {
    const item = new vscode.TreeItem(path.posix.basename(file.path));
    item.id = `${diff === this.delta ? "delta" : "files"}/${file.path}`;
    const dir = path.posix.dirname(file.path);
    const counts = file.insertions === null ? "binary" : `+${file.insertions} −${file.deletions}`;
    item.description = `${dir === "." ? "" : `${dir} · `}${fileState(file.status)} ${counts}`;
    item.tooltip = file.old_path ? `${file.old_path} → ${file.path}` : file.path;
    item.resourceUri = vscode.Uri.file(path.join(this.model.repo, file.path));
    item.iconPath = vscode.ThemeIcon.File;
    item.command = { command: "ger.openDiff", title: "Open Diff", arguments: [file, diff] };
    return item;
  }

  private threadItem(chain: CommentChain): vscode.TreeItem {
    const first = chain.comments[0];
    const where = NOT_FILES.includes(chain.path) ? "whole change" : `${path.posix.basename(chain.path)}${chain.line ? `:${chain.line}` : ""}`;
    const replies = chain.comments.length - 1;
    const item = new vscode.TreeItem(
      first?.message.split("\n")[0] ?? where,
      replies ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None,
    );
    item.id = `thread/${chain.root_id}`;
    const answered = !chain.resolved && chain.resolved_after_publish;
    const held = chain.held && !chain.resolved;
    const notes = [held ? "kept for you" : "", answered ? "your draft resolves it" : "", where, first?.author ?? "", replies ? `${replies} ${replies === 1 ? "reply" : "replies"}` : ""];
    item.description = notes.filter(Boolean).join(" · ");
    item.iconPath = chain.resolved
      ? icon("pass", "disabledForeground")
      : answered
        ? icon("edit", "testing.iconPassed")
        : held
          ? icon("person", "list.warningForeground")
          : icon("comment-unresolved", "list.warningForeground");
    item.tooltip = new vscode.MarkdownString(
      chain.comments.map((c) => `**${c.author ?? "you"}** ${c.state === "published" ? "" : `_(${c.state})_`}\n\n${c.message}`).join("\n\n---\n\n"),
    );
    item.contextValue = chain.resolved ? "ger.thread.resolved" : `ger.thread.open${chain.held ? ".held" : ".free"}`;
    const location: Location = { path: chain.path, line: chain.line };
    item.command = { command: "ger.openLocation", title: "Go to Comment", arguments: [location] };
    return item;
  }

  private findingItem(finding: CiFinding, introduced: boolean | null): vscode.TreeItem {
    const item = new vscode.TreeItem(finding.message);
    const where = finding.path ? `${finding.path}${finding.line ? `:${finding.line}` : ""}` : "";
    const origin = introduced === null ? "" : introduced ? "introduced here" : "also fails on the parent";
    item.description = [finding.tool, where, origin].filter(Boolean).join(" · ");
    item.iconPath =
      introduced === null
        ? icon("question")
        : introduced
          ? icon("flame", "list.errorForeground")
          : icon("circle-outline", "disabledForeground");
    item.tooltip = new vscode.MarkdownString(
      [finding.message, finding.detail ? `\`\`\`\n${finding.detail}\n\`\`\`` : "", origin].filter(Boolean).join("\n\n"),
    );
    if (finding.path) {
      const location: Location = { path: finding.path, line: finding.line };
      item.command = { command: "ger.openLocation", title: "Go to Finding", arguments: [location] };
    }
    return item;
  }

  // --- Opening things ---------------------------------------------------------------

  /** The diff of one file: parent on the left, the change on the right. */
  async openDiff(file: FileChange, diff: Diff | undefined = this.diff): Promise<void> {
    if (!diff || diff.base === null) {
      return;
    }
    const left = fileUri(diff.base, file.old_path ?? file.path);
    // The commit you are standing on diffs against the real file, so you can edit in place.
    const editable = this.target?.local && this.model.stack?.head === diff.commit && file.status !== "D";
    const right = editable ? vscode.Uri.file(path.join(this.model.repo, file.path)) : fileUri(diff.commit, file.path);
    const range = diff.since ? `patchset ${diff.since.patch_set} ↔ ${diff.since.current_patch_set}` : diff.commit.slice(0, 8);
    const title = `${path.posix.basename(file.path)} (${range})`;
    await vscode.commands.executeCommand("vscode.diff", left, right, title);
  }

  /** The document a path of the shown change lives in: your file for a local commit, the commit's otherwise. */
  locationUri(file: string): vscode.Uri {
    const commit = this.diff?.commit ?? this.shown?.sha;
    return this.target?.local || !commit ? vscode.Uri.file(path.join(this.model.repo, file)) : fileUri(commit, file);
  }

  /** Open where a comment or a CI finding points. */
  async openLocation(location: Location): Promise<void> {
    if (NOT_FILES.includes(location.path)) {
      return;
    }
    const uri = this.locationUri(location.path);
    const line = Math.max(0, (location.line ?? 1) - 1);
    try {
      await vscode.window.showTextDocument(uri, { selection: new vscode.Range(line, 0, line, 0), preview: true });
    } catch (error) {
      void vscode.window.showWarningMessage(`Cannot open ${location.path}: ${describeError(error)}`);
    }
  }
}
