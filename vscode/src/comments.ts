// Unresolved comment chains of the shown change as editor comment threads, with draft replies.
// Threads sit on the working-tree files, so they are shown for local stack commits only.

import * as path from "node:path";
import * as vscode from "vscode";

import { CommentChain } from "./api";
import { ChangeView } from "./changeView";
import { Model, describeError } from "./model";

interface Anchor {
  rev: string;
  chain: CommentChain;
}

export class CommentThreads implements vscode.Disposable {
  private readonly controller = vscode.comments.createCommentController("ger", "Gerrit comments");
  private readonly anchors = new Map<vscode.CommentThread, Anchor>();
  private cursor = -1;

  constructor(
    private readonly model: Model,
    private readonly view: ChangeView,
    private readonly reveal: (chain: CommentChain) => void,
  ) {
    view.onDidLoad(() => this.rebuild());
  }

  private rebuild(): void {
    for (const thread of this.anchors.keys()) {
      thread.dispose();
    }
    this.anchors.clear();
    this.cursor = -1;
    const target = this.view.target;
    if (!target?.local) {
      return;
    }
    for (const chain of this.view.unresolved()) {
      if (chain.path.startsWith("/")) {
        continue; // commit message and patchset-level chains have no line in a file
      }
      const line = Math.max(0, (chain.line ?? 1) - 1);
      const first = chain.start_line ? Math.max(0, chain.start_line - 1) : line;
      const thread = this.controller.createCommentThread(
        vscode.Uri.file(path.join(this.model.repo, chain.path)),
        new vscode.Range(first, 0, line, 0),
        chain.comments.map((comment) => ({
          body: new vscode.MarkdownString(comment.message),
          mode: vscode.CommentMode.Preview,
          author: { name: comment.author ?? "you" },
          label: comment.state === "published" ? undefined : comment.state,
        })),
      );
      thread.label = chain.resolved_after_publish ? "Gerrit — your draft resolves this" : "Gerrit — unresolved";
      thread.contextValue = "open";
      thread.canReply = true;
      thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
      this.anchors.set(thread, { rev: target.rev, chain });
    }
  }

  /** Step through the unresolved chains of the shown change, in the Change view's order. */
  async step(direction: 1 | -1): Promise<void> {
    const chains = this.view.unresolved();
    if (!chains.length) {
      void vscode.window.setStatusBarMessage("$(pass) No unresolved comments on this change", 4000);
      return;
    }
    this.cursor = (this.cursor + direction + chains.length) % chains.length;
    const chain = chains[this.cursor];
    this.reveal(chain);
    await this.view.openLocation({ path: chain.path, line: chain.line });
    for (const [thread, anchor] of this.anchors) {
      const here = anchor.chain.root_id === chain.root_id;
      thread.collapsibleState = here ? vscode.CommentThreadCollapsibleState.Expanded : vscode.CommentThreadCollapsibleState.Collapsed;
    }
    void vscode.window.setStatusBarMessage(`Comment ${this.cursor + 1} of ${chains.length}`, 4000);
  }

  /** Save a draft reply from the thread's reply box. */
  async replyInThread(reply: vscode.CommentReply, resolved: boolean | undefined): Promise<void> {
    const anchor = this.anchors.get(reply.thread);
    if (!anchor || !reply.text.trim()) {
      return;
    }
    await this.save(anchor.chain, reply.text, resolved);
  }

  /** Ask for a reply to `chain` (or take `message`) and save it as a draft. */
  async replyToChain(chain: CommentChain, resolved: boolean | undefined, message?: string): Promise<void> {
    const text =
      message ??
      (await vscode.window.showInputBox({
        title: `Reply to ${chain.comments.at(-1)?.author ?? "comment"} — ${chain.path}${chain.line ? `:${chain.line}` : ""}`,
        prompt: "Saved as a draft on Gerrit. Drafts are published with `ger push` or `ger review --vote`.",
        placeHolder: chain.comments.at(-1)?.message.split("\n")[0],
      }));
    if (text?.trim()) {
      await this.save(chain, text, resolved);
    }
  }

  private async save(chain: CommentChain, message: string, resolved: boolean | undefined): Promise<void> {
    const target = this.view.target;
    const anchorId = chain.comments.find((c) => c.comment_id)?.comment_id ?? chain.root_id;
    if (!target) {
      return;
    }
    try {
      const client = await this.model.client();
      await client.reply(target.rev, anchorId, message.trim(), resolved);
      void vscode.window.setStatusBarMessage("$(check) Draft saved. Publish it with ger push.", 6000);
      await this.view.open();
    } catch (error) {
      void vscode.window.showErrorMessage(`Draft not saved: ${describeError(error)}`);
    }
  }

  dispose(): void {
    this.controller.dispose();
  }
}
