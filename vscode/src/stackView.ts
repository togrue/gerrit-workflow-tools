// "Local stack": the commits above the upstream, newest first, with drag-and-drop reordering.

import * as vscode from "vscode";

import { StackCommit, StackStep } from "./api";
import { commitDescription, moved, reasonWords, severity } from "./format";
import { icon, severityIcon } from "./icons";
import { ChangeTarget, Model, describeError } from "./model";

const MIME = "application/vnd.code.tree.ger.stack";

type Node = { kind: "commit"; commit: StackCommit } | { kind: "info"; text: string; detail?: string; icon?: vscode.ThemeIcon };

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
      return [{ kind: "info", text: local.error.message, icon: icon("warning", "list.warningForeground") }];
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

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "info") {
      const item = new vscode.TreeItem(node.text);
      item.description = node.detail;
      item.iconPath = node.icon;
      return item;
    }
    const commit = node.commit;
    const commits = this.commits();
    const index = commits.findIndex((c) => c.sha === commit.sha);
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
      ]
        .filter(Boolean)
        .join("\n\n"),
    );
    const flags = [commit.gerrit_url ? ".url" : "", index < commits.length - 1 ? ".up" : "", index > 0 ? ".down" : ""];
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
    const dragged: string[] = data.get(MIME)?.value ?? [];
    if (target?.kind !== "commit" || !dragged.length || dragged.includes(target.commit.sha)) {
      return;
    }
    // In the list (newest first) the dragged commits take the target's place, pushing it down.
    const shown = [...this.commits()].reverse().map((c) => c.sha);
    const rest = shown.filter((sha) => !dragged.includes(sha));
    rest.splice(rest.indexOf(target.commit.sha), 0, ...shown.filter((sha) => dragged.includes(sha)));
    await this.rewrite(rest.reverse(), "Stack reordered.");
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
