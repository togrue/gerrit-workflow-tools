// Wires the views, the status bar and the commands to one `ger serve`, for the first workspace
// folder in a git checkout.

import * as vscode from "vscode";

import { handToAgent, runAgent } from "./agent";
import { CommentChain, Diff, FileChange } from "./api";
import { ChangeView, FILE_SCHEME, Location, ThreadArg } from "./changeView";
import { CommentThreads } from "./comments";
import { setting } from "./config";
import { EventFollower } from "./events";
import { ShownCommitFollower } from "./follow";
import { plural } from "./format";
import { gerProgram, installGer, runInTerminal } from "./launch";
import { ChangeTarget, Model } from "./model";
import { OverviewView, headlineTarget } from "./overviewView";
import { Poller } from "./poller";
import { RepoWatcher } from "./repoWatcher";
import { checkout, editCommit, leaveReview, review } from "./review";
import { GerProcess } from "./server";
import { CommitArg, StackView } from "./stackView";
import { waitsForYou } from "./state";
import { statusBar } from "./statusBar";
import { Ui, ui, useUi } from "./ui";
import { setUpstream } from "./upstream";
import { staticView } from "./webview";
import { RepoChoice, chooseRepo, gitDirs, repoNote } from "./workspace";

const WEBVIEWS = ["ger.stack", "ger.change"];

/**
 * No workspace folder is in a git checkout. Every command is still there and says so, rather
 * than "command not found"; the views stay empty, and their welcome text offers Open Folder.
 * Adding a folder that is in a checkout starts the extension then, without a reload.
 */
function activateWithoutRepo(context: vscode.ExtensionContext, log: vscode.OutputChannel): void {
  log.appendLine("No workspace folder is in a git checkout: nothing to serve.");
  const waiting: vscode.Disposable[] = [];
  void vscode.commands.executeCommand("setContext", "ger.state", "no-repo");
  const declared: { command: string }[] = context.extension.packageJSON.contributes.commands;
  const missing = () => ui().message("info", "Open a folder with a Gerrit checkout first: ger works on the git repository of a workspace folder.", {}, []);
  for (const { command } of declared) {
    waiting.push(vscode.commands.registerCommand(command, command === "ger.showLog" ? () => log.show() : missing));
  }
  const empty: vscode.TreeDataProvider<never> = { getChildren: () => [], getTreeItem: (node) => node };
  waiting.push(vscode.window.registerTreeDataProvider("ger.overview", empty));
  for (const view of WEBVIEWS) {
    waiting.push(vscode.window.registerWebviewViewProvider(view, staticView("Open a folder with a Gerrit checkout: Review chains says how.")));
  }
  waiting.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      const choice = repoChoice();
      if (choice) {
        // The placeholders make room for the real commands and views.
        waiting.forEach((item) => item.dispose());
        start(context, log, choice);
      }
    }),
  );
  context.subscriptions.push(...waiting);
}

const repoChoice = () => chooseRepo((vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri.fsPath));

/** What the extension hands to its end-to-end tests. */
export interface ExtensionApi {
  model: Model;
  overviewView: OverviewView;
  stackView: StackView;
  changeView: ChangeView;
  threads: CommentThreads;
  statusItem: vscode.StatusBarItem;
  events: EventFollower;
  /** Replace what talks to the user, for the end-to-end suite. */
  useUi(next: Ui): void;
}

export function activate(context: vscode.ExtensionContext): ExtensionApi | undefined {
  const log = vscode.window.createOutputChannel("ger");
  context.subscriptions.push(log);
  const choice = repoChoice();
  if (!choice) {
    activateWithoutRepo(context, log);
    return undefined;
  }
  return start(context, log, choice);
}

function start(context: vscode.ExtensionContext, log: vscode.OutputChannel, choice: RepoChoice): ExtensionApi {
  const repo = choice.repo;
  const note = repoNote(choice);
  log.appendLine(note ?? `Repository: ${repo}`);
  void vscode.commands.executeCommand("setContext", "ger.state", "starting");
  const model = new Model(new GerProcess(repo, gerProgram, (line) => log.appendLine(line)), context.workspaceState, repo);
  const overviewView = new OverviewView(model, context.workspaceState);
  const stackView = new StackView(context.extensionUri, model);
  const changeView = new ChangeView(context.extensionUri, model);

  const overviewTree = vscode.window.createTreeView("ger.overview", { treeDataProvider: overviewView });
  // Kept while hidden: the selection in the stack, and a commit message you are still typing.
  const kept = { webviewOptions: { retainContextWhenHidden: true } };
  const stackPane = vscode.window.registerWebviewViewProvider("ger.stack", stackView, kept);
  const changePane = vscode.window.registerWebviewViewProvider("ger.change", changeView, kept);
  const threads = new CommentThreads(model, changeView, (chain) => changeView.revealThread(chain));

  model.onDidChange(() => {
    const waiting = model.overview?.sections?.find((s) => s.name === "to-review")?.chains.length ?? 0;
    overviewTree.description = model.stale ? "out of date" : undefined;
    overviewTree.badge = waiting ? { value: waiting, tooltip: `${plural(waiting, "chain")} to review` } : undefined;
  });
  let pausedLogged = false;
  const follower = new ShownCommitFollower(model, changeView);
  model.onDidGetNewHeadline(async (headline) => {
    if (setting("notify", true) && (await ui().message("info", headline.text, {}, ["Open"])) === "Open") {
      await vscode.commands.executeCommand("ger.openHeadline");
    }
  });

  /** The change a command acts on: its argument (a target, a tree node or a stack row), else the one shown. */
  const targetOf = (arg: unknown): ChangeTarget | undefined => {
    if (arg && typeof arg === "object" && "rev" in arg) {
      return arg as ChangeTarget;
    }
    return OverviewView.target(arg as Parameters<typeof OverviewView.target>[0]) ?? stackView.target(arg as CommitArg) ?? changeView.target;
  };
  const threadOf = (arg: unknown): CommentChain | undefined => {
    const row = arg as ThreadArg | undefined;
    return row?.kind === "thread" ? row.chain : undefined;
  };

  /** `preserveFocus`: the keyboard stays where it is (walking the stack with the arrow keys). */
  const openChange = async (target: ChangeTarget | undefined, preserveFocus = false) => {
    if (!target) {
      return;
    }
    if (preserveFocus) {
      changeView.show(true);
    } else {
      await vscode.commands.executeCommand("ger.change.focus");
    }
    follower.forget();
    await changeView.open(target);
    if (target.chainKey) {
      await model.markSeen([target.chainKey]);
    }
  };

  stackView.onOpen = openChange;
  changeView.reword = (sha, message, applied) => stackView.reword(sha, message, applied);

  const setHold = async (arg: unknown, held: boolean) => {
    const chain = threadOf(arg);
    if (chain) {
      await (await model.client()).hold(chain.root_id, held);
      await changeView.open();
    }
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    "ger.refresh": async () => {
      if (model.failure || model.stale) {
        // It failed: Retry starts ger serve afresh, and says so.
        await ui().progress(vscode.ProgressLocation.Notification, "Restarting ger serve", () => {
          model.restart();
          return model.refresh({ fromGerrit: true });
        });
      } else {
        // Both views it reloads show that they are loading, whichever one the button is in.
        const title = "Refreshing from Gerrit";
        await ui().progress({ viewId: "ger.overview" }, title, () => ui().progress({ viewId: "ger.stack" }, title, () => model.refresh({ fromGerrit: true })));
      }
      if (model.stale) {
        // Failed again: the views keep the last answer (marked out of date); you asked, so say why.
        void ui().message("warning", `Still out of date: ${model.stale}`, {}, []);
      } else if (model.state === "ready") {
        await changeView.open(); // still failing: the change shown stays as it was, the welcome text says why
      }
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
    "ger.review": (arg: unknown) => review(model, targetOf(arg)),
    "ger.checkout": (arg: unknown) => checkout(model, targetOf(arg)),
    "ger.stack.edit": (arg: unknown) => editCommit(model, targetOf(arg)),
    "ger.setUpstream": (upstream?: unknown) => setUpstream(model, typeof upstream === "string" ? upstream : undefined),
    "ger.reviewDone": () => leaveReview(model),
    "ger.push": () => runInTerminal(repo, ["push"]),
    "ger.setup": () => runInTerminal(repo, ["setup"]),
    "ger.installCli": () => installGer(repo, context.extension.packageJSON),
    "ger.stack.squash": (arg: CommitArg) => stackView.fold(arg, false),
    "ger.stack.squashTakeMessage": (arg: CommitArg) => stackView.fold(arg, true),
    "ger.stack.drop": (arg: CommitArg) => stackView.drop(arg),
    "ger.stack.copyChangeId": (arg: CommitArg) => stackView.copy(arg, "change-id"),
    "ger.stack.copyHash": (arg: CommitArg) => stackView.copy(arg, "hash"),
    "ger.sortChains": () => overviewView.chooseSort(),
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

  // Gerrit's event stream, when ger serve has one: a refresh on news, and only a slow poll.
  const events = new EventFollower(model, () => void model.refresh({ background: true }), (line) => log.appendLine(line));
  const statusItem = statusBar(model, events, note);
  // Local git (a commit, a checkout, a rebase) is news Gerrit's events do not carry. Not news to notify about: you did it.
  const dirs = gitDirs(repo);
  const localGit = dirs
    ? new RepoWatcher(dirs, (lastChange) => {
        // A refresh that began after the last change (your own stack edit refreshes itself) already knows.
        if (!waitsForYou(model.state) && !model.loadedSince(lastChange)) {
          void model.refresh();
        }
      })
    : undefined;
  context.subscriptions.push(
    model,
    overviewTree,
    stackPane,
    changePane,
    threads,
    follower,
    statusItem,
    vscode.workspace.registerTextDocumentContentProvider(FILE_SCHEME, files),
    // Nothing starts in Restricted Mode (see Model.load): trusting the folder starts it.
    vscode.workspace.onDidGrantWorkspaceTrust(() => void model.refresh()),
    events,
    ...(localGit ? [localGit] : []),
    new Poller(() => void model.refresh({ background: true }), events, () => {
      // Not again and again: ger serve would start and fail each tick. Regaining focus, Retry and a changed setting still try.
      const paused = waitsForYou(model.state);
      if (paused && !pausedLogged) {
        log.appendLine(`Background refresh paused: ${model.failure ?? model.state}. Retry, or fix the setting, to try again.`);
      }
      pausedLogged = paused;
      return paused;
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("ger.executable")) {
        model.restart();
        void model.refresh();
      }
    }),
  );
  void model.refresh();
  return { model, overviewView, stackView, changeView, threads, statusItem, events, useUi };
}

export function deactivate(): void {
  // Disposing the model (a subscription) stops ger serve.
}
