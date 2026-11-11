// Wires the views, the status bar and the commands to one `ger serve`, for the first workspace
// folder in a git checkout.

import * as vscode from "vscode";

import { handToAgent, runAgent } from "./agent";
import { CommentChain, Diff, FileChange } from "./api";
import { ChangeView, FILE_SCHEME, Location, Node as ChangeNode } from "./changeView";
import { CommentThreads } from "./comments";
import { setting } from "./config";
import { ShownCommitFollower } from "./follow";
import { plural } from "./format";
import { gerProgram, runInTerminal } from "./launch";
import { ChangeTarget, Model } from "./model";
import { OverviewView, headlineTarget } from "./overviewView";
import { Poller } from "./poller";
import { checkout, editCommit, leaveReview } from "./review";
import { GerProcess } from "./server";
import { StackView } from "./stackView";
import { statusBar } from "./statusBar";
import { Ui, ui, useUi } from "./ui";
import { setUpstream } from "./upstream";
import { chooseRepo, repoNote } from "./workspace";

const VIEWS = ["ger.overview", "ger.stack", "ger.change"];

/**
 * No workspace folder is in a git checkout. Every command is still there and says so, rather
 * than "command not found"; the views stay empty, and their welcome text offers Open Folder.
 */
function activateWithoutRepo(context: vscode.ExtensionContext, log: vscode.OutputChannel): void {
  log.appendLine("No workspace folder is in a git checkout: nothing to serve.");
  void vscode.commands.executeCommand("setContext", "ger.state", "no-repo");
  const declared: { command: string }[] = context.extension.packageJSON.contributes.commands;
  const missing = () => ui().message("info", "Open a folder with a Gerrit checkout first: ger works on the git repository of a workspace folder.", {}, []);
  for (const { command } of declared) {
    context.subscriptions.push(vscode.commands.registerCommand(command, command === "ger.showLog" ? () => log.show() : missing));
  }
  const empty: vscode.TreeDataProvider<never> = { getChildren: () => [], getTreeItem: (node) => node };
  for (const view of VIEWS) {
    context.subscriptions.push(vscode.window.registerTreeDataProvider(view, empty));
  }
}

/** What the extension hands to its end-to-end tests. */
export interface ExtensionApi {
  model: Model;
  overviewView: OverviewView;
  stackView: StackView;
  changeView: ChangeView;
  threads: CommentThreads;
  statusItem: vscode.StatusBarItem;
  /** Replace what talks to the user, for the end-to-end suite. */
  useUi(next: Ui): void;
}

export function activate(context: vscode.ExtensionContext): ExtensionApi | undefined {
  const log = vscode.window.createOutputChannel("ger");
  context.subscriptions.push(log);
  const choice = chooseRepo((vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri.fsPath));
  if (!choice) {
    activateWithoutRepo(context, log);
    return undefined;
  }
  const repo = choice.repo;
  const note = repoNote(choice);
  log.appendLine(note ?? `Repository: ${repo}`);
  void vscode.commands.executeCommand("setContext", "ger.state", "starting");
  const model = new Model(new GerProcess(repo, gerProgram, (line) => log.appendLine(line)), context.workspaceState, repo);
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
  const follower = new ShownCommitFollower(model, changeView);
  model.onDidGetNewHeadline(async (headline) => {
    if (setting("notify", true) && (await ui().message("info", headline.text, {}, ["Open"])) === "Open") {
      await vscode.commands.executeCommand("ger.openHeadline");
    }
  });
  changeView.onDidLoad(() => {
    const number = changeView.shown?.number ?? changeView.target?.number;
    changeTree.title = number ? `Change ${number}` : "Change";
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
    follower.forget();
    await changeView.open(target);
    if (target.chainKey) {
      await model.markSeen([target.chainKey]);
    }
  };

  const setHold = async (arg: unknown, held: boolean) => {
    const chain = threadOf(arg);
    if (chain) {
      await (await model.client()).hold(chain.root_id, held);
      await changeView.open();
    }
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    "ger.refresh": async () => {
      if (model.failure) {
        model.restart();
      }
      await model.refresh({ fromGerrit: true });
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
      return url ? ui().openExternal(vscode.Uri.parse(url)) : undefined;
    },
    "ger.checkout": (arg: unknown) => checkout(model, targetOf(arg)),
    "ger.stack.edit": (arg: unknown) => editCommit(model, targetOf(arg)),
    "ger.setUpstream": (upstream?: unknown) => setUpstream(model, typeof upstream === "string" ? upstream : undefined),
    "ger.reviewDone": () => leaveReview(model),
    "ger.push": () => runInTerminal(repo, ["push"]),
    "ger.setup": () => runInTerminal(repo, ["setup"]),
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
      return chain ? threads.replyToChain(chain, false) : undefined;
    },
    "ger.replyDone": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.replyToChain(chain, true) : undefined;
    },
    "ger.comment.saveDraft": (reply: vscode.CommentReply) => threads.saveNew(reply, "draft"),
    "ger.comment.saveLocalDraft": (reply: vscode.CommentReply) => threads.saveNew(reply, "local-draft"),
    "ger.comment.saveNote": (reply: vscode.CommentReply) => threads.saveNew(reply, "note"),
    "ger.thread.reply": (reply: vscode.CommentReply) => threads.replyInThread(reply, undefined),
    "ger.thread.resolve": (reply: vscode.CommentReply) => threads.replyInThread(reply, true),
    "ger.agentBrief": (arg: unknown) => handToAgent(model, targetOf(arg)),
    "ger.agentWalk": () => runAgent(model, undefined),
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

  const statusItem = statusBar(model, note);
  context.subscriptions.push(
    model,
    overviewTree,
    stackTree,
    changeTree,
    threads,
    follower,
    statusItem,
    vscode.workspace.registerTextDocumentContentProvider(FILE_SCHEME, files),
    // Nothing starts in Restricted Mode (see Model.load): trusting the folder starts it.
    vscode.workspace.onDidGrantWorkspaceTrust(() => void model.refresh()),
    new Poller(() => void model.refresh({ background: true })),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("ger.executable")) {
        model.restart();
        void model.refresh();
      }
    }),
  );
  void model.refresh();
  return { model, overviewView, stackView, changeView, threads, statusItem, useUi };
}

export function deactivate(): void {
  // Disposing the model (a subscription) stops ger serve.
}
