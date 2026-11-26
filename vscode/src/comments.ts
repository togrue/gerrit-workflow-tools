// Unresolved comment chains of the shown change as editor comment threads, with draft replies.
// For a local stack commit they sit on your files; for any other change, on the commit's own
// documents — the right-hand side of its diffs. What you wrote and have not published (a draft,
// a local draft, a private note) can be edited and deleted where it stands. A suggested edit shows
// as old → new; on your own commit Apply writes it into your file. Suggest Edit writes one.

import * as path from "node:path";
import * as vscode from "vscode";

import { Comment, CommentChain, NewCommentState, ShownCommit } from "./api";
import { NOT_FILES, answeredByDraft, ownComment, placeWords, replyName } from "./changeRows";
import { ChangeView } from "./changeView";
import { FILE_SCHEME, filePath, repoPath } from "./diffTabs";
import { commentLocation } from "./format";
import { Model, describeError } from "./model";
import { APPLY_WORDS, Place, applyPlan, latestSuggestion, suggestionMarkdown } from "./suggestions";
import { offerWaysOn, ui } from "./ui";
import { failureWays } from "./problemRows";

const FILE_PROMPT: Record<NewCommentState, string> = {
  draft: "About the whole file. Saved as a draft on Gerrit, visible only to you until it is published.",
  "local-draft": "About the whole file. Saved here as a local draft: it goes to Gerrit once the commit is pushed.",
  note: "About the whole file. A private note: it never leaves this repository.",
};

const SAVED: Record<Exclude<NewCommentState, "draft">, string> = {
  "local-draft": "Local draft saved here. It goes to Gerrit once the commit is pushed.",
  note: "Private note saved here. It never leaves this repository.",
};

/**
 * What publishes a draft on the change shown, as core says (`drafts_published_by`), and the command
 * that publishes it now: on your own commit of the local stack your next push, anywhere else a vote
 * (`ger push` only publishes the drafts on the changes it sends).
 */
function publishing(shown: ShownCommit | undefined): { words: string; button: string; command: string } {
  return shown?.drafts_published_by === "push"
    ? { words: "your next push publishes it", button: "Publish Drafts…", command: "ger.publishDrafts" }
    : { words: "Vote and Publish sends it", button: "Vote and Publish…", command: "ger.vote" };
}

/**
 * What the comment boxes say: VS Code has one prompt and placeholder per controller, so they are set
 * for the box about to be used. A reply names who it answers; a new comment says where it goes.
 */
const BOX = {
  reply: (to: string | undefined): vscode.CommentOptions => ({
    prompt: to ? `Reply to ${to}…` : "Reply…",
    placeHolder: `${to ? `Reply to ${to}` : "Reply"}… saved as your draft, visible only to you until it is published`,
  }),
  new: { prompt: "Comment on the shown change", placeHolder: "A draft on Gerrit, a local draft where Gerrit does not have this commit, or a private note" } as vscode.CommentOptions,
};

/** What an unpublished comment of yours is called. */
const YOURS: Record<string, string> = { draft: "draft", "local-draft": "local draft", note: "private note" };

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** A comment as a thread shows it: an edit it suggests as old → new, under *Suggested edit*. */
function shownBody(source: Comment): vscode.MarkdownString {
  return new vscode.MarkdownString(suggestionMarkdown(source, (replacement) => replacement.original));
}

/** A comment as its thread shows it. One you wrote and have not published can be edited in place. */
export class ThreadComment implements vscode.Comment {
  body: vscode.MarkdownString | string;
  mode = vscode.CommentMode.Preview;
  readonly author: vscode.CommentAuthorInformation;
  readonly label: string | undefined;
  /** `yours`: the manifest offers Edit and Delete on it; `suggestion`: Apply, the edit it suggests written into your file. */
  readonly contextValue: string | undefined;
  thread: vscode.CommentThread | undefined;

  constructor(
    readonly source: Comment,
    applies = false,
  ) {
    this.body = shownBody(source);
    this.author = { name: source.author ?? "you" };
    this.label = source.state === "published" ? undefined : source.state;
    this.contextValue = source.state !== "published" && source.comment_id ? "yours" : applies ? "suggestion" : undefined;
  }

  /** What the comment says now: while it is edited, what you typed. */
  get text(): string {
    return typeof this.body === "string" ? this.body : this.body.value;
  }
}

/**
 * Esc in a thread's reply box: the box gives up the keyboard to the thread's comments, so a
 * second Esc closes it (`closeComment`). VS Code has no API to focus a comment; its widget
 * navigation does exactly this step. Where it is missing, Esc closes the thread at once.
 */
export async function leaveCommentBox(): Promise<void> {
  try {
    await vscode.commands.executeCommand("widgetNavigation.focusPrevious");
  } catch {
    await closeComment();
  }
}

/** Esc on a comment, or in the editor while a thread is open there: close the open threads in view, as VS Code's Hide Comment does. */
export async function closeComment(): Promise<void> {
  await vscode.commands.executeCommand("workbench.action.hideComment");
}

/**
 * Set a thread's Resolved toggle (in its header) and its label to match: `resolved` or
 * `unresolved`, whether the reply saved next resolves the chain.
 */
function mark(thread: vscode.CommentThread, chain: CommentChain, resolved: boolean, outdated = false, shownPatchset?: number): void {
  thread.contextValue = resolved ? "resolved" : "unresolved";
  const label = !resolved ? "Gerrit — unresolved" : chain.resolved_after_publish ? "Gerrit — your draft resolves this" : "Gerrit — your reply will resolve this";
  thread.label = `${label}${placeWords(chain, outdated, shownPatchset)}`;
}


/** Your unpublished reply in `chain`: a chain holds at most one. */
function yourDraft(chain: CommentChain): Comment | undefined {
  return chain.comments.slice(1).find((comment) => comment.state === "draft");
}

/** Which change a walk position belongs to: its Change-Id, or its commit before the first push. */
function changeKey(target: { changeId?: string | null; rev: string } | undefined): string {
  return target?.changeId ?? target?.rev ?? "";
}

/**
 * The index in `ids` (the walk now) one step in `direction` from `position` (the chain shown last,
 * in the walk then); may be out of range, the caller wraps it. Still in the walk, the chain next to
 * it; gone from it (answered), the first of those after it (before it, stepping back) that is still
 * there. Nowhere yet, the first (the last).
 */
function stepFrom(position: { id: string; order: string[] } | undefined, ids: string[], direction: 1 | -1): number {
  if (!position) {
    return direction === 1 ? 0 : -1;
  }
  const at = ids.indexOf(position.id);
  if (at >= 0) {
    return at + direction;
  }
  const from = position.order.indexOf(position.id);
  const passed = direction === 1 ? position.order.slice(from + 1) : position.order.slice(0, Math.max(0, from)).reverse();
  const next = passed.map((id) => ids.indexOf(id)).find((index) => index >= 0);
  return next ?? (direction === 1 ? ids.length : -1);
}

interface Anchor {
  rev: string;
  chain: CommentChain;
  /** Drawn where its lines were: they changed in your file since the version it was written on. */
  outdated: boolean;
}

export class CommentThreads implements vscode.Disposable {
  private readonly controller = vscode.comments.createCommentController("ger", "Gerrit comments");
  private readonly anchors = new Map<vscode.CommentThread, Anchor>();
  /** Comment boxes "Add Comment" opened that are not saved yet. */
  private readonly boxes = new Set<vscode.CommentThread>();
  /** Comments you are editing: the threads are left alone until you save or cancel. */
  private readonly editing = new Set<ThreadComment>();
  /** The changes a notification already said, this session, how a draft on them is published. */
  private readonly toldAbout = new Set<string>();
  private outdated = false;
  private readonly listeners: vscode.Disposable[] = [];
  /**
   * Where Next / Previous Unresolved Comment stands: the chain it showed last, by identity, and the
   * walk's order then. A reload (a reply saved) keeps it; a chain that left the walk (your draft
   * answers it now) is stepped on from where it was. Another change starts the walk afresh.
   */
  private position: { change: string; id: string; order: string[] } | undefined;
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
      // The reply box names whom it answers: the thread the cursor is in, wherever it was opened from.
      vscode.window.onDidChangeTextEditorSelection((event) => this.nameReplies(this.threadAt(event.textEditor))),
    );
    // The "+" in the gutter: on the shown change's own lines, where a line number means the same to Gerrit.
    this.controller.commentingRangeProvider = {
      provideCommentingRanges: (document) =>
        this.pathOf(document.uri) === undefined ? [] : [new vscode.Range(0, 0, Math.max(0, document.lineCount - 1), 0)],
    };
    this.controller.options = BOX.reply(undefined);
  }

  /**
   * The repository path of `uri` when it shows the shown change's lines: the right side of its
   * diffs, or your own file when the working files hold the shown commit (`worktreeHolds`: the
   * commit you stand on, or a staged review's change). Elsewhere a line number would point at
   * other text, so no new comment starts there.
   */
  private pathOf(uri: vscode.Uri): string | undefined {
    const [target, shown] = [this.view.target, this.view.shown];
    const commit = this.view.diff?.commit ?? shown?.sha;
    if (!target || !commit) {
      return undefined;
    }
    if (uri.scheme === FILE_SCHEME) {
      return uri.query === commit ? filePath(uri, this.model.repo) : undefined;
    }
    return this.view.worktreeHolds(commit) ? repoPath(this.model.repo, uri) : undefined;
  }

  /** With no change shown, your own file is commented on as the commit you stand on has it: whether `uri` is such a file. */
  private ofHead(uri: vscode.Uri): boolean {
    const onStack = this.model.stack?.commits.some((commit) => commit.is_head) ?? false;
    return !this.view.target && onStack && repoPath(this.model.repo, uri) !== undefined;
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
    this.controller.options = BOX.new;
    const box = this.controller.createCommentThread(uri, lines, []);
    box.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.boxes.add(box);
  }

  /** Close a comment box without saving. */
  cancelNew(reply: vscode.CommentReply): void {
    this.boxes.delete(reply.thread);
    reply.thread.dispose();
  }

  /** Ask for a comment on the whole file `file` of the version looked at and save it: a draft where Gerrit has that commit, else a local draft. */
  async commentOnFile(file: string): Promise<void> {
    const target = this.view.target;
    if (!target) {
      return;
    }
    const state: NewCommentState = this.view.newComment ?? "local-draft";
    const text = await ui().input({ title: `Comment on ${file}`, prompt: FILE_PROMPT[state] });
    if (!text?.trim()) {
      return;
    }
    try {
      await (await this.model.client()).newComment(target.rev, commentLocation(file), text.trim(), state, this.view.diff?.commit);
      this.saved(state, "Draft saved on Gerrit");
      await this.view.open();
    } catch (error) {
      void offerWaysOn("error", `Comment not saved: ${describeError(error)}`, failureWays(error));
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
      // The lines are those of the version looked at (`pathOf`), which the change alone does not name: an older patchset.
      await client.newComment(target.rev, location, text, state, this.view.diff?.commit);
      this.boxes.delete(reply.thread);
      reply.thread.dispose(); // the saved chain comes back with the reload, in its place
      this.saved(state, "Draft saved on Gerrit");
      await this.view.open();
    } catch (error) {
      // The box stays open with the text, to try again or save it another way.
      void offerWaysOn("error", `Comment not saved: ${describeError(error)}`, failureWays(error));
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
    const target = this.view.target;
    if (this.position && this.position.change !== changeKey(target)) {
      this.position = undefined;
    }
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
      const place = this.view.placeOf(chain);
      const line = Math.max(0, (place.line ?? 1) - 1);
      const first = place.start ? Math.max(0, place.start - 1) : line;
      // Apply is offered on the newest comment that suggests an edit, on your own commit's threads.
      const suggestion = this.applies() ? latestSuggestion(chain.comments)?.comment : undefined;
      const comments = chain.comments.map((comment) => new ThreadComment(comment, comment === suggestion));
      const thread = this.controller.createCommentThread(this.view.locationUri(chain.path), new vscode.Range(first, 0, line, 0), comments);
      comments.forEach((comment) => (comment.thread = thread));
      const root = chain.comments[0]?.state;
      if (root === "published") {
        mark(thread, chain, chain.resolved_after_publish, place.outdated, this.view.shownPatchset());
      } else {
        thread.label = root === "note" ? "Your private note" : "Your new comment (not published yet)";
        thread.contextValue = "yours";
      }
      thread.canReply = root === "published"; // a reply answers someone on Gerrit
      thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
      this.anchors.set(thread, { rev: target.rev, chain, outdated: place.outdated });
    }
    this.nameReplies(); // a name from the change shown before must not stay
  }

  /** The chain of the thread whose lines hold `editor`'s cursor; none when it is in no thread. */
  private threadAt(editor: vscode.TextEditor): CommentChain | undefined {
    const [uri, line] = [editor.document.uri.toString(), editor.selection.active.line];
    for (const [thread, anchor] of this.anchors) {
      if (thread.uri.toString() === uri && thread.range && line >= thread.range.start.line && line <= thread.range.end.line) {
        return anchor.chain;
      }
    }
    return undefined;
  }

  /**
   * Say in the reply box whom it answers (`replyName`): the chain `at`, else the one person every
   * thread shown answers, else nobody. While a new comment's box is open, its own text stays.
   */
  private nameReplies(at?: CommentChain): void {
    if (this.boxes.size) {
      return;
    }
    const options = BOX.reply(replyName([...this.anchors.values()].map((anchor) => anchor.chain), at));
    if (options.prompt !== this.controller.options?.prompt) {
      this.controller.options = options;
    }
  }

  /**
   * Step through the shown change's comments. On your own change, what is left for you to answer
   * (`toAnswer`), in the Change view's order: a chain your draft answers is not walked again, and
   * the count is of what is left. On a change you review (not yours), every open chain, your own
   * drafts, local drafts and notes too: what you are about to publish is read again the same way.
   * A chain about the whole change (no line in a file) is shown in the Change view, its reply box
   * focused.
   */
  async step(direction: 1 | -1): Promise<void> {
    const reviewing = this.view.shown?.own === false;
    const chains = reviewing ? this.view.openChains() : this.view.toAnswer();
    if (!chains.length) {
      this.position = undefined;
      const answered = reviewing ? 0 : this.view.chains().filter((chain) => !chain.resolved && answeredByDraft(chain)).length;
      const drafts = answered ? ` · ${answered} answered by your drafts (${publishing(this.view.shown).words.replace(/ it$/, " them")})` : "";
      ui().status(`$(pass) ${reviewing ? "No unresolved comments or drafts of yours" : "No comments left to answer"} on this change${drafts}`, 4000);
      return;
    }
    const ids = chains.map((chain) => chain.root_id);
    const index = (stepFrom(this.position, ids, direction) + ids.length) % ids.length;
    const chain = chains[index];
    this.position = { change: changeKey(this.view.target), id: chain.root_id, order: ids };
    await this.goTo(chain);
    ui().status(`Comment ${index + 1} of ${chains.length}${reviewing ? "" : " to answer"}`, 4000);
  }

  /**
   * Next comment, as the Change view's Next does it: on from where the walk stands to the first
   * chain of the walk not gone to yet on this change; with none left, the next one, as Alt+F9.
   */
  async stepUnread(): Promise<void> {
    // What Next counts (nextAction.ts): left to answer, not what waits on a reply to you.
    const chains = this.view.toAnswer();
    const visited = this.visitedHere();
    const ids = chains.map((chain) => chain.root_id);
    const from = Math.max(0, stepFrom(this.position, ids, 1));
    const order = [...ids.slice(from), ...ids.slice(0, from)];
    const unread = order.find((id) => !visited.has(id));
    if (!unread) {
      return this.step(1);
    }
    const chain = chains[ids.indexOf(unread)];
    this.position = { change: changeKey(this.view.target), id: chain.root_id, order: ids };
    await this.goTo(chain);
    ui().status(`Comment ${ids.indexOf(unread) + 1} of ${chains.length}`, 4000);
  }

  /**
   * Go to `chain` where it is answered: its row marked in the Change view, and the file opened at
   * its thread (`show`); one about the whole change (no line in a file) is shown in the Change view.
   * Nothing is asked of ger serve: the chains are those the Change view has loaded.
   */
  async goTo(chain: CommentChain): Promise<void> {
    this.reveal(chain);
    if (NOT_FILES.includes(chain.path)) {
      this.seen(chain);
      await this.view.focusThread(chain);
    } else {
      await this.show(chain);
    }
  }

  /**
   * Open the file at `chain`'s line with its thread expanded there, the other threads closed, and
   * what it was aimed at highlighted until you move on. A chain the editor shows no thread for (a
   * resolved one) just opens its line, highlighted the same.
   */
  async show(chain: CommentChain): Promise<void> {
    // The reply box of the thread it opens answers the last of them who spoke.
    this.nameReplies(chain);
    this.seen(chain);
    const editor = await this.view.openLocation({ path: chain.path, line: this.view.placeOf(chain).line });
    for (const [thread, anchor] of this.anchors) {
      const here = anchor.chain.root_id === chain.root_id;
      thread.collapsibleState = here ? vscode.CommentThreadCollapsibleState.Expanded : vscode.CommentThreadCollapsibleState.Collapsed;
    }
    this.showResolved(chain);
    if (editor) {
      this.aim(editor, chain);
    }
  }

  /** The resolved chain gone to, drawn while it is the one gone to: the editor draws no thread for resolved chains otherwise (S4). */
  private resolvedThread: vscode.CommentThread | undefined;

  /** Draw `chain`'s thread at its line when it is resolved (read only: there is nothing to answer), and no other resolved one. */
  private showResolved(chain: CommentChain): void {
    this.resolvedThread?.dispose();
    this.resolvedThread = undefined;
    if (!chain.resolved || NOT_FILES.includes(chain.path) || chain.path.startsWith("/")) {
      return;
    }
    const place = this.view.placeOf(chain);
    const line = Math.max(0, (place.line ?? 1) - 1);
    const first = place.start ? Math.max(0, place.start - 1) : line;
    const thread = this.controller.createCommentThread(this.view.locationUri(chain.path), new vscode.Range(first, 0, line, 0), chain.comments.map((comment) => new ThreadComment(comment)));
    thread.label = `Gerrit — resolved${placeWords(chain, place.outdated, this.view.shownPatchset())}`;
    thread.contextValue = "resolved-shown";
    thread.canReply = false;
    thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.resolvedThread = thread;
  }

  /** The chains gone to on each change this session (by Change-Id): what Next has shown already. */
  private readonly visited = new Map<string, Set<string>>();

  private seen(chain: CommentChain): void {
    const key = changeKey(this.view.target);
    this.visited.set(key, (this.visited.get(key) ?? new Set()).add(chain.root_id));
    this.view.render(); // Next moves on from it
  }

  /** The ids of the chains of the change shown that were gone to already. */
  visitedHere(): ReadonlySet<string> {
    return this.visited.get(changeKey(this.view.target)) ?? new Set();
  }

  /** What a comment was aimed at, as the editor shows it: a soft highlight, gone with the next move. */
  private readonly aimed = vscode.window.createTextEditorDecorationType({
    // The colour of a search match: seen at a glance on every theme, and known to mean "here".
    backgroundColor: new vscode.ThemeColor("editor.findMatchHighlightBackground"),
    borderColor: new vscode.ThemeColor("editor.findMatchHighlightBorder"),
    borderStyle: "solid",
    borderWidth: "1px",
    borderRadius: "2px",
    overviewRulerColor: new vscode.ThemeColor("editorOverviewRuler.findMatchForeground"),
  });
  private aiming: vscode.Disposable | undefined;

  /**
   * Highlight what `chain` was aimed at in `editor`: the characters of its range (Gerrit's `range`)
   * where its lines are where Gerrit has them, else its lines as they are drawn. Cleared once the
   * cursor moves or another editor comes up; the move that opened it does not count.
   */
  private aim(editor: vscode.TextEditor, chain: CommentChain): void {
    const place = this.view.placeOf(chain);
    const last = Math.min(Math.max(0, (place.line ?? 1) - 1), editor.document.lineCount - 1);
    const first = Math.min(place.start ? Math.max(0, place.start - 1) : last, last);
    const range = chain.range;
    const there = range && range.start_line - 1 === first && range.end_line - 1 === last;
    const aimed = there
      ? new vscode.Range(range.start_line - 1, range.start_character, range.end_line - 1, range.end_character)
      : new vscode.Range(first, 0, last, editor.document.lineAt(last).text.length);
    this.aiming?.dispose();
    editor.setDecorations(this.aimed, [aimed]);
    editor.revealRange(aimed, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    const since = Date.now();
    const clear = () => {
      editor.setDecorations(this.aimed, []);
      this.aiming?.dispose();
      this.aiming = undefined;
    };
    this.aiming = vscode.Disposable.from(
      vscode.window.onDidChangeTextEditorSelection((event) => (Date.now() - since > 500 || event.textEditor !== editor ? clear() : undefined)),
      vscode.window.onDidChangeActiveTextEditor((active) => (active !== editor ? clear() : undefined)),
    );
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
   * Save a draft reply from the thread's reply box, resolving the chain or not as the Resolved
   * toggle in the thread's header says. A chain holds one draft of yours: what you type here is
   * added to it, not put in its place.
   */
  async replyInThread(reply: vscode.CommentReply): Promise<void> {
    const anchor = this.anchors.get(reply.thread);
    if (anchor) {
      await this.replyWith(anchor.chain, reply.text, reply.thread.contextValue === "resolved");
    }
  }

  /** Done in a thread's reply box: what you typed (else "Done", or your draft as it is) saved as your draft, resolving the chain. */
  async doneInThread(reply: vscode.CommentReply): Promise<void> {
    const anchor = this.anchors.get(reply.thread);
    if (!anchor) {
      return;
    }
    await (reply.text.trim() ? this.replyWith(anchor.chain, reply.text, true) : this.replyToChain(anchor.chain, true));
  }

  /** Cancel in a thread's reply box: VS Code has emptied the box; the thread closes and its toggle goes back. */
  cancelReply(reply: vscode.CommentReply): void {
    const anchor = this.anchors.get(reply.thread);
    if (anchor) {
      mark(reply.thread, anchor.chain, anchor.chain.resolved_after_publish, anchor.outdated, this.view.shownPatchset());
    }
    reply.thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
  }

  /**
   * The Resolved toggle in a thread's header, Gerrit's checkbox. It sits in the header, not by
   * Save, because VS Code empties the reply box on any button there. The reply saved next resolves
   * the chain or leaves it open; your draft there, if you have one, changes at once.
   */
  async setResolved(thread: vscode.CommentThread, resolved: boolean): Promise<void> {
    const anchor = this.anchors.get(thread);
    if (!anchor) {
      return;
    }
    mark(thread, anchor.chain, resolved, anchor.outdated, this.view.shownPatchset());
    if (yourDraft(anchor.chain)) {
      await this.replyWith(anchor.chain, "", resolved);
    }
  }

  /**
   * Save `text` as your draft reply in `chain`, added to the draft you have there: from a thread's
   * reply box, or the Change view's for a chain with no line in a file. With nothing typed, your
   * draft is saved again only to change whether it resolves the chain.
   */
  async replyWith(chain: CommentChain, text: string, resolved: boolean | undefined): Promise<boolean> {
    const [draft, typed] = [yourDraft(chain), text.trim()];
    if (!typed && !(draft && resolved !== undefined)) {
      return false;
    }
    return this.save(chain, draft ? [draft.message, typed].filter(Boolean).join("\n\n") : typed, resolved);
  }

  /**
   * Ask for a reply to `chain` and save it as a draft. With a draft of yours there already, the
   * box opens on its text to edit. `done` resolves the chain: with no draft yet it writes "Done",
   * else it keeps what you wrote.
   */
  /**
   * Reply as Draft… (or Done): the reply box has Gerrit's Resolved choice as the Change view's
   * box does, starting where the comment stands; Enter saves with it, the toggle in the box's
   * title bar flips it, Ctrl+Shift+Enter saves and resolves.
   */
  async replyToChain(chain: CommentChain, done: boolean): Promise<void> {
    const draft = yourDraft(chain);
    const where = `${chain.path}${chain.line ? `:${chain.line}` : ""}`;
    if (done) {
      await this.save(chain, draft?.message ?? "Done", true);
      return;
    }
    const words = publishing(this.view.shown).words;
    const reply = await ui().reply({
      title: draft ? `Edit your draft reply — ${where}` : `Reply to ${chain.comments.at(-1)?.author ?? "comment"} — ${where}`,
      prompt: (resolved) => `Saved as a draft${resolved ? " that resolves the comment" : ", the comment left unresolved"}; visible only to you: ${words}.`,
      placeHolder: chain.comments.at(-1)?.message.split("\n")[0],
      value: draft?.message,
      resolved: chain.resolved_after_publish,
    });
    if (reply?.text.trim()) {
      await this.save(chain, reply.text, reply.resolved);
    }
  }

  private async save(chain: CommentChain, message: string, resolved: boolean | undefined): Promise<boolean> {
    const target = this.view.target;
    const anchorId = chain.comments.find((c) => c.comment_id)?.comment_id ?? chain.root_id;
    if (!target) {
      return false;
    }
    try {
      const client = await this.model.client();
      const saved = await client.reply(target.rev, anchorId, message.trim(), resolved);
      const what = saved.reply.action === "updated" ? "Draft updated" : "Draft saved";
      const how = resolved ? ", resolving the comment" : resolved === false && chain.resolved_after_publish ? ", leaving the comment unresolved" : "";
      this.draftSaved(`${what}${how}`);
      await this.view.open();
      return true;
    } catch (error) {
      void offerWaysOn("error", `Draft not saved: ${describeError(error)}`, failureWays(error));
      return false;
    }
  }

  /** Say that a comment was saved: a draft on Gerrit says what publishes it, a local one where it stays. */
  private saved(state: NewCommentState, draft: string): void {
    if (state === "draft") {
      this.draftSaved(draft);
    } else {
      ui().status(`$(check) ${SAVED[state]}`, 6000);
    }
  }

  /**
   * A draft on Gerrit was saved: only you see it until it is published, and the way to publish it
   * is one click away. The first draft on a change says so in a notification with that button; the
   * ones after it in the status bar, so a reviewer writing ten drafts is not told ten times.
   */
  private draftSaved(what: string): void {
    const how = publishing(this.view.shown);
    const text = `${what}, visible only to you: ${how.words}.`;
    const change = this.view.target?.changeId ?? this.view.target?.rev ?? "";
    if (this.toldAbout.has(change)) {
      ui().status(`$(check) ${text}`, 6000);
      return;
    }
    this.toldAbout.add(change);
    void Promise.resolve(ui().message("info", text, {}, [how.button])).then((picked) => (picked ? vscode.commands.executeCommand(how.command) : undefined));
  }

  // --- Suggested edits ------------------------------------------------------------------

  /** Whether an edit a comment suggests may be written into your file: the shown change is your own local commit, not a review. */
  private applies(): boolean {
    return Boolean(this.view.target?.local) && this.view.shown?.own !== false && !this.model.stack?.staged_review;
  }

  /** Apply on a thread's comment (its title bar). */
  async applyInThread(comment: ThreadComment): Promise<void> {
    const anchor = comment.thread && this.anchors.get(comment.thread);
    if (anchor) {
      await this.applySuggestion(anchor.chain);
    }
  }

  /**
   * Write the edit `chain`'s newest suggesting comment suggests into your file, where its lines are
   * now (core's `worktree`), once they still say what it replaces; else say why not. Nothing is saved
   * or committed: the edit is in the editor, to undo or amend in. Then Reply Done is offered.
   */
  async applySuggestion(chain: CommentChain): Promise<void> {
    const found = latestSuggestion(chain.comments);
    if (!found || !this.applies()) {
      return;
    }
    const files = new Map<string, vscode.TextDocument>();
    try {
      for (const rep of found.edit.replacements) {
        files.set(rep.path, files.get(rep.path) ?? (await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(this.model.repo, rep.path)))));
      }
    } catch (error) {
      void ui().message("warning", `The suggested edit was not applied: ${describeError(error)}`, {}, []);
      return;
    }
    const range = (place: Place) => new vscode.Range(place.start.line, place.start.character, place.end.line, place.end.character);
    const plan = applyPlan(found.edit, (place) => files.get(place.path)?.getText(range(place)));
    if ("refused" in plan) {
      void ui().message("warning", plan.refused, {}, []);
      return;
    }
    const edit = new vscode.WorkspaceEdit();
    plan.places.forEach((place) => edit.replace(files.get(place.path)!.uri, range(place), place.replacement));
    if (!(await vscode.workspace.applyEdit(edit))) {
      void ui().message("warning", "The suggested edit was not applied: VS Code refused the edit.", {}, []);
      return;
    }
    const [first] = plan.places;
    const where = `${first.path}:${first.start.line + 1}`;
    await vscode.window.showTextDocument(files.get(first.path)!, { selection: new vscode.Range(first.start.line, 0, first.start.line, 0), preview: true });
    const done = "Reply Done";
    if ((await ui().message("info", APPLY_WORDS.applied(where), {}, [done])) === done) {
      await this.replyToChain(chain, true);
    }
  }

  /** Suggestion boxes Suggest Edit opened, not saved yet: the comment being written, by its thread. */
  private readonly suggesting = new Map<ThreadComment, vscode.CommentThread>();

  /**
   * Suggest Edit: a comment box on the selected lines (the cursor's line without a selection),
   * holding them in a ```` ```suggestion ```` block to change into what they should say, as Gerrit's
   * own "Suggest edit" writes it. Saved as a new comment, a draft where Gerrit has the commit.
   */
  async suggest(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const uri = editor?.document.uri;
    if (uri && this.ofHead(uri)) {
      await this.showHead();
    }
    const file = uri && this.pathOf(uri);
    if (!editor || !uri || file === undefined) {
      await this.add(); // says why there is nothing to comment on here
      return;
    }
    const { start, end } = editor.selection;
    const last = end.line > start.line && end.character === 0 ? end.line - 1 : end.line;
    const lines = editor.document.getText(new vscode.Range(start.line, 0, last, editor.document.lineAt(last).text.length));
    const body = `\`\`\`suggestion\n${lines}\n\`\`\``;
    const box = this.controller.createCommentThread(uri, new vscode.Range(start.line, 0, last, 0), []);
    const comment = new ThreadComment({ path: file, line: last + 1, message: body, text: body, markdown: body, patch_set: null, state: "published", url: null, author: "you" });
    comment.body = body;
    comment.mode = vscode.CommentMode.Editing;
    comment.thread = box;
    box.comments = [comment];
    box.canReply = false;
    box.label = "Suggest an edit: change the lines in the block";
    box.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.suggesting.set(comment, box);
  }

  /** Save a suggestion box: a new comment on its lines, as the version looked at has them. */
  private async saveSuggestion(comment: ThreadComment, box: vscode.CommentThread): Promise<void> {
    const [target, file, text] = [this.view.target, this.pathOf(box.uri), comment.text.trim()];
    const range = box.range as vscode.Range | undefined;
    if (!target || file === undefined || !text) {
      return;
    }
    const state: NewCommentState = this.view.newComment ?? "local-draft";
    try {
      await (await this.model.client()).newComment(target.rev, commentLocation(file, range && { start: range.start.line + 1, end: range.end.line + 1 }), text, state, this.view.diff?.commit);
      this.suggesting.delete(comment);
      box.dispose();
      this.saved(state, "Draft with a suggested edit saved on Gerrit");
      await this.view.open();
    } catch (error) {
      void offerWaysOn("error", `Comment not saved: ${describeError(error)}`, failureWays(error));
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
    comment.body = new vscode.MarkdownString(comment.source.message); // the text you wrote, not the fixes shown below it
    comment.mode = vscode.CommentMode.Editing;
    comment.thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    this.editing.add(comment);
    this.redraw(comment);
  }

  /** Leave the text box: the comment says again what is saved. A suggestion box not saved goes. */
  cancelEdit(comment: ThreadComment): void {
    const box = this.suggesting.get(comment);
    if (box) {
      this.suggesting.delete(comment);
      box.dispose();
      return;
    }
    comment.body = shownBody(comment.source);
    comment.mode = vscode.CommentMode.Preview;
    this.editing.delete(comment);
    this.redraw(comment);
    if (this.outdated && !this.editing.size) {
      this.rebuild();
    }
  }

  /** Save what the text box says. Empty or unchanged text leaves the comment as it was. */
  async saveEdit(comment: ThreadComment): Promise<void> {
    const box = this.suggesting.get(comment);
    if (box) {
      return this.saveSuggestion(comment, box);
    }
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
      void offerWaysOn("error", `Comment not saved: ${describeError(error)}`, failureWays(error));
      return false;
    }
  }

  /** Delete a draft, local draft or note of yours, after asking: it cannot be taken back. True once it is deleted. */
  async discard(own: Comment, rev: string | undefined = this.view.target?.rev, reply = false): Promise<boolean> {
    const what = reply ? "draft reply" : YOURS[own.state];
    if (!what || !own.comment_id || !rev) {
      return false;
    }
    const where = `${own.path}${own.line ? `:${own.line}` : ""}`;
    const sure = await ui().message("warning", `Delete your ${what} on ${where}?`, { modal: true, detail: own.message }, ["Delete"]);
    if (sure !== "Delete") {
      return false;
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
      return true;
    } catch (error) {
      void offerWaysOn("error", `Comment not deleted: ${describeError(error)}`, failureWays(error));
      return false;
    }
  }

  /** Delete the comment a thread shows, from its title bar. */
  discardInThread(comment: ThreadComment): Promise<boolean> {
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
      await this.view.openLocation({ path: chain.path, line: this.view.placeOf(chain).line });
      this.startEdit(shown);
      return;
    }
    const text = await ui().input({ title: `Edit your ${YOURS[own.state] ?? "comment"} — ${chain.path}`, value: own.message });
    if (text?.trim() && text.trim() !== own.message.trim()) {
      await this.edit(rev, own, text.trim());
    }
  }

  /** Delete your unpublished comment in `chain`: the chain itself when you started it, else your draft reply. */
  async discardChain(chain: CommentChain): Promise<boolean> {
    const own = ownComment(chain);
    return own ? this.discard(own, undefined, own !== chain.comments[0]) : false;
  }

  dispose(): void {
    this.listeners.forEach((listener) => listener.dispose());
    this.aiming?.dispose();
    this.aimed.dispose();
    this.controller.dispose();
  }
}
