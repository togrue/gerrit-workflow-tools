// "Change": one change in detail — what it needs, its commit message (editable for your own
// commit), which version is compared with what, its files (as diffs), its comment chains and,
// on demand, the CI findings with the ones this change introduced on top. A webview
// (media/change.js) draws the view model of changeRows.ts; this loads it and acts on its clicks.

import * as path from "node:path";
import * as vscode from "vscode";

import { CommentChain, Diff, FileChange, GerClient, Patchsets, ShownCommit } from "./api";
import { ChangeDetail } from "./changeLoad";
import { ChangeVm, CiView, Mode, NOT_FILES, Version, changeHeading, changeLines, changeVm, isYours } from "./changeRows";
import { ChangeTarget, Model, describeError } from "./model";
import { waitingFor } from "./state";
import { ui } from "./ui";
import { Message, WebView } from "./webview";

export const FILE_SCHEME = "ger-file";

const NOTHING_SHOWN = "Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.";

/** What the comment commands take: the chain a row of the view stands for. */
export interface ThreadArg {
  kind: "thread";
  chain: CommentChain;
}

export interface Location {
  path: string;
  line: number | null;
}

/** A file as it is in a commit: `ger-file:/<path>?<commit>`. */
export function fileUri(rev: string, file: string): vscode.Uri {
  return vscode.Uri.from({ scheme: FILE_SCHEME, path: `/${file}`, query: rev });
}

export class ChangeView extends WebView<ChangeVm> {
  private readonly loaded = new vscode.EventEmitter<void>();
  /** Fires when a change (or its reload) has arrived. */
  readonly onDidLoad = this.loaded.event;

  /** The change selected, and what of it has come: drawn at once from its row, filled in answer by answer. */
  private readonly detail = new ChangeDetail();
  /** Which version is looked at. A change you open starts on your local commit when there is one. */
  version: Version = "current";
  mode: Mode = "base";
  private patchsets: Patchsets | undefined;
  private ci: CiView = { state: "closed" };
  /** Aborted with each open and close: the requests of a load nobody waits for any more are called off, so ger serve skips what it has not started. */
  private requests = new AbortController();
  private loading: Promise<void> = Promise.resolve();
  /**
   * Whether the page has the commit message's first line on screen, as it last said. Until it
   * says, it is taken to: a page starts scrolled to the top, where that line sits under the
   * head row, so the title bar does not show the subject only to drop it a moment later.
   */
  private firstLineVisible = true;
  /** What decorate last set on the view's title bar: what the end-to-end suite prints. */
  heading: { title: string; description: string | undefined } | undefined;
  /** Gives a stack commit a new message; `applied` runs once it has it. Set by the extension. */
  reword: (sha: string, message: string, applied: () => void) => Promise<boolean> = () => Promise.resolve(false);
  /** Opens a chain where it sits, its editor thread expanded. Set by the extension. */
  showThread: (chain: CommentChain) => Promise<void> = (chain) => this.openLocation({ path: chain.path, line: chain.line });
  /** Saves `text` as your draft reply in `chain`; whether it was saved. Set by the extension. */
  replyWith: (chain: CommentChain, text: string, resolved: boolean | undefined) => Promise<boolean> = () => Promise.resolve(false);

  get target(): ChangeTarget | undefined {
    return this.detail.target;
  }

  /** `/show`'s answer for the change selected; undefined until it has come. */
  get shown(): ShownCommit | undefined {
    return this.detail.shown;
  }

  /** The version looked at against its parent: its files, and the commit whose lines comments sit on. */
  get diff(): Diff | undefined {
    return this.detail.diff;
  }

  /** The review delta: for the mode chosen, or (someone's change) what is new since your review. */
  get delta(): Diff | undefined {
    return this.detail.delta;
  }

  constructor(
    extensionUri: vscode.Uri,
    private readonly model: Model,
  ) {
    super(extensionUri, "change");
    // The view says why nothing is shown while ger is not running, and whether a rebase keeps the message read-only.
    model.onDidChange(() => this.render());
  }

  /** A new page: it starts at the top, the message's first line on screen, until it says otherwise. */
  override resolveWebviewView(view: vscode.WebviewView): void {
    this.firstLineVisible = true;
    super.resolveWebviewView(view);
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
   * Show `target`; with no argument, reload the change that is shown. Another change starts on
   * its local commit against its base; `keep` (a reload, or the same change under a new commit
   * id) leaves the version and the comparison as you chose them.
   */
  open(target: ChangeTarget | undefined = this.target, keep: boolean = target === this.target): Promise<void> {
    if (!target) {
      return this.loading;
    }
    if (!keep) {
      [this.version, this.mode, this.patchsets] = [target.local ? "local" : "current", "base", undefined];
    }
    const token = this.supersede(() => this.detail.select(target, keep));
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

  /** The changeish the patchset endpoints take; undefined before the first push. */
  private change(target: ChangeTarget): string | undefined {
    const number = target.number ?? (target === this.target ? (this.shown?.number ?? undefined) : undefined);
    return number ? `change:${number}` : target.local ? undefined : target.rev;
  }

  /** Your commit, not a patchset of it: its files are in this worktree. */
  private showsLocal(): boolean {
    return Boolean(this.target?.local) && this.version === "local";
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
        return this.detail.fail(token, describeError(error)) && this.render();
      }
      // The commit against its parent comes with the commit: one request, and ger serve resolves it once.
      const together = !this.patchsetDiff(target);
      const commit = this.fetchCommit(client, target, together, signal).then(
        ([shown, files]) => {
          if (this.detail.arrive(token, "commit", shown) && (!together || this.detail.arrive(token, "base", files))) {
            // Which save button a new comment box offers: "Save Draft" or "Save Local Draft" (plus "Save Private Note").
            void vscode.commands.executeCommand("setContext", "ger.newComment", shown.new_comment ?? "none");
            this.render();
          }
        },
        (error: unknown) => this.detail.fail(token, describeError(error)) && this.render(),
      );
      const base = together
        ? undefined
        : this.baseDiff(client, target, token, signal)
            .catch(() => undefined)
            .then((diff) => this.detail.arrive(token, "base", diff) && this.render());
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

  private async deltaDiff(client: GerClient, target: ChangeTarget, signal: AbortSignal): Promise<Diff | undefined> {
    const change = this.change(target);
    if (!change) {
      return undefined;
    }
    const local = target.local && this.version === "local" ? target.rev : undefined;
    if (this.mode === "gerrit") {
      return local ? client.diff(change, { since: "current", local }, signal) : undefined;
    }
    // Someone's change: asked either way, as what is new since your review is offered up front.
    if (this.mode === "reviewed" || !target.local) {
      return client.diff(change, { since: "reviewed", patchset: typeof this.version === "number" ? this.version : undefined, local }, signal);
    }
    return undefined;
  }

  /** The patchsets to choose from. After the change itself, and only once it has stayed a moment: walking the stack with the arrow keys asks nothing. */
  private async loadPatchsets(token: number, signal: AbortSignal): Promise<void> {
    const change = this.target && this.change(this.target);
    if (!change || !this.shown?.pushed || this.patchsets) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (!this.detail.current(token)) {
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
      [this.version, this.mode] = [local ? "local" : "current", "base"];
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

  // --- The view ---------------------------------------------------------------------

  private readOnly(): string {
    if (!this.target?.local) {
      return "Only a commit of your local stack can be reworded here.";
    }
    if (this.version !== "local") {
      return "This is a patchset on Gerrit: choose the local commit to edit its message.";
    }
    return this.model.stack?.rebase_in_progress ? "A rebase is in progress: finish or abort it first." : "";
  }

  vm(): ChangeVm {
    const waiting = this.model.overview ? undefined : waitingFor(this.model.state, "A change");
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
      mode: this.mode,
      readOnly: this.readOnly(),
      ci: this.ci,
    });
    if (!state) {
      return { empty: this.target && this.detail.loading() ? `Loading ${this.target.title}…` : NOTHING_SHOWN };
    }
    return changeVm(state);
  }

  /** The view as text, with the CI findings fetched: for the end-to-end suite. */
  async lines(): Promise<string[]> {
    if (this.vm().ci?.state === "closed") {
      await this.openCi();
    }
    return changeLines(this.vm());
  }

  protected decorate(view: vscode.WebviewView, vm: ChangeVm): void {
    const seen = { viewVisible: view.visible, firstLineVisible: this.firstLineVisible };
    const commit = this.detail.commit();
    this.heading = changeHeading(commit?.number ?? this.target?.number, commit?.summary ?? this.target?.title, vm, seen);
    ({ title: view.title, description: view.description } = this.heading);
  }

  /** Scroll to a comment chain and mark it, without taking the keyboard. */
  revealThread(chain: CommentChain): void {
    this.show(true);
    this.post({ type: "reveal", id: chain.root_id });
  }

  protected receive(message: Message): unknown {
    const diff = this.shownDiff();
    switch (message.type) {
      case "subjectVisible":
        this.firstLineVisible = message.visible === true;
        return this.view && this.decorate(this.view, this.vm());
      case "file": {
        const file = diff?.files[message.index as number];
        return file ? this.openDiff(file, diff) : undefined;
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
        if (message.action === "replyText") {
          return this.replyInView(chain, String(message.text ?? ""), message.resolved === true ? true : undefined);
        }
        return vscode.commands.executeCommand(`ger.${String(message.action)}`, arg);
      }
      case "finding":
        return this.openLocation({ path: message.path as string, line: (message.line as number | null) ?? null });
      case "url":
        return ui().openExternal(vscode.Uri.parse(message.url as string));
      case "version":
        return this.choose(message.value as string, this.mode);
      case "mode":
        return this.choose(String(this.version), message.value as Mode);
      case "ci":
        return this.openCi();
      case "message":
        return this.applyMessage(message.text as string);
      case "command":
        return vscode.commands.executeCommand(message.command as string);
      default:
        return undefined;
    }
  }

  /** Look at another version, or compare with something else. Gerrit against local is about your commit. */
  choose(version: string, mode: Mode): Promise<void> {
    const wanted: Version = version === "local" || version === "current" ? version : Number(version);
    const moved = wanted !== this.version;
    this.mode = mode === "gerrit" && moved && wanted !== "local" ? "base" : mode;
    this.version = this.mode === "gerrit" ? "local" : wanted;
    return this.open();
  }

  /** A reply typed in the view's own box (a chain with no line in a file); the page clears the box once it is saved. */
  private async replyInView(chain: CommentChain, text: string, resolved: boolean | undefined): Promise<void> {
    const saved = await this.replyWith(chain, text, resolved);
    this.post({ type: "replied", id: chain.root_id, saved });
  }

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

  private async fetchCi(): Promise<CiView> {
    const target = this.target;
    if (!target) {
      return { state: "loaded", contents: [], introduced: null };
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
    return { state: "loaded", contents: commit?.ci_content ?? [], introduced: introduced ? new Set(introduced) : null };
  }

  // --- Opening things ---------------------------------------------------------------

  /** The files the view lists: against the base, or the review delta of the mode chosen. */
  shownDiff(): Diff | undefined {
    return this.mode === "base" ? this.diff : this.delta;
  }

  /** The diff of one file: what it is compared with on the left, the version looked at on the right. */
  async openDiff(file: FileChange, diff: Diff | undefined = this.shownDiff()): Promise<void> {
    if (!diff || diff.base === null) {
      return;
    }
    const left = fileUri(diff.base, file.old_path ?? file.path);
    // The commit you are standing on diffs against the real file, so you can edit in place.
    const editable = this.showsLocal() && this.model.stack?.head === diff.commit && file.status !== "D";
    const right = editable ? vscode.Uri.file(path.join(this.model.repo, file.path)) : fileUri(diff.commit, file.path);
    const to = diff.since?.to_patch_set ?? (diff.local_commit ? "local" : diff.since?.current_patch_set);
    const range = diff.since ? `patchset ${diff.since.patch_set} ↔ ${to}` : diff.commit.slice(0, 8);
    const title = `${path.posix.basename(file.path)} (${range})`;
    await vscode.commands.executeCommand("vscode.diff", left, right, title);
  }

  /** The document a path of the shown change lives in: your file for your local commit, the commit's otherwise. */
  locationUri(file: string): vscode.Uri {
    const commit = this.diff?.commit ?? this.shown?.sha;
    return this.showsLocal() || !commit ? vscode.Uri.file(path.join(this.model.repo, file)) : fileUri(commit, file);
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
      void ui().message("warning", `Cannot open ${location.path}: ${describeError(error)}`, {}, []);
    }
  }
}
