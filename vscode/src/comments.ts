// Unresolved comment chains of the shown change as editor comment threads, with draft replies.
// For a local stack commit they sit on your files; for any other change, on the commit's own
// documents — the right-hand side of its diffs.

import * as path from "node:path";
import * as vscode from "vscode";

import { Comment, CommentChain, NewCommentState } from "./api";
import { ChangeView, FILE_SCHEME } from "./changeView";
import { commentLocation } from "./format";
import { Model, describeError } from "./model";
import { ui } from "./ui";

const SAVED: Record<NewCommentState, string> = {
  draft: "Draft saved on Gerrit, visible only to you. Publish it with ger push.",
  "local-draft": "Local draft saved here. It goes to Gerrit once the commit is pushed.",
  note: "Private note saved here. It never leaves this repository.",
};

/** Your unpublished reply in `chain`: a chain holds at most one. */
function yourDraft(chain: CommentChain): Comment | undefined {
  return chain.comments.slice(1).find((comment) => comment.state === "draft");
}

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
    // The "+" in the gutter: on the shown change's own lines, where a line number means the same to Gerrit.
    this.controller.commentingRangeProvider = {
      provideCommentingRanges: (document) =>
        this.pathOf(document.uri) === undefined ? [] : [new vscode.Range(0, 0, Math.max(0, document.lineCount - 1), 0)],
    };
    this.controller.options = {
      prompt: "Comment on the shown change",
      placeHolder: "A draft on Gerrit, a local draft where Gerrit does not have this commit, or a private note",
    };
  }

  /**
   * The repository path of `uri` when it shows the shown change's lines: the right side of its
   * diffs, or your own file when the shown commit is HEAD. Elsewhere a line number would point
   * at other text, so no new comment starts there.
   */
  private pathOf(uri: vscode.Uri): string | undefined {
    const [target, shown] = [this.view.target, this.view.shown];
    const commit = this.view.diff?.commit ?? shown?.sha;
    if (!target || !commit) {
      return undefined;
    }
    if (uri.scheme === FILE_SCHEME) {
      return uri.query === commit ? uri.path.slice(1) : undefined;
    }
    if (uri.scheme === "file" && target.local && this.model.stack?.head === commit) {
      const relative = path.relative(this.model.repo, uri.fsPath);
      return relative && !relative.startsWith("..") && !path.isAbsolute(relative) ? relative.split(path.sep).join("/") : undefined;
    }
    return undefined;
  }

  /** Start a chain from a new thread's comment box: a draft on Gerrit, a local draft, or a private note. */
  async saveNew(reply: vscode.CommentReply, state: NewCommentState): Promise<void> {
    const target = this.view.target;
    const file = this.pathOf(reply.thread.uri);
    const text = reply.text.trim();
    if (!target || file === undefined || !text) {
      return;
    }
    const range = reply.thread.range as vscode.Range | undefined;
    const location = commentLocation(file, range && { start: range.start.line + 1, end: range.end.line + 1 });
    try {
      const client = await this.model.client();
      await client.newComment(target.rev, location, text, state);
      reply.thread.dispose(); // the saved chain comes back with the reload, in its place
      ui().status(`$(check) ${SAVED[state]}`, 6000);
      await this.view.open();
    } catch (error) {
      // The box stays open with the text, to try again or save it another way.
      void ui().message("error", `Comment not saved: ${describeError(error)}`, {}, []);
    }
  }

  private rebuild(): void {
    for (const thread of this.anchors.keys()) {
      thread.dispose();
    }
    this.anchors.clear();
    this.cursor = -1;
    const target = this.view.target;
    if (!target) {
      return;
    }
    for (const chain of this.view.openChains()) {
      if (chain.path.startsWith("/")) {
        continue; // commit message and patchset-level chains have no line in a file
      }
      const line = Math.max(0, (chain.line ?? 1) - 1);
      const first = chain.start_line ? Math.max(0, chain.start_line - 1) : line;
      const thread = this.controller.createCommentThread(
        this.view.locationUri(chain.path),
        new vscode.Range(first, 0, line, 0),
        chain.comments.map((comment) => ({
          body: new vscode.MarkdownString(comment.message),
          mode: vscode.CommentMode.Preview,
          author: { name: comment.author ?? "you" },
          label: comment.state === "published" ? undefined : comment.state,
        })),
      );
      const root = chain.comments[0]?.state;
      thread.label =
        root === "note"
          ? "Your private note"
          : root !== "published"
            ? "Your new comment (not published yet)"
            : chain.resolved_after_publish
              ? "Gerrit — your draft resolves this"
              : "Gerrit — unresolved";
      thread.contextValue = "open";
      thread.canReply = root === "published"; // a reply answers someone on Gerrit
      thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
      this.anchors.set(thread, { rev: target.rev, chain });
    }
  }

  /** Step through the unresolved chains of the shown change, in the Change view's order. */
  async step(direction: 1 | -1): Promise<void> {
    const chains = this.view.unresolved();
    if (!chains.length) {
      ui().status("$(pass) No unresolved comments on this change", 4000);
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
    ui().status(`Comment ${this.cursor + 1} of ${chains.length}`, 4000);
  }

  /** The editor thread that shows `chain`, if the shown change has one. */
  threadOf(chain: CommentChain): vscode.CommentThread | undefined {
    for (const [thread, anchor] of this.anchors) {
      if (anchor.chain.root_id === chain.root_id) {
        return thread;
      }
    }
    return undefined;
  }

  /**
   * Save a draft reply from the thread's reply box. A chain holds one draft of yours: what you
   * type here is added to it, not put in its place.
   */
  async replyInThread(reply: vscode.CommentReply, resolved: boolean | undefined): Promise<void> {
    const anchor = this.anchors.get(reply.thread);
    if (!anchor || !reply.text.trim()) {
      return;
    }
    const draft = yourDraft(anchor.chain);
    await this.save(anchor.chain, draft ? `${draft.message}\n\n${reply.text.trim()}` : reply.text, resolved);
  }

  /**
   * Ask for a reply to `chain` and save it as a draft. With a draft of yours there already, the
   * box opens on its text to edit. `done` resolves the chain: with no draft yet it writes "Done",
   * else it keeps what you wrote.
   */
  async replyToChain(chain: CommentChain, done: boolean): Promise<void> {
    const draft = yourDraft(chain);
    const where = `${chain.path}${chain.line ? `:${chain.line}` : ""}`;
    const text = done
      ? (draft?.message ?? "Done")
      : await ui().input({
          title: draft ? `Edit your draft reply — ${where}` : `Reply to ${chain.comments.at(-1)?.author ?? "comment"} — ${where}`,
          prompt: "Saved as a draft on Gerrit. Drafts are published with `ger push` or `ger review --vote`.",
          placeHolder: chain.comments.at(-1)?.message.split("\n")[0],
          value: draft?.message,
        });
    if (text?.trim()) {
      await this.save(chain, text, done ? true : undefined);
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
      const saved = await client.reply(target.rev, anchorId, message.trim(), resolved);
      const what = saved.reply.action === "updated" ? "Draft updated" : "Draft saved";
      ui().status(`$(check) ${what}${resolved ? ", resolving the comment" : ""}. Publish it with ger push.`, 6000);
      await this.view.open();
    } catch (error) {
      void ui().message("error", `Draft not saved: ${describeError(error)}`, {}, []);
    }
  }

  dispose(): void {
    this.controller.dispose();
  }
}
