// Wires the views, the status bar and the commands to one `ger serve` per workspace folder.

import { execFile } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { CommentChain, Diff, FileChange } from "./api";
import { ChangeView, FILE_SCHEME, Location, Node as ChangeNode } from "./changeView";
import { CommentThreads } from "./comments";
import { plural } from "./format";
import { ChangeTarget, Model, describeError } from "./model";
import { OverviewView, headlineTarget } from "./overviewView";
import { GerProcess } from "./server";
import { StackView } from "./stackView";

function setting<T>(name: string, fallback: T): T {
  return vscode.workspace.getConfiguration("ger").get<T>(name, fallback);
}

function executable(): string {
  return setting("executable", "ger").trim() || "ger";
}

function terminal(repo: string): vscode.Terminal {
  const existing = vscode.window.terminals.find((t) => t.name === "ger" && t.exitStatus === undefined);
  const term = existing ?? vscode.window.createTerminal({ name: "ger", cwd: repo });
  term.show(true);
  return term;
}

/** Run a ger command that needs a human in front of it (it prompts, or opens an editor). */
function runInTerminal(repo: string, args: string): void {
  terminal(repo).sendText(`${executable()} ${args}`);
}

function statusBar(model: Model): vscode.StatusBarItem {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  item.command = "ger.openHeadline";
  const render = () => {
    const overview = model.overview;
    const news = model.active.size ? `  $(bell-dot) ${model.active.size}` : "";
    const tooltip = new vscode.MarkdownString(undefined, true);
    if (!overview) {
      item.text = model.failure ? "$(warning) ger" : "$(sync~spin) ger";
      tooltip.appendText(model.failure ?? "Starting ger serve…");
      item.backgroundColor = undefined;
    } else {
      const headline = overview.headline;
      const text = headline ? headline.text : overview.error ? overview.error.message : "nothing needs you";
      const glyph = !headline ? "$(pass)" : headline.reason === "ci-failed" ? "$(error)" : headline.section === "to-review" ? "$(eye)" : "$(comment-unresolved)";
      item.text = `${glyph} ${text.length > 60 ? `${text.slice(0, 59)}…` : text}${news}`;
      item.backgroundColor = headline?.reason === "ci-failed" ? new vscode.ThemeColor("statusBarItem.errorBackground") : undefined;
      for (const section of overview.sections ?? []) {
        const title = section.name === "outgoing" ? "Outgoing" : "To review";
        tooltip.appendMarkdown(`**${title}** — ${plural(section.chains.length, "chain")}`);
        if (section.summary.ci_failures) {
          tooltip.appendMarkdown(`, ${section.summary.ci_failures} with failed CI`);
        }
        if (section.summary.comments) {
          tooltip.appendMarkdown(`, ${section.summary.comments} with unresolved comments`);
        }
        tooltip.appendMarkdown("\n\n");
      }
      const stack = model.stack;
      if (stack) {
        tooltip.appendMarkdown(`**Local stack** — ${plural(stack.summary.commits, "commit")}, ${stack.summary.not_on_gerrit} not on Gerrit\n\n`);
      }
      if (model.active.size) {
        tooltip.appendMarkdown(`$(bell-dot) New activity on ${plural(model.active.size, "chain")}\n\n`);
      }
      tooltip.appendMarkdown(headline ? "Click to open it." : "Click to open the overview.");
    }
    item.tooltip = tooltip;
    item.show();
  };
  model.onDidChange(render);
  render();
  return item;
}

/** The Markdown `ger show --ai` prints for a change: what an agent needs to fix it. */
function agentBrief(repo: string, rev: string): Promise<string> {
  const [command, ...prefix] = executable().split(/\s+/);
  const args = [...prefix, "show", "--ai", "--comments", "unresolved", "--ci", "--context", rev];
  return new Promise((resolve, reject) => {
    // Exit 1 is "attention": the brief is on stdout all the same.
    execFile(command, args, { cwd: repo, maxBuffer: 16 * 1024 * 1024, shell: process.platform === "win32" }, (error, stdout, stderr) => {
      if (stdout.trim()) {
        resolve(stdout);
      } else {
        reject(new Error(stderr.trim() || error?.message || "ger show printed nothing"));
      }
    });
  });
}

/** What the extension hands to its end-to-end tests. */
export interface ExtensionApi {
  model: Model;
  overviewView: OverviewView;
  stackView: StackView;
  changeView: ChangeView;
}

export function activate(context: vscode.ExtensionContext): ExtensionApi | undefined {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    return undefined;
  }
  const repo = folder.uri.fsPath;
  const log = vscode.window.createOutputChannel("ger");
  const model = new Model(new GerProcess(repo, executable, (line) => log.appendLine(line)), context.workspaceState, repo);
  const overviewView = new OverviewView(model);
  const stackView = new StackView(model);
  const changeView = new ChangeView(model);

  const overviewTree = vscode.window.createTreeView("ger.overview", { treeDataProvider: overviewView });
  const stackTree = vscode.window.createTreeView("ger.stack", { treeDataProvider: stackView, dragAndDropController: stackView, canSelectMany: false });
  const changeTree = vscode.window.createTreeView("ger.change", { treeDataProvider: changeView });
  const threads = new CommentThreads(model, changeView, (chain) => {
    const node = changeView.threadNode(chain);
    if (node) {
      void changeTree.reveal(node, { select: true, focus: false });
    }
  });

  model.onDidChange(() => {
    const stack = model.stack;
    stackTree.description = stack ? `${stack.branch ?? "detached"} → ${stack.stack.target_branch}` : undefined;
    const waiting = model.overview?.sections?.find((s) => s.name === "to-review")?.chains.length ?? 0;
    overviewTree.badge = waiting ? { value: waiting, tooltip: `${plural(waiting, "chain")} to review` } : undefined;
  });
  // A rewrite or an amend gives your commits new ids: follow the shown one by its Change-Id.
  model.onDidChange(() => {
    const target = changeView.target;
    const stack = model.stack;
    if (!target?.local || !stack || stack.commits.some((c) => c.sha === target.rev)) {
      return;
    }
    const changeId = changeView.shown?.change_id;
    const moved = changeId ? stack.commits.find((c) => c.change_id === changeId) : undefined;
    if (moved) {
      void changeView.open({ ...target, rev: moved.sha, title: moved.summary });
    } else {
      changeView.close();
    }
  });
  model.onDidGetNewHeadline(async (headline) => {
    if (setting("notify", true) && (await vscode.window.showInformationMessage(headline.text, "Open")) === "Open") {
      await vscode.commands.executeCommand("ger.openHeadline");
    }
  });
  changeView.onDidLoad(() => {
    changeTree.title = changeView.target?.number ? `Change ${changeView.target.number}` : "Change";
    changeTree.description = changeView.shown?.summary ?? changeView.target?.title;
  });

  /** The change a command acts on: its argument (a target or a tree node), else the one shown. */
  const targetOf = (arg: unknown): ChangeTarget | undefined => {
    if (arg && typeof arg === "object" && "rev" in arg) {
      return arg as ChangeTarget;
    }
    type Any = Parameters<typeof OverviewView.target>[0] & Parameters<typeof StackView.target>[0];
    return OverviewView.target(arg as Any) ?? StackView.target(arg as Any) ?? changeView.target;
  };
  const threadOf = (arg: unknown): CommentChain | undefined => {
    const node = arg as ChangeNode | undefined;
    return node?.kind === "thread" ? node.chain : undefined;
  };

  const openChange = async (target: ChangeTarget | undefined) => {
    if (!target) {
      return;
    }
    await vscode.commands.executeCommand("ger.change.focus");
    await changeView.open(target);
    if (target.chainKey) {
      await model.markSeen([target.chainKey]);
    }
  };

  const checkout = async (target: ChangeTarget | undefined) => {
    if (!target) {
      return;
    }
    if (target.local) {
      // Your own commit: stop an interactive rebase on it, so you can amend it in place.
      runInTerminal(repo, `edit ${target.rev}`);
      return;
    }
    try {
      const client = await model.client();
      const review = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Checking out ${target.title}` },
        () => client.reviewOpen(target.number ?? target.rev),
      );
      const where = vscode.Uri.file(review.worktree);
      const inPlace = path.resolve(review.worktree) === path.resolve(repo);
      const open = inPlace ? undefined : "Open Review Worktree";
      const picked = await vscode.window.showInformationMessage(
        `Change ${review.number} (patchset ${review.patch_set}) is checked out${review.staged ? ", staged," : ""} on ${review.branch}${inPlace ? "" : ` in ${review.worktree}`}.`,
        ...(open ? [open] : []),
      );
      if (picked) {
        await vscode.commands.executeCommand("vscode.openFolder", where, { forceNewWindow: true });
      }
    } catch (error) {
      void vscode.window.showWarningMessage(`Not checked out: ${describeError(error)}`);
    }
  };

  /** Put the runbook in front of the user, then start the agent on it or copy it. */
  const runAgent = async (rev: string | undefined) => {
    try {
      const client = await model.client();
      const runbook = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Collecting comments and CI findings" },
        () => client.runbook(rev),
      );
      const { commits, comments, findings, held } = runbook.totals;
      const kept = held ? ` ${plural(held, "comment")} kept for you.` : "";
      if (!commits) {
        void vscode.window.showInformationMessage(`Nothing here for an agent.${kept}`);
        return;
      }
      const file = path.join(os.tmpdir(), `ger-runbook-${Date.now()}.md`);
      await fs.writeFile(file, runbook.markdown, "utf8");
      await vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(file));
      const template = setting("agentCommand", "").trim();
      const start = template ? "Start Agent" : "Copy Runbook";
      const summary = `${plural(commits, "commit")}: ${plural(comments, "comment")} and ${plural(findings, "CI finding")} for the agent.${kept}`;
      const detail = "The agent amends the commits and writes draft replies. Nothing is pushed or published: you review and run ger push.";
      if ((await vscode.window.showInformationMessage(summary, { modal: true, detail }, start)) !== start) {
        return;
      }
      if (!template) {
        await vscode.env.clipboard.writeText(runbook.markdown);
        void vscode.window.showInformationMessage("Runbook copied. Set `ger.agentCommand` to start your agent from here.");
        return;
      }
      // GER_NO_PUBLISH makes ger refuse to publish drafts, whatever the agent decides to try.
      const term = vscode.window.createTerminal({ name: "ger agent", cwd: repo, env: { GER_NO_PUBLISH: "1" } });
      term.show(true);
      term.sendText(template.replaceAll("{rev}", rev ?? "").replaceAll("{brief}", file));
    } catch (error) {
      void vscode.window.showWarningMessage(`Nothing handed over: ${describeError(error)}`);
    }
  };

  const setHold = async (arg: unknown, held: boolean) => {
    const chain = threadOf(arg);
    if (chain) {
      await (await model.client()).hold(chain.root_id, held);
      await changeView.open();
    }
  };

  const handToAgent = async (target: ChangeTarget | undefined) => {
    if (!target) {
      return;
    }
    if (target.local) {
      await runAgent(target.rev);
      return;
    }
    try {
      const brief = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Collecting comments and CI findings" },
        () => agentBrief(repo, target.rev),
      );
      const template = setting("agentCommand", "").trim();
      if (!template) {
        await vscode.env.clipboard.writeText(brief);
        void vscode.window.showInformationMessage("Comments and CI findings copied. Paste them into your agent, or set `ger.agentCommand` to start it from here.");
        return;
      }
      const file = path.join(os.tmpdir(), `ger-brief-${target.rev.replace(/[^\w.-]/g, "_").slice(0, 40)}.md`);
      await fs.writeFile(file, brief, "utf8");
      terminal(repo).sendText(template.replaceAll("{rev}", target.rev).replaceAll("{brief}", file));
    } catch (error) {
      void vscode.window.showWarningMessage(`Nothing to hand over: ${describeError(error)}`);
    }
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    "ger.refresh": async () => {
      if (model.failure) {
        model.restart();
      }
      await model.refresh(true);
      await changeView.open();
    },
    "ger.showLog": () => log.show(),
    "ger.openHeadline": async () => {
      const target = headlineTarget(model);
      await (target ? openChange(target) : vscode.commands.executeCommand("ger.overview.focus"));
    },
    "ger.markSeen": () => model.markSeen(),
    "ger.openChange": (arg: unknown) => openChange(targetOf(arg)),
    "ger.openInGerrit": (arg: unknown) => {
      const url = targetOf(arg)?.url ?? changeView.shown?.gerrit_url;
      return url ? vscode.env.openExternal(vscode.Uri.parse(url)) : undefined;
    },
    "ger.checkout": (arg: unknown) => checkout(targetOf(arg)),
    "ger.reviewDone": async () => {
      try {
        const done = await (await model.client()).reviewDone();
        void vscode.window.showInformationMessage(`Left the review of change ${done.done}.`);
      } catch (error) {
        void vscode.window.showWarningMessage(describeError(error));
      }
    },
    "ger.push": () => runInTerminal(repo, "push"),
    "ger.stack.moveUp": (node: Parameters<StackView["move"]>[0]) => stackView.move(node, 1),
    "ger.stack.moveDown": (node: Parameters<StackView["move"]>[0]) => stackView.move(node, -1),
    "ger.stack.moveUpBlocked": (node: Parameters<StackView["move"]>[0]) => stackView.explainBlocked(node, 1),
    "ger.stack.moveDownBlocked": (node: Parameters<StackView["move"]>[0]) => stackView.explainBlocked(node, -1),
    "ger.stack.squash": (node: Parameters<StackView["move"]>[0]) => stackView.fold(node, false),
    "ger.stack.squashTakeMessage": (node: Parameters<StackView["move"]>[0]) => stackView.fold(node, true),
    "ger.stack.drop": (node: Parameters<StackView["move"]>[0]) => stackView.drop(node),
    "ger.openDiff": (file: FileChange, diff?: Diff) => changeView.openDiff(file, diff),
    "ger.openLocation": (location: Location) => changeView.openLocation(location),
    "ger.nextComment": () => threads.step(1),
    "ger.prevComment": () => threads.step(-1),
    "ger.reply": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.replyToChain(chain, undefined) : undefined;
    },
    "ger.replyDone": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.replyToChain(chain, true, "Done") : undefined;
    },
    "ger.thread.reply": (reply: vscode.CommentReply) => threads.replyInThread(reply, undefined),
    "ger.thread.resolve": (reply: vscode.CommentReply) => threads.replyInThread(reply, true),
    "ger.agentBrief": (arg: unknown) => handToAgent(targetOf(arg)),
    "ger.agentWalk": () => runAgent(undefined),
    "ger.hold": (arg: unknown) => setHold(arg, true),
    "ger.release": (arg: unknown) => setHold(arg, false),
  };
  for (const [name, run] of Object.entries(commands)) {
    context.subscriptions.push(vscode.commands.registerCommand(name, run as (...args: unknown[]) => unknown));
  }

  // Files as they are in a commit, for diffs and for changes that are not checked out.
  const files: vscode.TextDocumentContentProvider = {
    provideTextDocumentContent: async (uri) => (await (await model.client()).file(uri.query, uri.path.slice(1))) ?? "",
  };

  const poll = setInterval(() => void model.refresh(), Math.max(15, setting("pollSeconds", 120)) * 1000);
  context.subscriptions.push(
    model,
    log,
    overviewTree,
    stackTree,
    changeTree,
    threads,
    statusBar(model),
    vscode.workspace.registerTextDocumentContentProvider(FILE_SCHEME, files),
    // Coming back to the window is when the stack has most likely changed underneath.
    vscode.window.onDidChangeWindowState((state) => (state.focused ? void model.refresh() : undefined)),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("ger.executable")) {
        model.restart();
        void model.refresh();
      }
    }),
    { dispose: () => clearInterval(poll) },
  );
  void model.refresh();
  return { model, overviewView, stackView, changeView };
}

export function deactivate(): void {
  // Disposing the model (a subscription) stops ger serve.
}
