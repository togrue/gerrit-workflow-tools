// "Local stack": the commits above the upstream, newest first, with drag-and-drop reordering.

import * as vscode from "vscode";

import { StackCommit, StackStep } from "./api";
import { blockedMove, commitDescription, droppedOn, moved, reasonWords, severity } from "./format";
import { icon, severityIcon } from "./icons";
import { ChangeTarget, Model, describeError } from "./model";

const MIME = "application/vnd.code.tree.ger.stack";

type Node =
  | { kind: "commit"; commit: StackCommit }
  | { kind: "info"; text: string; detail?: string; icon?: vscode.ThemeIcon; tooltip?: string; command?: vscode.Command };

export function commitTarget(commit: StackCommit): ChangeTarget {
  return { rev: commit.sha, title: commit.summary, url: commit.gerrit_url, local: true };
}

export class StackView implements vscode.TreeDataProvider<Node>, vscode.TreeDragAndDropController<Node> {
  readonly dragMimeTypes = [MIME];
  readonly dropMimeTypes = [MIME];

  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly model: Model) {
    model.onDidChange(() => this.changed.fire());
  }

  /** Oldest first, as git and the service order them. */
  private commits(): StackCommit[] {
    return this.model.stack?.commits ?? [];
  }

  getChildren(node?: Node): Node[] {
    if (node) {
      return [];
    }
    const local = this.model.overview?.local;
    if (!local) {
      return [];
    }
    if ("error" in local) {
      const warning = icon("warning", "list.warningForeground");
      if (local.needs_upstream) {
        const branch = this.model.upstreamChoice?.branch ?? "This branch";
        const why = `${branch} has no upstream, so ger cannot tell where your stack starts`;
        return [{ kind: "info", text: why, tooltip: local.error.message, icon: warning }, ...this.upstreamRows()];
      }
      return [{ kind: "info", text: local.error.message.split("\n")[0], tooltip: local.error.message, icon: warning }];
    }
    const nodes: Node[] = [];
    if (local.rebase_in_progress) {
      nodes.push({ kind: "info", text: "A rebase is in progress", detail: "this is the branch as it was before", icon: icon("warning", "list.warningForeground") });
    }
    if (!local.commits.length) {
      nodes.push({ kind: "info", text: "No local commits", detail: `${local.branch ?? "HEAD"} is at ${local.stack.target_branch}` });
    }
    return nodes.concat([...local.commits].reverse().map((commit) => ({ kind: "commit", commit })));
  }

  /** The branch has no upstream, so no stack: offer the suggested one in one click, or a choice. */
  private upstreamRows(): Node[] {
    const choice = this.model.upstreamChoice;
    const rows: Node[] = [];
    if (choice?.suggested) {
      rows.push({
        kind: "info",
        text: `Track ${choice.suggested}`,
        detail: choice.why ?? undefined,
        tooltip: `Make ${choice.suggested} the upstream of ${choice.branch}: the stack is the commits above it, and push goes to its branch on Gerrit.`,
        icon: icon("git-branch"),
        command: { command: "ger.setUpstream", title: "Track", arguments: [choice.suggested] },
      });
    }
    rows.push({
      kind: "info",
      text: choice?.suggested ? "Choose another upstream branch…" : "Choose an upstream branch…",
      icon: icon("list-selection"),
      command: { command: "ger.setUpstream", title: "Choose" },
    });
    return rows;
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "info") {
      const item = new vscode.TreeItem(node.text);
      item.description = node.detail;
      item.iconPath = node.icon;
      item.tooltip = node.tooltip;
      item.command = node.command;
      return item;
    }
    const commit = node.commit;
    const commits = this.commits();
    const index = commits.findIndex((c) => c.sha === commit.sha);
    const [below, above] = [commits[index - 1], commits[index + 1]];
    const upConflict = above && this.model.swapConflict(commit.sha, above.sha);
    const downConflict = below && this.model.swapConflict(below.sha, commit.sha);
    const item = new vscode.TreeItem(commit.summary);
    item.id = commit.sha;
    item.description = `${commit.is_head ? "HEAD · " : ""}${commitDescription(commit)}`;
    item.iconPath = commit.pushed ? severityIcon(severity(commit.attention_reasons)) : icon("cloud-upload");
    item.tooltip = new vscode.MarkdownString(
      [
        `**${commit.summary}**`,
        reasonWords(commit.attention_reasons) || "no open issues",
        commit.held_back ? "Held back by the ready boundary: `ger push` will not send it." : "",
        commit.reviewers.length ? `Reviewers: ${commit.reviewers.join(", ")}` : "",
        upConflict ? blockedMove(commit.summary, above.summary, upConflict) : "",
        downConflict ? blockedMove(commit.summary, below.summary, downConflict) : "",
      ]
        .filter(Boolean)
        .join("\n\n"),
    );
    // `.upx` / `.downx`: the neighbour is there, but the two would conflict in each other's place.
    const flags = [commit.gerrit_url ? ".url" : "", above ? (upConflict ? ".upx" : ".up") : "", below ? (downConflict ? ".downx" : ".down") : ""];
    item.contextValue = `ger.commit${flags.join("")}`;
    item.command = { command: "ger.openChange", title: "Show Change", arguments: [commitTarget(commit)] };
    return item;
  }

  static target(node: Node | undefined): ChangeTarget | undefined {
    return node?.kind === "commit" ? commitTarget(node.commit) : undefined;
  }

  // --- Reordering -------------------------------------------------------------------

  handleDrag(source: readonly Node[], data: vscode.DataTransfer): void {
    const shas = source.filter((n) => n.kind === "commit").map((n) => (n as { commit: StackCommit }).commit.sha);
    data.set(MIME, new vscode.DataTransferItem(shas));
  }

  async handleDrop(target: Node | undefined, data: vscode.DataTransfer): Promise<void> {
    const value: unknown = data.get(MIME)?.value;
    const dragged = Array.isArray(value) ? value.filter((sha): sha is string => typeof sha === "string") : [];
    if (dragged.length !== 1 || target?.kind === "info") {
      return;
    }
    // The dragged commit takes the target's place, up or down; empty space under the list is the bottom.
    const order = droppedOn(
      this.commits().map((c) => c.sha),
      dragged[0],
      target?.commit.sha,
    );
    if (order) {
      await this.rewrite(order, "Stack reordered.");
    }
  }

  /** Move one commit a place later (`+1`) or earlier (`-1`) in the stack. */
  async move(node: Node | undefined, step: number): Promise<void> {
    if (node?.kind !== "commit") {
      return;
    }
    const shas = this.commits().map((c) => c.sha);
    const order = moved(shas, shas.indexOf(node.commit.sha), step);
    if (order) {
      await this.rewrite(order, "Stack reordered.");
    }
  }

  /** Say why a commit cannot move a place later (`+1`) or earlier (`-1`). */
  explainBlocked(node: Node | undefined, step: number): void {
    const commits = this.commits();
    const index = node?.kind === "commit" ? commits.findIndex((c) => c.sha === node.commit.sha) : -1;
    const [commit, other] = [commits[index], commits[index + step]];
    const conflict = commit && other && (step > 0 ? this.model.swapConflict(commit.sha, other.sha) : this.model.swapConflict(other.sha, commit.sha));
    if (conflict) {
      void vscode.window.showInformationMessage(`${blockedMove(commit.summary, other.summary, conflict)} Nothing was changed.`);
    }
  }

  /** Fold a commit into the one below it. One message survives whole, and with it its Change-Id. */
  async fold(node: Node | undefined, takeMessage: boolean): Promise<void> {
    const commits = this.commits();
    const index = node?.kind === "commit" ? commits.findIndex((c) => c.sha === node.commit.sha) : -1;
    if (index < 1) {
      return;
    }
    const [below, folded] = [commits[index - 1], commits[index]];
    const [kept, gone] = takeMessage ? [folded, below] : [below, folded];
    const action = takeMessage ? "fixup-take-message" : "fixup";
    const steps = commits.map((c): StackStep => (c.sha === folded.sha ? { sha: c.sha, action } : c.sha));
    const orphan = gone.pushed ? ` The Gerrit change of "${gone.summary}" stays open: abandon it there.` : "";
    await this.rewrite(steps, `Squashed into "${kept.summary}".${orphan}`);
  }

  async drop(node: Node | undefined): Promise<void> {
    if (node?.kind !== "commit") {
      return;
    }
    const commit = node.commit;
    const detail = commit.pushed
      ? "Its change stays open on Gerrit until you abandon it there. You can undo the drop right after."
      : "It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.";
    const answer = await vscode.window.showWarningMessage(`Drop "${commit.summary}" from the stack?`, { modal: true, detail }, "Drop");
    if (answer !== "Drop") {
      return;
    }
    const steps = this.commits().map((c): StackStep => (c.sha === commit.sha ? { sha: c.sha, action: "drop" } : c.sha));
    await this.rewrite(steps, `Dropped "${commit.summary}".`);
  }

  private async rewrite(steps: StackStep[], done: string): Promise<void> {
    try {
      const client = await this.model.client();
      const stack = await vscode.window.withProgress({ location: { viewId: "ger.stack" }, title: "Rewriting the stack" }, () =>
        client.rewrite(steps),
      );
      this.model.setStack(stack);
      void this.model.refresh();
      if (stack.head && stack.head !== stack.previous_head) {
        const undo = await vscode.window.showInformationMessage(`${done} Push to update Gerrit.`, "Undo");
        if (undo && stack.head) {
          this.model.setStack(await client.undo(stack.head, stack.previous_head));
          void this.model.refresh();
        }
      }
    } catch (error) {
      // All or nothing on the server: a refusal means the branch is exactly as it was.
      void vscode.window.showWarningMessage(`Stack left as it was: ${describeError(error)}`);
      void this.model.refresh();
    }
  }
}
