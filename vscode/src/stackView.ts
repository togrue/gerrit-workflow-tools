// "Local stack": the commits above the upstream, newest first. A webview (media/stack.js) draws
// the rows of stackRows.ts; this is what its clicks, drops and context menu do.

import * as vscode from "vscode";

import { GerApiError, GerClient, Rewritten, StackCommit, StackStep } from "./api";
import { blockedMove, movedTo, sentence } from "./format";
import { ChangeTarget, Model, describeError } from "./model";
import { StackVm, frozen, stackLines, stackVm } from "./stackRows";
import { waitingFor } from "./state";
import { ui } from "./ui";
import { Message, WebView } from "./webview";

const STASH = "Stash and Continue";

export function commitTarget(commit: StackCommit): ChangeTarget {
  return { rev: commit.sha, title: commit.summary, number: commit.number ?? undefined, url: commit.gerrit_url, local: true };
}

/** What a command gets from the view: the row's context (`sha`, and `shas` when several rows are selected), or a commit id. */
export type CommitArg = { sha?: unknown; shas?: unknown } | string | undefined;

export class StackView extends WebView<StackVm> {
  /** Runs when a row is chosen: `preserveFocus` while you walk the list with the arrow keys. */
  onOpen: (target: ChangeTarget, preserveFocus: boolean) => unknown = () => undefined;

  constructor(
    extensionUri: vscode.Uri,
    private readonly model: Model,
  ) {
    super(extensionUri, "stack");
    model.onDidChange(() => this.render());
  }

  /** Oldest first, as git and the service order them. */
  private commits(): StackCommit[] {
    return this.model.stack?.commits ?? [];
  }

  vm(): StackVm {
    const waiting = this.model.overview ? undefined : waitingFor(this.model.state, "Your local stack");
    const vm = stackVm(this.model.overview?.local, this.model.upstreamChoice, (lower, upper) => this.model.swapConflict(lower, upper));
    return waiting ? { ...vm, waiting } : vm;
  }

  /** The view as text: for the end-to-end suite. */
  lines(): string[] {
    return stackLines(this.vm());
  }

  protected decorate(view: vscode.WebviewView): void {
    const stack = this.model.stack;
    const where = stack ? `${stack.branch ?? "detached"} → ${stack.stack.target_branch}` : undefined;
    view.description = where && this.model.stale ? `${where} · out of date` : where;
  }

  /** The commits a command's argument names, oldest first. */
  private named(arg: CommitArg): StackCommit[] {
    const one = typeof arg === "string" ? arg : arg?.sha;
    const many = typeof arg === "object" && Array.isArray(arg.shas) ? arg.shas : [one];
    return this.commits().filter((commit) => many.includes(commit.sha));
  }

  /** The one commit a command's argument is about: the row it was invoked on. */
  commit(arg: CommitArg): StackCommit | undefined {
    const sha = typeof arg === "string" ? arg : arg?.sha;
    return this.commits().find((commit) => commit.sha === sha);
  }

  target(arg: CommitArg): ChangeTarget | undefined {
    const commit = this.commit(arg);
    return commit ? commitTarget(commit) : undefined;
  }

  protected receive(message: Message): unknown {
    switch (message.type) {
      case "open": {
        const target = this.target(message.sha as string);
        return target ? this.onOpen(target, message.keepFocus === true) : undefined;
      }
      case "move":
        return this.move(message.shas as string[], message.gap as number);
      case "frozen":
        return this.explainFrozen();
      case "command":
        return vscode.commands.executeCommand(message.command as string, ...((message.args as unknown[] | undefined) ?? []));
      default:
        return undefined;
    }
  }

  private explainFrozen(): boolean {
    const stack = this.model.stack;
    const why = stack ? frozen(stack) : null;
    if (why) {
      void ui().message("info", `${why} Nothing was changed.`, {}, []);
    }
    return Boolean(why);
  }

  // --- Copying ----------------------------------------------------------------------

  /** Copy the commit ids, or the Change-Ids, of the selected rows: one per line, newest first as shown. */
  async copy(arg: CommitArg, what: "hash" | "change-id"): Promise<void> {
    const values = this.named(arg)
      .reverse()
      .map((commit) => (what === "hash" ? commit.sha : commit.change_id))
      .filter((value): value is string => Boolean(value));
    if (!values.length) {
      return;
    }
    await ui().copy(values.join("\n"));
    const noun = what === "hash" ? "commit hash" : "Change-Id";
    ui().status(`$(check) Copied ${values.length === 1 ? `the ${noun}` : `${values.length} ${noun}${what === "hash" ? "es" : "s"}`}`, 4000);
  }

  // --- Rewriting --------------------------------------------------------------------

  /**
   * Move the commits `shas` to `gap` of the list as shown (newest first): gap 0 is above the
   * top row, the number of rows is under the last. They stay together, in their own order.
   */
  async move(shas: readonly string[], gap: number): Promise<void> {
    if (this.explainFrozen()) {
      return;
    }
    const shown = this.commits()
      .map((commit) => commit.sha)
      .reverse();
    const next = movedTo(shown, shas, gap);
    if (!next) {
      return;
    }
    const order = [...next].reverse();
    if (shas.length === 1 && this.explainBlocked(shas[0], order)) {
      return;
    }
    await this.rewrite((client, autostash) => client.rewrite(order, autostash), "Stack reordered.");
  }

  /** One commit, one place: when the conflict preview already knows the swap fails, say so at once. */
  private explainBlocked(sha: string, order: readonly string[]): boolean {
    const commits = this.commits();
    const from = commits.findIndex((commit) => commit.sha === sha);
    const step = order.indexOf(sha) - from;
    const [commit, other] = [commits[from], commits[from + step]];
    if (Math.abs(step) !== 1 || !commit || !other) {
      return false;
    }
    const conflict = step > 0 ? this.model.swapConflict(commit.sha, other.sha) : this.model.swapConflict(other.sha, commit.sha);
    if (conflict) {
      void ui().message("info", `${blockedMove(commit.summary, other.summary, conflict)} Nothing was changed.`, {}, []);
    }
    return Boolean(conflict);
  }

  /** Fold a commit into the one below it. One message survives whole, and with it its Change-Id. */
  async fold(arg: CommitArg, takeMessage: boolean): Promise<void> {
    const commits = this.commits();
    const index = commits.findIndex((commit) => commit.sha === this.commit(arg)?.sha);
    if (index < 1 || this.explainFrozen()) {
      return;
    }
    const [below, folded] = [commits[index - 1], commits[index]];
    const [kept, gone] = takeMessage ? [folded, below] : [below, folded];
    const action = takeMessage ? "fixup-take-message" : "fixup";
    const steps = commits.map((c): StackStep => (c.sha === folded.sha ? { sha: c.sha, action } : c.sha));
    const orphan = gone.pushed ? ` The Gerrit change of "${gone.summary}" stays open: abandon it there.` : "";
    await this.rewrite((client, autostash) => client.rewrite(steps, autostash), `Squashed into "${kept.summary}".${orphan}`);
  }

  async drop(arg: CommitArg): Promise<void> {
    const commit = this.commit(arg);
    if (!commit || this.explainFrozen()) {
      return;
    }
    const detail = commit.pushed
      ? "Its change stays open on Gerrit until you abandon it there. You can undo the drop right after."
      : "It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.";
    const answer = await ui().message("warning", `Drop "${commit.summary}" from the stack?`, { modal: true, detail }, ["Drop"]);
    if (answer !== "Drop") {
      return;
    }
    const steps = this.commits().map((c): StackStep => (c.sha === commit.sha ? { sha: c.sha, action: "drop" } : c.sha));
    await this.rewrite((client, autostash) => client.rewrite(steps, autostash), `Dropped "${commit.summary}".`);
  }

  /** Give a commit a new commit message; `applied` runs as soon as it has it, before Undo is offered. */
  async reword(sha: string, message: string, applied: () => void = () => undefined): Promise<boolean> {
    const commit = this.commit(sha);
    if (!commit) {
      void ui().message("warning", "Commit message not applied: the commit is no longer in the local stack.", {}, []);
      return false;
    }
    return this.rewrite((client) => client.reword(sha, message), `Commit message of "${commit.summary}" changed.`, applied);
  }

  /**
   * Run one rewrite, show its result, and offer Undo. Uncommitted changes in the way are not a
   * dead end: ask, then stash them for the rewrite and put them back after it.
   */
  private async rewrite(run: (client: GerClient, autostash: boolean) => Promise<Rewritten>, done: string, applied: () => void = () => undefined): Promise<boolean> {
    const before = this.commits();
    try {
      const client = await this.model.client();
      const attempt = (autostash: boolean) => ui().progress({ viewId: "ger.stack" }, "Rewriting the stack", () => run(client, autostash));
      let stack: Rewritten;
      try {
        stack = await attempt(false);
      } catch (error) {
        if (!(error instanceof GerApiError) || error.reason !== "uncommitted-changes") {
          throw error;
        }
        const detail = "They are stashed for the rewrite and put back right after it, also when the rewrite is refused.";
        const answer = await ui().message("warning", "You have uncommitted changes. Stash them and continue?", { modal: true, detail }, [STASH]);
        if (answer !== STASH) {
          return false;
        }
        stack = await attempt(true);
      }
      this.model.setStack(stack);
      void this.model.refresh();
      applied();
      if (stack.stash_kept) {
        void ui().message("warning", "Your uncommitted changes no longer fit the rewritten commits. They are safe in the stash: run git stash pop and resolve the conflicts.", {}, []);
      }
      if (stack.head && stack.head !== stack.previous_head) {
        // Only commits Gerrit has seen and that got new ids need a push; a commit never pushed does not.
        const kept = new Set(stack.commits.map((c) => c.sha));
        const push = before.some((c) => c.pushed && !kept.has(c.sha)) ? " Push to update Gerrit." : "";
        await this.offerUndo(client, stack, `${done}${push}`);
      }
      return true;
    } catch (error) {
      // All or nothing on the server: a refusal means the branch is exactly as it was.
      const reason = describeError(error);
      void ui().message("warning", /left as it was/.test(reason) ? sentence(reason) : `Stack left as it was: ${reason}`, {}, []);
      void this.model.refresh();
      return false;
    }
  }

  private async offerUndo(client: GerClient, stack: Rewritten, text: string): Promise<void> {
    try {
      const undo = await ui().message("info", text, {}, ["Undo"]);
      if (undo && stack.head) {
        this.model.setStack(await client.undo(stack.head, stack.previous_head));
        void this.model.refresh();
      }
    } catch (error) {
      void ui().message("warning", `Not undone: ${describeError(error)}`, {}, []);
      void this.model.refresh();
    }
  }
}
