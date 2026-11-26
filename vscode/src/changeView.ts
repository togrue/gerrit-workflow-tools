// "Change": one change in detail — what it needs, its commit message (editable for your own
// commit), which version is compared with what, its files (as diffs), its comment chains and,
// on demand, the CI findings with the ones this change introduced on top. Stopped there by a rebase
// on a conflict, it says so first, with the conflicted files and the rebase's buttons. A webview
// (media/change.js) draws the view model of changeRows.ts; this loads it and acts on its clicks.

import * as path from "node:path";
import * as vscode from "vscode";

import { CommentChain, Diff, FileChange, GerClient, Patchsets, ShownCommit } from "./api";
import { ChangeDetail, ciRevs, sameChange, stoppedHere } from "./changeLoad";
import { ChangeState, ChangeVm, SeenWhere, seenWhere, CiView, From, MODES, Mode, NOTHING_SHOWN, NOT_FILES, READ_ONLY, Sides, Version, changeHeading, changeLines, changeVm, fitSides, isYours, modeSides, toAnswer } from "./changeRows";
import { FILE_SCHEME, filePath, fileUri, openDiff } from "./diffTabs";
import { FilesSeen } from "./filesSeen";
import { ChangeTarget, Model, describeError } from "./model";
import { openConflict } from "./rebase";
import { waitingFor } from "./state";
import { NextAction, nextAction } from "./nextAction";
import { focusedMember, reviewFocus } from "./reviewFocus";
import { markOf } from "./staleDiffs";
import { ui } from "./ui";
import { TaskVm, taskLines } from "./taskRows";
import { Message, WebView } from "./webview";

/** What the comment commands take: the chain a row of the view stands for. */
export interface ThreadArg {
  kind: "thread";
  chain: CommentChain;
}

/** Where the default mode is remembered (`globalState`): it is yours, whatever the repository. */
export const MODE_KEY = "ger.change.defaultMode";

export interface Location {
  path: string;
  line: number | null;
}

export class ChangeView extends WebView<ChangeVm> {
  private readonly loaded = new vscode.EventEmitter<void>();
  /** Fires when a change (or its reload) has arrived. */
  readonly onDidLoad = this.loaded.event;

  /** The change selected, and what of it has come: drawn at once from its row, filled in answer by answer. */
  private readonly detail = new ChangeDetail();
  /** To: which version is looked at. A change you open starts on the default mode's, by default your local commit. */
  version: Version = "current";
  /** From: what it is compared with. A pick by hand holds for the change shown; the next one starts on the default mode's. */
  from: From = "base";
  private patchsets: Patchsets | undefined;
  private ci: CiView = { state: "closed" };
  /** Aborted with each open and close: the requests of a load nobody waits for any more are called off, so ger serve skips what it has not started. */
  private requests = new AbortController();
  private loading: Promise<void> = Promise.resolve();
  /** What decorate last set on the view's title bar: what the end-to-end suite prints. */
  heading: string | undefined;
  /** Gives a stack commit a new message; `applied` runs once it has it. Set by the extension. */
  reword: (sha: string, message: string, applied: () => void) => Promise<boolean> = () => Promise.resolve(false);
  /** Opens a chain where it sits, its editor thread expanded. Set by the extension. */
  showThread: (chain: CommentChain) => Promise<void> = async (chain) => void (await this.openLocation({ path: chain.path, line: chain.line }));

  /** The chains already gone to on the change shown (CommentThreads keeps them). Set by the extension. */
  visited: () => ReadonlySet<string> = () => new Set();
  /** The files seen of each version: Gerrit's reviewed flag where the version is a patchset. */
  readonly filesSeen: FilesSeen;

  get target(): ChangeTarget | undefined {
    return this.detail.target;
  }

  /** Where the ticks of the version looked at are kept. */
  private seenWhere(): SeenWhere | undefined {
    const review = this.holdsReview() ? this.model.stack?.staged_review?.patch_set : undefined;
    const version = this.version === "current" && this.patchsets?.current ? this.patchsets.current : this.version;
    return seenWhere(this.target, version, { staged: review, current: this.shown?.current_patch_set ?? this.patchsets?.current });
  }

  /** The files of the comparison shown that were seen. */
  private seenHere(): ReadonlySet<string> {
    const where = this.seenWhere();
    return where ? this.filesSeen.seen(where) : new Set();
  }

  /** The file of the comparison shown that `uri` shows on its right side, and where its tick is kept. */
  private fileOf(uri: vscode.Uri): { where: SeenWhere; path: string } | undefined {
    const [diff, where] = [this.shownDiff(), this.seenWhere()];
    if (!diff || !where) {
      return undefined;
    }
    const own = uri.scheme === "file" && (this.worktreeHolds(diff.commit) || this.showsLocal());
    const name = own ? path.relative(this.model.repo, uri.fsPath).split(path.sep).join("/") : uri.scheme === FILE_SCHEME && uri.query === diff.commit ? filePath(uri, this.model.repo) : undefined;
    return name && diff.files.some((file) => file.path === name && file.status !== "D") ? { where, path: name } : undefined;
  }

  /** Next on the change shown: what its button names and `ger.next` does. Asks nothing of ger serve. */
  next(): NextAction | undefined {
    if (this.busyText) {
      return undefined;
    }
    const stack = this.model.stack;
    const focus = this.holdsReview() ? reviewFocus(this.model.overview, stack) : undefined;
    const { member, above } = focus ? focusedMember(focus) : { member: undefined, above: undefined };
    return nextAction({
      stack,
      shown: this.detail.loading() ? undefined : this.shown,
      chains: this.chains(),
      local: Boolean(this.target?.local),
      reviewing: this.holdsReview(),
      visited: this.visited(),
      files: (this.shownDiff()?.files ?? []).map((file) => file.path),
      seenFiles: this.seenHere(),
      yourTurn: member?.your_turn,
      above: focus ? above : undefined,
    });
  }

  /** Next file: the first file of the comparison shown that was not seen yet, in the Files list's order. */
  async openNextFile(): Promise<void> {
    const diff = this.shownDiff();
    const seen = this.seenHere();
    const file = diff?.files.find((f) => !seen.has(f.path));
    if (file) {
      await this.openDiff(file, diff);
    }
  }

  /** `/show`'s answer for the change selected; undefined until it has come. */
  get shown(): ShownCommit | undefined {
    return this.detail.shown;
  }

  /** The version looked at against its parent: its files, and the commit whose lines comments sit on. */
  get diff(): Diff | undefined {
    return this.detail.diff;
  }

  /**
   * What a new comment on the version looked at becomes by default. Looking at another commit
   * than the one `/show` resolved is looking at a patchset of the change: Gerrit has it, so a
   * draft, whatever your own commit is. Else what `/show` says of the commit.
   */
  get newComment(): "draft" | "local-draft" | undefined {
    const [shown, diff] = [this.shown, this.diff];
    return shown && diff && diff.commit !== shown.sha ? "draft" : shown?.new_comment;
  }

  /** Which save button a new comment box offers: "Save Draft" or "Save Local Draft" (plus "Save Private Note"). */
  private offerNewComment(): void {
    void vscode.commands.executeCommand("setContext", "ger.newComment", this.newComment ?? "none");
  }

  /** The review delta: for the mode chosen, or (someone's change) what is new since your review. */
  get delta(): Diff | undefined {
    return this.detail.delta;
  }

  constructor(
    extensionUri: vscode.Uri,
    private readonly model: Model,
    private readonly memory: vscode.Memento,
  ) {
    super(extensionUri, "change");
    this.filesSeen = new FilesSeen(() => model.client(), (uri) => this.fileOf(uri));
    this.filesSeen.onDidChange(() => this.render());
    // The view says why nothing is shown while ger is not running, and whether a rebase keeps the message read-only.
    model.onDidChange(() => this.render());
  }

  /** The default mode, remembered: what a change opened next starts on. */
  get mode(): Mode {
    const mode = this.memory.get<string>(MODE_KEY);
    return mode !== undefined && mode in MODES ? (mode as Mode) : "base";
  }

  /** What `target` allows comparing: patchsets once it is on Gerrit, your commit when it is in your stack. */
  private sides(target: ChangeTarget): Sides {
    const shown = target === this.target ? this.shown : undefined;
    return { pushed: shown ? shown.pushed : this.change(target) !== undefined, local: Boolean(target.local) };
  }

  /** From and To as the default mode has them for `target`. */
  private startOn(target: ChangeTarget): void {
    ({ from: this.from, to: this.version } = modeSides(this.mode, this.sides(target)));
  }

  /** Resolves once no load is under way: for the end-to-end suite, which reads the view after each press. */
  async idle(): Promise<void> {
    let seen: Promise<void> | undefined;
    while (seen !== this.loading) {
      seen = this.loading;
      await seen;
    }
  }

  /**
   * Show `target`; with no argument, reload the change that is shown. A click starts on the
   * default mode's From and To; `keep` (a reload, or the same change under a new commit id)
   * leaves them as you chose them. What is drawn stays
   * until the answers replace it whenever the change is the one shown (`sameChange`), however
   * it was opened: clicking it again draws no loading frame.
   */
  open(target: ChangeTarget | undefined = this.target, keep: boolean = target === this.target): Promise<void> {
    if (!target) {
      return this.loading;
    }
    const same = keep || (this.target !== undefined && sameChange(this.target, target));
    if (!same) {
      this.patchsets = undefined;
      // Another change: no save button of the one before until its own `/show` says which.
      void vscode.commands.executeCommand("setContext", "ger.newComment", "none");
    }
    const token = this.supersede(() => this.detail.select(target, keep));
    if (!keep) {
      this.startOn(target); // after select: a change shown before does not lend this one its answer
    }
    this.ci = { state: "closed" };
    // The title bar offers "Edit Commit" for your own stack commit, "Review Change" and "Check Out to Work On" otherwise.
    void vscode.commands.executeCommand("setContext", "ger.changeShown", true);
    void vscode.commands.executeCommand("setContext", "ger.changeIsLocal", Boolean(target.local));
    // What the row knows is drawn now; the rest is filled in as it comes.
    this.render();
    // ger serve answers one request at a time: a load for every commit walked past would queue
    // in front of the one you stopped on. So one load runs at a time, the one under way is
    // dropped, and of the opens asked for meanwhile only the latest is loaded.
    const signal = this.requests.signal;
    const load = () => (this.detail.current(token) ? this.load(target, token, signal) : undefined);
    this.loading = this.loading.then(load, load);
    return this.loading;
  }

  /**
   * Show `target` from answers already in hand (`shown` with its `files`): the next change of a
   * review, asked while you read the one before it. Drawn at once; nothing is asked of ger serve.
   */
  prefill(target: ChangeTarget, shown: ShownCommit, files: Diff): void {
    this.patchsets = undefined;
    const token = this.supersede(() => this.detail.select(target, false));
    this.startOn(target);
    this.ci = { state: "closed" };
    this.detail.arrive(token, "commit", shown);
    this.detail.arrive(token, "base", files);
    void vscode.commands.executeCommand("setContext", "ger.changeShown", true);
    void vscode.commands.executeCommand("setContext", "ger.changeIsLocal", Boolean(target.local));
    this.offerNewComment();
    this.render();
    this.loaded.fire();
  }

  /**
   * The change drawn, under the target it has now (checked out here: your files hold it): kept as
   * drawn while it reloads, its sides picked anew for that target.
   */
  adopt(target: ChangeTarget): Promise<void> {
    this.startOn(target);
    return this.open(target, true);
  }

  /** What is under way that the view waits on (a checkout of the change shown): said in place of Next. */
  private busyText: string | undefined;

  busy(text: string | undefined): void {
    this.busyText = text;
    this.render();
  }

  /** The changeish the patchset endpoints take; undefined before the first push. */
  private change(target: ChangeTarget): string | undefined {
    const number = target.number ?? (target === this.target ? (this.shown?.number ?? undefined) : undefined);
    return number ? `change:${number}` : target.local ? undefined : target.rev;
  }

  /**
   * Whether the change shown is the one a staged review holds here: then it is read as somebody
   * else's (what is new since your review), and when Gerrit has moved on, updating it is offered.
   */
  private holdsReview(target: ChangeTarget | undefined = this.target): boolean {
    const review = this.model.stack?.staged_review;
    return Boolean(review && target?.local && target.number === review.number);
  }

  private reviewing(): ChangeState["reviewing"] {
    if (!this.holdsReview()) {
      return undefined;
    }
    const shown = this.detail.commit();
    return { behind: shown?.patchset_status === "outdated", newest: this.patchsets?.current ?? null };
  }

  /**
   * In a staged review, start on what is new since your review when you reviewed an earlier
   * patchset than the one checked out: the re-review is about that. Asks for the patchsets.
   */
  async preferReviewDelta(): Promise<void> {
    const target = this.target;
    const change = target && this.change(target);
    if (!target || !change || !this.holdsReview(target) || this.from !== "base") {
      return;
    }
    const patchsets = this.patchsets ?? (await (await this.model.client()).patchsets(change).catch(() => undefined));
    const review = this.model.stack?.staged_review;
    if (patchsets && this.target === target) {
      this.patchsets = patchsets;
      if (patchsets.reviewed !== null && review && patchsets.reviewed < review.patch_set) {
        await this.choose("reviewed", "local");
      }
    }
  }

  /** Your commit, not a patchset of it: its files are in this worktree. */
  private showsLocal(): boolean {
    return Boolean(this.target?.local) && this.version === "local";
  }

  /**
   * Whether the working files are `commit`'s content, so a diff of it shows your file on the right
   * and its comment threads sit there, where language features work: the change a staged review
   * holds here (core's `staged_review`), seen at that very commit; or, looking at your local
   * commit, the one you stand on. Any other commit (another patchset, a commit below HEAD) is read
   * from git as it is in the commit.
   */
  worktreeHolds(commit: string | null | undefined): boolean {
    const stack = this.model.stack;
    if (!commit || !stack) {
      return false;
    }
    return stack.staged_review?.commit === commit || (this.showsLocal() && stack.head === commit);
  }

  /** A new selection token (from `select`): the loads under the previous ones are dropped, and their requests called off. */
  private supersede(select: () => number): number {
    this.requests.abort();
    this.requests = new AbortController();
    return select();
  }

  /**
   * Ask for the commit, its files and the review delta at once, and draw each as it comes: ger
   * serve answers them one after the other, and the commit (message, files, comments) is asked
   * first. An answer for a change selected before (`token` no longer current) is dropped.
   */
  private async load(target: ChangeTarget, token: number, signal: AbortSignal): Promise<void> {
    await vscode.window.withProgress({ location: { viewId: "ger.change" } }, async () => {
      let client: GerClient;
      try {
        client = await this.model.client();
      } catch (error) {
        return this.fail(token, error);
      }
      // The commit against its parent comes with the commit: one request, and ger serve resolves it once.
      const together = !this.patchsetDiff(target);
      const commit = this.fetchCommit(client, target, together, signal).then(
        ([shown, files]) => {
          if (this.detail.arrive(token, "commit", shown) && (!together || this.detail.arrive(token, "base", files))) {
            this.offerNewComment();
            this.render();
          }
        },
        (error: unknown) => this.fail(token, error),
      );
      const base = together
        ? undefined
        : this.baseDiff(client, target, token, signal)
            .catch(() => undefined)
            .then((diff) => {
              if (this.detail.arrive(token, "base", diff)) {
                this.offerNewComment(); // the patchset looked at is known now
                this.render();
              }
            });
      const delta = this.deltaDiff(client, target, signal)
        .catch(() => undefined)
        .then((diff) => this.detail.arrive(token, "delta", diff) && this.render());
      await Promise.all([commit, base, delta]);
    });
    if (!this.detail.current(token)) {
      return; // a later open (or a close) owns the view now
    }
    this.loaded.fire();
    void this.loadPatchsets(token, signal);
  }

  /** The change cannot be shown: say why, with no save button for a new comment. Only for the change selected now. */
  private fail(token: number, error: unknown): void {
    if (this.detail.fail(token, describeError(error))) {
      void vscode.commands.executeCommand("setContext", "ger.newComment", "none");
      this.render();
    }
  }

  /** The commit, and with `files` its files against its parent. Files that cannot be had (a change that cannot be fetched) leave the commit shown without them. */
  private async fetchCommit(client: GerClient, target: ChangeTarget, files: boolean, signal: AbortSignal): Promise<[ShownCommit, Diff | undefined]> {
    const options = { comments: "all", context: 3 } as const;
    if (files) {
      try {
        const [shown] = await client.show([target.rev], { ...options, files: true }, signal);
        return [shown, shown.files];
      } catch (error) {
        if (signal.aborted) {
          throw error;
        }
      }
    }
    const [shown] = await client.show([target.rev], options, signal);
    return [shown, undefined];
  }

  /** Whether the version looked at is a patchset of the change, not the commit `/show` resolves: `/diff` is asked for it. */
  private patchsetDiff(target: ChangeTarget): boolean {
    return this.change(target) !== undefined && (typeof this.version === "number" || (this.version === "current" && Boolean(target.local)));
  }

  /** A patchset looked at against its parent. */
  private async baseDiff(client: GerClient, target: ChangeTarget, token: number, signal: AbortSignal): Promise<Diff> {
    const change = this.change(target);
    let version = this.version;
    if (version === "current" && target.local && change) {
      // Your commit carries the change, so the change alone would name your commit: ask which patchset is current.
      const patchsets = this.patchsets ?? (await client.patchsets(change, signal));
      version = patchsets.current ?? "local";
      if (this.detail.current(token)) {
        [this.patchsets, this.version] = [patchsets, version]; // a load dropped meanwhile leaves the next change's choice alone
      }
    }
    return typeof version === "number" && change ? client.diff(change, { patchset: version }, signal) : client.diff(target.rev, {}, signal);
  }

  /** From against To; for someone's change looked at against its base, what is new since your review (its hint). */
  private async deltaDiff(client: GerClient, target: ChangeTarget, signal: AbortSignal): Promise<Diff | undefined> {
    const change = this.change(target);
    if (!change) {
      return undefined;
    }
    const local = target.local && this.version === "local" ? target.rev : undefined;
    const patchset = typeof this.version === "number" ? this.version : undefined;
    if (this.from !== "base") {
      return client.diff(change, { since: this.from, patchset, local }, signal);
    }
    // Someone's change (also one checked out for review): asked either way, as what is new since your review is offered up front.
    return target.local && !this.holdsReview(target) ? undefined : client.diff(change, { since: "reviewed", patchset, local }, signal);
  }

  /** The patchsets to choose from. After the change itself, and only once it has stayed a moment: walking the stack with the arrow keys asks nothing. */
  private async loadPatchsets(token: number, signal: AbortSignal): Promise<void> {
    const change = this.target && this.change(this.target);
    if (!change || !this.shown?.pushed || this.patchsets) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
    // Asked meanwhile (a staged review's re-review asks at once): not twice.
    if (!this.detail.current(token) || this.patchsets) {
      return;
    }
    try {
      const patchsets = await (await this.model.client()).patchsets(change, signal);
      if (this.detail.current(token)) {
        this.patchsets = patchsets;
        this.render();
      }
    } catch {
      // Without the list, Gerrit's current patchset is still offered.
    }
  }

  /** The shown change is (no longer) a commit of your stack: the title bar offers Edit instead of Review and Check Out, or the other way round. */
  setLocal(local: boolean): void {
    if (this.target && this.target.local !== local) {
      this.detail.target = { ...this.target, local };
      this.startOn(this.detail.target);
      void this.open();
    }
  }

  /** Show nothing: the change that was shown is gone. */
  close(): void {
    this.supersede(() => this.detail.clear());
    this.patchsets = undefined;
    this.ci = { state: "closed" };
    void vscode.commands.executeCommand("setContext", "ger.changeShown", false);
    void vscode.commands.executeCommand("setContext", "ger.changeIsLocal", false);
    void vscode.commands.executeCommand("setContext", "ger.newComment", "none");
    this.render();
    this.loaded.fire();
  }

  /** Unresolved comment chains first, then by file and line. */
  chains(): CommentChain[] {
    const chains = [...(this.shown?.comment_chains ?? [])];
    const open = (c: CommentChain) => (c.resolved ? 1 : 0);
    return chains.sort((a, b) => open(a) - open(b) || a.path.localeCompare(b.path) || (a.line ?? 0) - (b.line ?? 0));
  }

  /** Every chain still open, your own new comments and private notes included. */
  openChains(): CommentChain[] {
    return this.chains().filter((chain) => !chain.resolved);
  }

  /** Feedback to address: open chains somebody started on Gerrit. Not your drafts or notes. */
  unresolved(): CommentChain[] {
    return this.openChains().filter((chain) => !isYours(chain));
  }

  /** What is left for you to answer (`toAnswer`): not what your draft answers, nor what waits on a reply. */
  toAnswer(): CommentChain[] {
    return this.chains().filter(toAnswer);
  }

  // --- The view ---------------------------------------------------------------------

  private readOnly(): string {
    if (!this.target?.local) {
      return READ_ONLY.remote;
    }
    if (this.version !== "local") {
      return READ_ONLY.patchset;
    }
    if (this.model.stack?.staged_review) {
      return READ_ONLY.review; // core refuses ger reword there too
    }
    return this.model.stack?.rebase_in_progress ? READ_ONLY.rebase : "";
  }

  vm(): ChangeVm {
    const waiting = this.model.overview ? undefined : waitingFor(this.model.state, "A change", this.model.incompatible, this.model.failure);
    if (waiting) {
      return { waiting };
    }
    if (this.detail.problem) {
      return { problem: this.detail.problem };
    }
    const state = this.detail.state({
      chains: this.chains(),
      patchsets: this.patchsets,
      version: this.version,
      from: this.from,
      mode: this.mode,
      readOnly: this.readOnly(),
      ci: this.ci,
      // Which commit a rebase stopped at is in the stack ger serve sent: the Change view reads it there, as Local stack does.
      stopped: this.target && stoppedHere(this.target, this.model.stack),
      reviewing: this.reviewing(),
      seen: this.seenHere(),
    });
    if (!state) {
      return { empty: this.target && this.detail.loading() ? `Loading ${this.target.title}…` : NOTHING_SHOWN, task: this.task() };
    }
    const next = this.busyText ? undefined : this.next();
    return { ...changeVm(state), next: next && { label: next.label, title: next.title }, busy: this.busyText, task: this.task() };
  }

  /** The view as text, with the CI findings fetched: for the end-to-end suite. */
  async lines(): Promise<string[]> {
    if (this.vm().ci?.state === "closed") {
      await this.openCi();
    }
    const vm = this.vm();
    return [...taskLines(vm.task).map((line) => `▌ ${line}`), ...changeLines(vm)];
  }

  protected decorate(view: vscode.WebviewView): void {
    const commit = this.detail.commit();
    this.heading = changeHeading(commit?.number ?? this.target?.number);
    view.title = this.heading;
    // Alt+Enter does what the button names, while there is one.
    void vscode.commands.executeCommand("setContext", "ger.nextAction", Boolean(this.target && !this.busyText && this.next()));
  }

  /** Scroll to a comment chain and mark it, without taking the keyboard. */
  revealThread(chain: CommentChain): void {
    this.show(true);
    this.post({ type: "reveal", id: chain.root_id });
  }

  /**
   * Show a chain with no line in a file (about the whole change) where it is answered: here, its
   * text whole, the keyboard on its row, whose buttons reply (in a box at the top of the window).
   */
  async focusThread(chain: CommentChain): Promise<void> {
    await vscode.commands.executeCommand("ger.change.focus");
    this.post({ type: "reveal", id: chain.root_id, open: true });
  }

  /** The task header to draw at the top (reviewing), and what a click on its chain does. Set by the extension. */
  task: () => TaskVm | undefined = () => undefined;
  taskStep: (key: string) => unknown = () => undefined;

  protected receive(message: Message): unknown {
    const diff = this.shownDiff();
    switch (message.type) {
      case "taskStep":
        return this.taskStep(String(message.key));
      case "file": {
        const file = diff?.files[message.index as number];
        return file ? this.openDiff(file, diff) : undefined;
      }
      case "seen": {
        const [file, where] = [diff?.files[message.index as number], this.seenWhere()];
        return file && where ? this.filesSeen.mark(where, file.path, !this.seenHere().has(file.path)) : undefined;
      }
      case "fileComment": {
        const file = diff?.files[message.index as number];
        return file ? vscode.commands.executeCommand("ger.comment.file", file.path) : undefined;
      }
      case "thread": {
        const chain = this.chains().find((c) => c.root_id === message.id);
        if (!chain) {
          return undefined;
        }
        const arg: ThreadArg = { kind: "thread", chain };
        if (message.action === "open") {
          return this.showThread(chain);
        }
        return vscode.commands.executeCommand(`ger.${String(message.action)}`, arg);
      }
      case "finding":
        return this.openLocation({ path: message.path as string, line: (message.line as number | null) ?? null });
      case "url":
        return ui().openExternal(vscode.Uri.parse(message.url as string));
      case "from":
        return this.choose(String(message.value), String(this.version));
      case "to":
        return this.choose(String(this.from), String(message.value));
      case "compare":
        return this.choose(String(message.from), String(message.to));
      case "mode":
        return this.setMode(message.value as Mode);
      case "ci":
        return this.openCi();
      case "message":
        return this.applyMessage(message.text as string);
      case "command":
        return vscode.commands.executeCommand(message.command as string);
      case "conflict":
        return openConflict(this.model, message.path as string);
      default:
        return undefined;
    }
  }

  /**
   * Compare `from` with `to`, picked by hand: for the change shown only, the default mode stays.
   * To at or below From's patchset sets From back to the base commit (`fitSides`).
   */
  choose(from: string, to: string): Promise<void> {
    const side = (value: string) => (value === "base" || value === "reviewed" || value === "current" || value === "local" ? value : Number(value));
    ({ from: this.from, to: this.version } = fitSides(side(from) as From, side(to) as Version, this.patchsets));
    return this.open();
  }

  /** Make `mode` the default, remembered for the changes opened next, and show the change shown that way. */
  async setMode(mode: Mode): Promise<void> {
    if (!(mode in MODES)) {
      return;
    }
    await this.memory.update(MODE_KEY, mode);
    if (this.target) {
      this.startOn(this.target);
    }
    return this.open();
  }

  /** Apply in the message box: `text` as the box holds it, without the Change-Id, which core keeps. */
  private async applyMessage(text: string): Promise<void> {
    const [target, key] = [this.target, this.shown?.change_id ?? this.shown?.sha];
    if (!target || this.readOnly()) {
      return;
    }
    // The page keeps what you typed until it is the commit's message.
    await this.reword(target.rev, text, () => this.post({ type: "applied", key }));
  }

  // --- CI ---------------------------------------------------------------------------

  /** CI content is fetched only when the CI section is opened: it means downloading build logs. */
  async openCi(): Promise<void> {
    if (this.ci.state !== "closed") {
      return;
    }
    const token = this.detail.selection();
    this.ci = { state: "loading" };
    this.render();
    let ci: CiView;
    try {
      ci = await this.fetchCi();
    } catch (error) {
      ci = { state: "error", error: describeError(error) };
    }
    if (this.detail.current(token)) {
      this.ci = ci;
      this.render();
    }
  }

  /** Open the CI section and the first finding of it that has a place in a file: what this change broke first. Whether there was one. */
  async openFirstFinding(): Promise<boolean> {
    await this.openCi();
    const finding = this.vm().ci?.links.flatMap((link) => link.findings).find((row) => row.path);
    if (finding?.path) {
      await this.openLocation({ path: finding.path, line: finding.line });
    }
    return Boolean(finding);
  }

  private async fetchCi(): Promise<CiView> {
    const target = this.target;
    if (!target) {
      return { state: "loaded", contents: [], introduced: null };
    }
    const client = await this.model.client();
    // Introduced findings need the parent: ask for it and this change, not the whole chain.
    const { revs, chain, stack } = ciRevs(target, this.model.stack);
    const commits = await client.show(revs, { ci: true, chain, stack });
    const commit = stack ? commits.find((c) => c.sha === target.rev) : commits[commits.length - 1];
    const introduced = commit?.ci_introduced;
    return { state: "loaded", contents: commit?.ci_content ?? [], introduced: introduced ? new Set(introduced) : null };
  }

  // --- Opening things ---------------------------------------------------------------

  /** The files the view lists: To against its base, or From against To. */
  shownDiff(): Diff | undefined {
    return this.from === "base" ? this.diff : this.delta;
  }

  /**
   * What a file's diff compares: the version compared with on the left (none for an added file),
   * the version looked at on the right (none for a deleted one). What the working files hold is
   * your file (`worktreeHolds`): comments are drawn where they are written, F12 works, edits go in
   * place. `own`: the right side is your file.
   */
  private fileSides(file: FileChange, diff: Diff & { base: string }): { left?: vscode.Uri; right?: vscode.Uri; own: boolean } {
    const own = this.worktreeHolds(diff.commit);
    const left = file.status === "A" ? undefined : fileUri(diff.base, file.old_path ?? file.path, own ? this.model.repo : undefined);
    const right = file.status === "D" ? undefined : own ? vscode.Uri.file(path.join(this.model.repo, file.path)) : fileUri(diff.commit, file.path);
    return { left, right, own };
  }

  /** What a diff's title says it compares: "patchset 1 ↔ 2", or the commit. */
  private static range(diff: Diff): string {
    const to = diff.since?.to_patch_set ?? (diff.local_commit ? "local" : diff.since?.current_patch_set);
    return diff.since ? `patchset ${diff.since.patch_set} ↔ ${to}` : diff.commit.slice(0, 8);
  }

  /**
   * The diff of one file: what it is compared with on the left, the version looked at on the right.
   * An added file opens alone, and so does a deleted one, as VS Code's own Git does: its diff editor
   * shows an empty side as one removed (or added) empty line.
   */
  async openDiff(file: FileChange, diff: Diff | undefined = this.shownDiff()): Promise<void> {
    if (!diff || diff.base === null) {
      return;
    }
    const { left, right, own } = this.fileSides(file, { ...diff, base: diff.base });
    if (right) {
      this.filesSeen.watch(right); // read to its end, it is seen
    }
    if (!left || !right) {
      await vscode.commands.executeCommand("vscode.open", left ?? right);
      return;
    }
    const title = `${path.posix.basename(file.path)} (${ChangeView.range(diff)})`;
    await openDiff(left, right, title, markOf(diff, this.model.stack, own));
  }

  /**
   * Every file of what is compared in one editor, in the Files list's order: VS Code's multi-diff
   * editor, each file with the sides its own diff has, so comments and language features work there
   * as in the file's diff. Where VS Code has no multi-diff editor (before 1.86), the first file's diff.
   */
  async openAll(diff: Diff | undefined = this.shownDiff(), note?: string): Promise<void> {
    if (!diff || diff.base === null || !diff.files.length) {
      const what = this.target ? `${this.target.title}: ` : "";
      void ui().message("info", `${what}there are no files to show${this.from === "base" ? "" : " for this comparison"}.`, {}, []);
      return;
    }
    if (!(await vscode.commands.getCommands(true)).includes("vscode.changes")) {
      await this.openDiff(diff.files[0], diff);
      return;
    }
    const base = diff.base;
    const resources = diff.files.map((file) => {
      const { left, right } = this.fileSides(file, { ...diff, base });
      // The row's name: the file in the repository, as Source Control names it.
      return [vscode.Uri.file(path.join(this.model.repo, file.path)), left, right];
    });
    const number = this.shown?.number ?? this.target?.number;
    const title = `${number ? `Change ${number}` : (this.shown?.summary ?? "Change")} (${ChangeView.range(diff)})${note ? ` — ${note}` : ""}`;
    if (note) {
      this.ahead.add(title);
    } else if (this.worktreeHolds(diff.commit)) {
      this.combined.add(title);
    }
    await vscode.commands.executeCommand("vscode.changes", title, resources);
  }

  /** The titles of the combined diffs opened this session: a review that moves on closes them (`closeCombined`). */
  private readonly combined = new Set<string>();
  /** The titles of combined diffs opened before the checkout of their change (`openAll`'s note): read from git, replaced once it is done. */
  private readonly ahead = new Set<string>();

  /**
   * Close the combined diffs opened of the change a staged review held: their right sides are your
   * files, which hold another change now. VS Code's 1.85 API does not say what a multi-diff tab shows,
   * so they are known by title; single-file diffs go stale on their own (diffTabs.ts).
   */
  async closeCombined(which: "worktree" | "ahead" = "worktree"): Promise<void> {
    const titles = which === "ahead" ? this.ahead : this.combined;
    // VS Code titles the tab "<title> (4 files)"; its input (not in the 1.85 API) lists `textDiffs`.
    const combined = (tab: vscode.Tab) => Boolean((tab.input as { textDiffs?: unknown } | undefined)?.textDiffs) && [...titles].some((title) => tab.label.startsWith(title));
    const tabs = vscode.window.tabGroups.all.flatMap((group) => group.tabs).filter(combined);
    titles.clear();
    if (tabs.length) {
      await vscode.window.tabGroups.close(tabs, true);
    }
  }

  /** The document a path of the shown change lives in: your file for your local commit and for what the working files hold, the commit's otherwise. */
  locationUri(file: string): vscode.Uri {
    const commit = this.diff?.commit ?? this.shown?.sha;
    const yours = this.showsLocal() || this.worktreeHolds(commit) || !commit;
    return yours ? vscode.Uri.file(path.join(this.model.repo, file)) : fileUri(commit, file);
  }

  /**
   * Where `chain` is drawn: in your file, where its lines are now (core's `worktree`) when core could
   * tell, `outdated` when they changed there; else at its lines as Gerrit has them.
   */
  placeOf(chain: CommentChain): { start: number | null; line: number | null; outdated: boolean } {
    const now = this.locationUri(chain.path).scheme === "file" ? chain.worktree : undefined;
    return now ? { start: now.start_line, line: now.line, outdated: now.outdated } : { start: chain.start_line, line: chain.line, outdated: false };
  }

  /** Open where a comment or a CI finding points; the editor it opened in, if it could. */
  async openLocation(location: Location): Promise<vscode.TextEditor | undefined> {
    if (NOT_FILES.includes(location.path)) {
      return undefined;
    }
    const uri = this.locationUri(location.path);
    const line = Math.max(0, (location.line ?? 1) - 1);
    try {
      return await vscode.window.showTextDocument(uri, { selection: new vscode.Range(line, 0, line, 0), preview: true });
    } catch (error) {
      void ui().message("warning", `Cannot open ${location.path}: ${describeError(error)}`, {}, []);
      return undefined;
    }
  }
}
