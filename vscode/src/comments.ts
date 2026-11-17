// Unresolved comment chains of the shown change as editor comment threads, with draft replies.
// For a local stack commit they sit on your files; for any other change, on the commit's own
// documents — the right-hand side of its diffs. What you wrote and have not published (a draft,
// a local draft, a private note) can be edited and deleted where it stands.

import * as path from "node:path";
import * as vscode from "vscode";

import { Comment, CommentChain, NewCommentState } from "./api";
import { ownComment } from "./changeRows";
import { ChangeView, FILE_SCHEME } from "./changeView";
import { commentLocation } from "./format";
import { Model, describeError } from "./model";
import { ui } from "./ui";

const FILE_PROMPT: Record<NewCommentState, string> = {
  draft: "About the whole file. Saved as a draft on Gerrit, visible only to you until it is published.",
  "local-draft": "About the whole file. Saved here as a local draft: it goes to Gerrit once the commit is pushed.",
  note: "About the whole file. A private note: it never leaves this repository.",
};

const SAVED: Record<NewCommentState, string> = {
  draft: "Draft saved on Gerrit, visible only to you. Publish it with ger push.",
  "local-draft": "Local draft saved here. It goes to Gerrit once the commit is pushed.",
  note: "Private note saved here. It never leaves this repository.",
};

/** What an unpublished comment of yours is called. */
const YOURS: Record<string, string> = { draft: "draft", "local-draft": "local draft", note: "private note" };

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** A comment as its thread shows it. One you wrote and have not published can be edited in place. */
export class ThreadComment implements vscode.Comment {
  body: vscode.MarkdownString | string;
  mode = vscode.CommentMode.Preview;
  readonly author: vscode.CommentAuthorInformation;
  readonly label: string | undefined;
  /** `yours`: the manifest offers Edit and Delete on it. */
  readonly contextValue: string | undefined;
  thread: vscode.CommentThread | undefined;

  constructor(readonly source: Comment) {
    this.body = new vscode.MarkdownString(source.message);
    this.author = { name: source.author ?? "you" };
    this.label = source.state === "published" ? undefined : source.state;
    this.contextValue = source.state !== "published" && source.comment_id ? "yours" : undefined;
  }

  /** What the comment says now: while it is edited, what you typed. */
  get text(): string {
    return typeof this.body === "string" ? this.body : this.body.value;
  }
}

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
  /** Comment boxes "Add Comment" opened that are not saved yet. */
  private readonly boxes = new Set<vscode.CommentThread>();
  /** Comments you are editing: the threads are left alone until you save or cancel. */
  private readonly editing = new Set<ThreadComment>();
  private outdated = false;
  private readonly listeners: vscode.Disposable[] = [];
  private cursor = -1;
  /** Shows the commit you are standing on in the Change view, if the stack has it. Set by the extension. */
  showHead: () => Promise<unknown> = () => Promise.resolve();

  constructor(
    private readonly model: Model,
    private readonly view: ChangeView,
    private readonly reveal: (chain: CommentChain) => void,
  ) {
    view.onDidLoad(() => {
      this.rebuild();
      this.offer();
    });
    this.listeners.push(
      vscode.window.onDidChangeActiveTextEditor(() => this.offer()),
      model.onDidChange(() => this.offer()),
    );
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
    return target.local && this.model.stack?.head === commit ? this.ownFile(uri) : undefined;
  }

  /** The repository path of your own file `uri`; undefined for anything else. */
  private ownFile(uri: vscode.Uri): string | undefined {
    if (uri.scheme !== "file") {
      return undefined;
    }
    const relative = path.relative(this.model.repo, uri.fsPath);
    return relative && !relative.startsWith("..") && !path.isAbsolute(relative) ? relative.split(path.sep).join("/") : undefined;
  }

  /** With no change shown, your own file is commented on as the commit you stand on has it: whether `uri` is such a file. */
  private ofHead(uri: vscode.Uri): boolean {
    const onStack = this.model.stack?.commits.some((commit) => commit.is_head) ?? false;
    return !this.view.target && onStack && this.ownFile(uri) !== undefined;
  }

  /** Whether "Add Comment" has something to comment on in `uri`. */
  private canAdd(uri: vscode.Uri | undefined): boolean {
    return uri !== undefined && (this.pathOf(uri) !== undefined || this.ofHead(uri));
  }

  /** The editor's menu offers "Add Comment" where it works. */
  private offer(): void {
    void vscode.commands.executeCommand("setContext", "ger.canComment", this.canAdd(vscode.window.activeTextEditor?.document.uri));
  }

  /** The comment boxes "Add Comment" opened and nobody saved or closed yet. */
  get openBoxes(): vscode.CommentThread[] {
    return [...this.boxes];
  }

  /**
   * Open a comment box on the selected lines of the active editor (or on `line`, from the line
   * number's menu). In your own file with no change shown, the commit you stand on is shown
   * first: its lines are the ones in the file.
   */
  async add(line?: number): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const uri = editor?.document.uri;
    if (uri && this.ofHead(uri)) {
      await this.showHead();
    }
    if (!editor || !uri || this.pathOf(uri) === undefined) {
      const shown = this.view.shown?.summary ?? this.view.target?.title;
      const which = shown ? `"${shown}", the change shown` : "a change";
      void ui().message("info", `These are not the lines of ${which}. Open one of its files from the Change view (the right-hand side of a diff) and add the comment there.`, {}, []);
      return;
    }
    const { start, end } = editor.selection;
    // A selection that ends at the start of a line does not take that line in.
    const last = end.line > start.line && end.character === 0 ? end.line - 1 : end.line;
    const lines = line === undefined ? new vscode.Range(start.line, 0, last, 0) : new vscode.Range(line, 0, line, 0);
    const box = this.controller.createCommentThread(uri, lines, []);
    box.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.boxes.add(box);
  }

  /** Close a comment box without saving. */
  cancelNew(reply: vscode.CommentReply): void {
    this.boxes.delete(reply.thread);
    reply.thread.dispose();
  }

  /** Ask for a comment on the whole file `file` of the shown change and save it: a draft where Gerrit has the commit, else a local draft. */
  async commentOnFile(file: string): Promise<void> {
    const target = this.view.target;
    if (!target) {
      return;
    }
    const state: NewCommentState = this.view.shown?.new_comment ?? "local-draft";
    const text = await ui().input({ title: `Comment on ${file}`, prompt: FILE_PROMPT[state] });
    if (!text?.trim()) {
      return;
    }
    try {
      await (await this.model.client()).newComment(target.rev, commentLocation(file), text.trim(), state);
      ui().status(`$(check) ${SAVED[state]}`, 6000);
      await this.view.open();
    } catch (error) {
      void ui().message("error", `Comment not saved: ${describeError(error)}`, {}, []);
    }
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
      this.boxes.delete(reply.thread);
      reply.thread.dispose(); // the saved chain comes back with the reload, in its place
      ui().status(`$(check) ${SAVED[state]}`, 6000);
      await this.view.open();
    } catch (error) {
      // The box stays open with the text, to try again or save it another way.
      void ui().message("error", `Comment not saved: ${describeError(error)}`, {}, []);
    }
  }

  private rebuild(): void {
    if (this.editing.size) {
      this.outdated = true; // what you are typing stays; the threads catch up once you save or cancel
      return;
    }
    this.outdated = false;
    for (const thread of this.anchors.keys()) {
      thread.dispose();
    }
    this.anchors.clear();
    this.cursor = -1;
    const target = this.view.target;
    for (const box of this.boxes) {
      // A box on lines the shown change no longer has (another change is shown) has nowhere to save to.
      if (this.pathOf(box.uri) === undefined) {
        box.dispose();
        this.boxes.delete(box);
      }
    }
    if (!target) {
      return;
    }
    for (const chain of this.view.openChains()) {
      if (chain.path.startsWith("/")) {
        continue; // commit message and patchset-level chains have no line in a file
      }
      const line = Math.max(0, (chain.line ?? 1) - 1);
      const first = chain.start_line ? Math.max(0, chain.start_line - 1) : line;
      const comments = chain.comments.map((comment) => new ThreadComment(comment));
      const thread = this.controller.createCommentThread(this.view.locationUri(chain.path), new vscode.Range(first, 0, line, 0), comments);
      comments.forEach((comment) => (comment.thread = thread));
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

  // --- Your own unpublished comments --------------------------------------------------

  /** Redraw `comment`'s thread: a comment's mode and body only show once the list is set again. */
  private redraw(comment: ThreadComment): void {
    if (comment.thread) {
      comment.thread.comments = [...comment.thread.comments];
    }
  }

  /** Turn a draft, local draft or note of yours into a text box, in its thread. */
  startEdit(comment: ThreadComment): void {
    if (!comment.contextValue || !comment.thread) {
      return;
    }
    comment.mode = vscode.CommentMode.Editing;
    comment.thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.editing.add(comment);
    this.redraw(comment);
  }

  /** Leave the text box: the comment says again what is saved. */
  cancelEdit(comment: ThreadComment): void {
    comment.body = new vscode.MarkdownString(comment.source.message);
    comment.mode = vscode.CommentMode.Preview;
    this.editing.delete(comment);
    this.redraw(comment);
    if (this.outdated && !this.editing.size) {
      this.rebuild();
    }
  }

  /** Save what the text box says. Empty or unchanged text leaves the comment as it was. */
  async saveEdit(comment: ThreadComment): Promise<void> {
    const text = comment.text.trim();
    const rev = (comment.thread && this.anchors.get(comment.thread)?.rev) ?? this.view.target?.rev;
    if (!text || text === comment.source.message.trim() || !rev) {
      this.cancelEdit(comment);
      return;
    }
    // Not saved: the box stays open with the text, to try again.
    await this.edit(rev, comment.source, text, () => {
      this.editing.delete(comment);
      comment.mode = vscode.CommentMode.Preview;
      this.redraw(comment);
    });
  }

  /** Write `text` as what `own` says; `saved` runs before the views reload. */
  private async edit(rev: string, own: Comment, text: string, saved: () => void = () => undefined): Promise<boolean> {
    try {
      await (await this.model.client()).editComment(rev, own.comment_id ?? "", text);
      saved();
      ui().status(`$(check) ${capital(YOURS[own.state] ?? "comment")} updated.`, 6000);
      await this.view.open();
      return true;
    } catch (error) {
      void ui().message("error", `Comment not saved: ${describeError(error)}`, {}, []);
      return false;
    }
  }

  /** Delete a draft, local draft or note of yours, after asking: it cannot be taken back. */
  async discard(own: Comment, rev: string | undefined = this.view.target?.rev, reply = false): Promise<void> {
    const what = reply ? "draft reply" : YOURS[own.state];
    if (!what || !own.comment_id || !rev) {
      return;
    }
    const where = `${own.path}${own.line ? `:${own.line}` : ""}`;
    const sure = await ui().message("warning", `Delete your ${what} on ${where}?`, { modal: true, detail: own.message }, ["Delete"]);
    if (sure !== "Delete") {
      return;
    }
    try {
      await (await this.model.client()).discardComment(rev, own.comment_id);
      for (const comment of this.editing) {
        if (comment.source.comment_id === own.comment_id) {
          this.editing.delete(comment);
        }
      }
      ui().status(`$(check) ${capital(what)} deleted.`, 6000);
      await this.view.open();
    } catch (error) {
      void ui().message("error", `Comment not deleted: ${describeError(error)}`, {}, []);
    }
  }

  /** Delete the comment a thread shows, from its title bar. */
  discardInThread(comment: ThreadComment): Promise<void> {
    const thread = comment.thread;
    return this.discard(comment.source, thread && this.anchors.get(thread)?.rev, thread !== undefined && thread.comments[0] !== comment);
  }

  /** The comment of yours that `chain`'s thread in the editor shows, to edit there. */
  ownInThread(chain: CommentChain): ThreadComment | undefined {
    const own = ownComment(chain);
    const shown = this.threadOf(chain)?.comments.find((comment) => comment instanceof ThreadComment && comment.source.comment_id === own?.comment_id);
    return own && shown instanceof ThreadComment ? shown : undefined;
  }

  /**
   * Edit the chain you started (a draft, a local draft or a note), from the Change view: in its
   * thread in the editor, where there is room to write; a chain with no line in a file gets an
   * input box.
   */
  async editChain(chain: CommentChain): Promise<void> {
    const own = ownComment(chain);
    const rev = this.view.target?.rev;
    if (!own || !rev) {
      return;
    }
    const shown = this.ownInThread(chain);
    if (shown) {
      await this.view.openLocation({ path: chain.path, line: chain.line });
      this.startEdit(shown);
      return;
    }
    const text = await ui().input({ title: `Edit your ${YOURS[own.state] ?? "comment"} — ${chain.path}`, value: own.message });
    if (text?.trim() && text.trim() !== own.message.trim()) {
      await this.edit(rev, own, text.trim());
    }
  }

  /** Delete your unpublished comment in `chain`: the chain itself when you started it, else your draft reply. */
  async discardChain(chain: CommentChain): Promise<void> {
    const own = ownComment(chain);
    if (own) {
      await this.discard(own, undefined, own !== chain.comments[0]);
    }
  }

  dispose(): void {
    this.listeners.forEach((listener) => listener.dispose());
    this.controller.dispose();
  }
}
