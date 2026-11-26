// Wires the views, the status bar and the commands to one `ger serve`, for the first workspace
// folder in a git checkout.

import * as vscode from "vscode";

import { handToAgent, runAgent } from "./agent";
import { CommentChain, Diff, FileChange, Health } from "./api";
import { sameChange } from "./changeLoad";
import { NextKind, pushable } from "./nextAction";
import { titleOffers } from "./stackRows";
import { gerritOffers } from "./changeScreen";
import { ChangeView, Location, ThreadArg } from "./changeView";
import { draftsIn } from "./changeRows";
import { CommentLink, commentDecorations } from "./commentHover";
import { DiffTabs, FILE_SCHEME, filePath } from "./diffTabs";
import { CommentThreads, ThreadComment, closeComment, leaveCommentBox } from "./comments";
import { setting } from "./config";
import { EventFollower } from "./events";
import { ShownCommitFollower } from "./follow";
import { plural } from "./format";
import { GER_TERMINAL, configWarner, gerProgram, installGer, runInTerminal, versionSkewWarner } from "./launch";
import { ChangeTarget, Model } from "./model";
import { OverviewView, headlineTarget } from "./overviewView";
import { Poller } from "./poller";
import { PROBLEM_ACTIONS, gerritProblem } from "./problemRows";
import { openConflict, push, rebaseOntoUpstream, rebaseStep } from "./rebase";
import { RepoWatcher } from "./repoWatcher";
import { reportIssue } from "./reportIssue";
import { checkout, editCommit, leaveReview, review, stepReview, updateReview } from "./review";
import { reviewWalk } from "./reviewWalk";
import { waitsOnYou } from "./rows";
import { GerProcess } from "./server";
import { CommitArg, StackView, commitTarget } from "./stackView";
import { waitsForYou } from "./state";
import { statusBar } from "./statusBar";
import { Task } from "./task";
import { activeTracer } from "./trace";
import { startTracing, tracedCommand, tracedTree, tracedUi } from "./tracing";
import { Ui, feedback, offerWaysOn, onTerminalCommandEnd, saveReplyResolved, ui, useUi, vscodeUi } from "./ui";
import { setUpstream } from "./upstream";
import { voteAndPublish } from "./vote";
import { AttentionWalk } from "./walk";
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
  // What works without a checkout: the log, and a report that says the extension did not start.
  const works: Record<string, () => unknown> = {
    "ger.showLog": () => log.show(),
    "ger.reportIssue": () => reportIssue(context.extension.packageJSON, undefined, "no-repo"),
  };
  for (const { command } of declared) {
    waiting.push(vscode.commands.registerCommand(command, works[command] ?? missing));
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
  task: Task;
  threads: CommentThreads;
  statusItem: vscode.StatusBarItem;
  events: EventFollower;
  diffTabs: DiffTabs;
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
  const logLine = (line: string) => log.appendLine(line);
  const skew = versionSkewWarner(repo, context.extension.packageJSON, context.globalState, logLine);
  const warnings = configWarner(repo, logLine);
  const served = (health: Health) => {
    skew(health);
    warnings(health);
  };
  // Before anything asks ger serve: the first requests are traced too. Off, nothing is wrapped.
  context.subscriptions.push(startTracing(logLine));
  const showTo = (next: Ui) => useUi(activeTracer() ? tracedUi(next) : next);
  showTo(vscodeUi);
  const model = new Model(new GerProcess(repo, gerProgram, logLine, served), context.workspaceState, repo);
  const overviewView = new OverviewView(model, context.workspaceState);
  const stackView = new StackView(context.extensionUri, model);
  const changeView = new ChangeView(context.extensionUri, model, context.globalState);
  context.subscriptions.push(changeView.ciProblems);
  const task = new Task(model);
  // The task header is drawn where the task is worked, the Change view, in both modes; Local stack,
  // while shown beside it (All chains), leaves out its rebase banner: Continue and Abort are said once.
  changeView.task = () => task.vm();
  stackView.task = () => (task.mode === "rework" ? task.vm() : undefined);
  // Out of a task (the rebase over, a push), Local stack says how the last step ended.
  stackView.outcome = () => task.outcome();
  changeView.taskStep = (key) => (task.mode ? task.go(task.mode, key) : undefined);
  task.shown = () => changeView.shownCounts(task.mode);
  task.onDidChange(() => {
    changeView.render();
    stackView.render();
  });

  const overviewTree = vscode.window.createTreeView("ger.overview", { treeDataProvider: activeTracer() ? tracedTree(overviewView) : overviewView });
  // Kept while hidden: the selection in the stack, and a commit message you are still typing.
  const kept = { webviewOptions: { retainContextWhenHidden: true } };
  const stackPane = vscode.window.registerWebviewViewProvider("ger.stack", stackView, kept);
  const changePane = vscode.window.registerWebviewViewProvider("ger.change", changeView, kept);
  const threads = new CommentThreads(model, changeView, (chain) => changeView.revealThread(chain));

  model.onDidChange(() => {
    // What waits on your review: the chains whose turn is yours, not those waiting on their author.
    const waiting = model.overview?.sections?.find((s) => s.name === "to-review")?.chains.filter((chain) => waitsOnYou(chain, "to-review")).length ?? 0;
    overviewTree.description = model.stale ? "out of date" : undefined;
    // Over the welcome text, which cannot say which versions: this can. (A message hides the welcome text and its
    // buttons, so setup wanting says its cause there by `ger.setupReason`, and ger's own words in the other views.)
    overviewTree.message = model.incompatible?.message;
    overviewTree.badge = waiting ? { value: waiting, tooltip: `${plural(waiting, "chain")} to review` } : undefined;
    // Local stack's title offers Work on Next and Push only where they would do something (P6).
    const offers = titleOffers(model.overview?.local, pushable);
    void vscode.commands.executeCommand("setContext", "ger.canWorkOnNext", offers.workOnNext);
    void vscode.commands.executeCommand("setContext", "ger.canPush", offers.push);
  });
  let pausedLogged = false;
  // What the Change view shows and why (shownChange.ts): the review's change, a rebase's stop (the keyboard stays
  // where it is), your selection. openChange and the review walk are defined below.
  let showReviewNow: () => Promise<unknown> = async () => undefined;
  const follower = new ShownCommitFollower(model, changeView, { stop: (commit) => openChange(commitTarget(commit), true), review: () => showReviewNow() });
  const diffTabs = new DiffTabs(model, (line) => log.appendLine(line));
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
    return OverviewView.target(arg as Parameters<typeof OverviewView.target>[0], model.stack) ?? stackView.target(arg as CommitArg) ?? changeView.target;
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
  threads.showHead = async () => {
    const head = model.stack?.commits.find((commit) => commit.is_head);
    if (head) {
      await openChange(commitTarget(head), true);
    }
  };
  /** Work on Next stopped at `commit`: shown, the keyboard on its first comment to answer, else its first CI finding. */
  const walk = new AttentionWalk(model, async (commit) => {
    await openChange(commitTarget(commit), true);
    await changeView.idle();
    if (changeView.toAnswer().length) {
      await threads.step(1);
    } else if (commit.attention_reasons.includes("ci-failed")) {
      await changeView.openFirstFinding();
    }
  });
  walk.hooks = { pressed: () => task.pressed(), refused: (why) => task.refused(why) };
  changeView.reword = (sha, message, applied) => stackView.reword(sha, message, applied);
  changeView.showThread = (chain) => threads.show(chain);
  changeView.visited = () => threads.visitedHere();
  /** The change shown, when `target` is it and its `/show` has come: what hovers read instead of asking again. */
  const shownAs = (target: ChangeTarget) => (changeView.target && changeView.shown && sameChange(changeView.target, target) ? changeView : undefined);
  overviewView.comments = stackView.comments = {
    chains: async (target) => shownAs(target)?.chains() ?? (await (await model.client()).show([target.rev], { comments: "all" }))[0]?.comment_chains,
    drafts: (target) => {
      const shown = shownAs(target);
      return shown ? draftsIn(shown.chains()) : 0;
    },
  };
  // What the Change view learns of the change shown (your drafts on it) colours its Review chains row.
  context.subscriptions.push(
    changeView.onDidLoad(() => {
      overviewView.redraw();
      stackView.render();
    }),
  );
  stackView.drafts = (sha) => {
    const shown = changeView.shown?.sha === sha || changeView.target?.rev === sha ? changeView : undefined;
    return shown?.shown ? draftsIn(shown.chains()) : 0;
  };
  /** The chain a hover's link names, the change shown first (the keyboard stays where it is). */
  const chainOf = async ({ target, root }: CommentLink) => {
    if (!shownAs(target)) {
      await openChange(target, true);
    }
    await changeView.idle();
    return changeView.chains().find((chain) => chain.root_id === root);
  };

  /** Copy the Change-Ids or the SHAs of what a command was invoked on: one per line, newest first. */
  const copyIds = async (arg: unknown, what: "changeId" | "sha") => {
    const fromStack = stackView.targets(arg as CommitArg);
    const one = fromStack.length ? [] : [targetOf(arg)];
    const values = [...fromStack, ...one].map((target) => target?.[what]).filter((value): value is string => Boolean(value));
    if (!values.length) {
      return;
    }
    await ui().copy(values.join("\n"));
    const noun = what === "sha" ? "SHA" : "Change-Id";
    feedback(`Copied ${values.length === 1 ? `${noun} ${values[0].slice(0, 12)}…` : `${values.length} ${noun}s`}`);
  };

  // Vote and Publish is the reviewer's: offered in the Change view for a change on Gerrit that is not yours.
  context.subscriptions.push(
    changeView.onDidLoad(() => {
      // Not on a closed change (Gerrit takes no vote there), nor on one that could not be read.
      const offers = gerritOffers(changeView.target ? changeView.shown : undefined, changeView.failed);
      void vscode.commands.executeCommand("setContext", "ger.changeOnGerrit", offers.onGerrit);
      void vscode.commands.executeCommand("setContext", "ger.changeVotable", offers.votable);
    }),
  );
  /** What was published shows at once: Review chains (it may leave *To review*), the change and its threads. */
  const afterVote = async () => {
    await model.refresh({ fromGerrit: true });
    await changeView.open();
  };
  const walking = reviewWalk({
    model,
    changeView,
    task,
    open: (target) => openChange(target, true),
    hold: (run) => follower.hold(run),
    voteChain: (number) => {
      const shown = changeView.target?.number === number ? changeView.target : { rev: `change:${number}`, title: String(number), number, local: false };
      return voteAndPublish(model, shown, { chain: true, after: afterVote });
    },
  });
  const { walk: reviewSteps, show: showReview } = walking;
  /** A click on a change in the task header: reviewing, that change; reworking, that commit shown. */
  task.go = (mode, key) => {
    if (mode === "review") {
      return walking.go(Number(key));
    }
    const commit = model.stack?.commits.find((c) => c.sha === key);
    return commit ? openChange(commitTarget(commit), true) : undefined;
  };
  /**
   * The review here moved, from wherever (a shell's `ger review`, a restart): its change is shown. A
   * window opened on a staged review (Open Review Worktree, or a reload there) opens on it the first
   * time: the Gerrit view, and in a window with no editor open yet, all its files.
   */
  let greeted = false;
  showReviewNow = () => {
    if (greeted) {
      return showReview(false);
    }
    greeted = true;
    const fresh = vscode.window.tabGroups.all.every((group) => group.tabs.length === 0);
    return Promise.resolve(vscode.commands.executeCommand("workbench.view.extension.ger")).then(() => showReview(fresh));
  };
  /**
   * After a vote on the change a staged review holds: Next Change while the chain goes on above it,
   * Leave Review at its top (`ger review`'s own `next:` line, the vote done). Nothing elsewhere.
   */
  const nextAfterVote = (target: ChangeTarget | undefined) => async () => {
    const review = model.stack?.staged_review;
    if (!review || review.number !== target?.number) {
      return undefined;
    }
    const chain = (await (await model.client()).review(true))?.chain ?? [];
    const index = chain.indexOf(review.number);
    if (index < 0) {
      return undefined;
    }
    return index < chain.length - 1
      ? { label: "Next Change", run: () => stepReview(model, 1, reviewSteps) }
      : { label: "Leave Review", run: () => leaveReview(model, reviewSteps) };
  };
  /**
   * Continue, Abort or Skip in the ger terminal. What the last step said is history from the press;
   * the header says the step runs only once it was sent (its question answered), until it ends; a
   * refusal is said in the header at once.
   */
  const stepInTerminal = async (step: "continue" | "abort" | "skip", text: string) => {
    task.pressed();
    return rebaseStep(model, step, { started: () => task.busyUntilMoved(text), refused: (why) => task.refused(why) });
  };
  /** A chain row of Review chains votes on its whole chain. */
  const isChainRow = (arg: unknown) => (arg as { kind?: unknown } | undefined)?.kind === "chain";

  const setHold = async (arg: unknown, held: boolean) => {
    const chain = threadOf(arg);
    if (chain) {
      await (await model.client()).hold(chain.root_id, held);
      feedback(held ? "Kept for you: the agent leaves this comment alone" : "Left to the agent");
      await changeView.open();
    }
  };

  /** What Next does, by what the Change view's button names. */
  const next: Record<NextKind, () => unknown> = {
    comment: () => threads.stepUnread(),
    file: () => changeView.openNextFile(),
    seen: () => changeView.markAllSeen(),
    vote: () => vscode.commands.executeCommand("ger.vote"),
    nextChange: () => stepReview(model, 1, reviewSteps),
    leave: () => leaveReview(model, reviewSteps),
    update: () => vscode.commands.executeCommand("ger.reviewUpdate"),
    conflict: () => {
      const first = model.stack?.rebase?.unmerged[0];
      return first ? openConflict(model, first) : undefined;
    },
    continue: () => vscode.commands.executeCommand("ger.rebase.continue"),
    workOnNext: () => walk.next(),
    push: () => {
      task.pressed();
      return push(model);
    },
    review: () => review(model, changeView.target, reviewSteps),
    reviewWindow: () => {
      const worktree = model.reviewElsewhere(changeView.target?.number);
      return worktree ? ui().openFolder(vscode.Uri.file(worktree)) : undefined;
    },
    gerrit: () => {
      const away = changeView.shown?.gerrit_unavailable;
      const fix = away ? gerritProblem(away).actions[0] : PROBLEM_ACTIONS.retry;
      return vscode.commands.executeCommand(fix.command.id, ...fix.command.args);
    },
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    "ger.refresh": async () => {
      const retried = Boolean(model.failure || model.stale);
      if (retried) {
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
        void offerWaysOn("warning", `Still out of date: ${model.stale}`, [PROBLEM_ACTIONS.retry, PROBLEM_ACTIONS.log]);
      } else if (model.failure) {
        // You asked, and it cannot show anything: say what it found this time, not only that it tried.
        const ways = model.state === "setup" ? [PROBLEM_ACTIONS.address, PROBLEM_ACTIONS.retry] : [PROBLEM_ACTIONS.retry, PROBLEM_ACTIONS.log];
        void offerWaysOn("warning", `${retried ? "Still not working" : "Refresh failed"}: ${model.failure}`, ways);
      } else if (model.state === "ready") {
        const away = model.gerritAway();
        if (away) {
          // The local stack was read again, Gerrit was not: said as a failure, never a tick. The views
          // say it with the ways on (Check Credentials, Retry): a toast would offer them a second time.
          feedback(`Not refreshed from Gerrit: ${away.message}`, { icon: "warning" });
        } else {
          feedback("Refreshed from Gerrit");
        }
        await changeView.open(); // still failing: the change shown stays as it was, the welcome text says why
      }
    },
    "ger.showLog": () => log.show(),
    "ger.reportIssue": () => reportIssue(context.extension.packageJSON, { model, events, overviewView, stackView, changeView }, model.state),
    "ger.openHeadline": async () => {
      // A rebase underway is finished in Local stack, where it shows where it stopped.
      if (model.overview?.headline?.section === "local") {
        if (task.mode === "review") {
          task.showAll(); // something else needs you: the lists the task header hides come back
        }
        await vscode.commands.executeCommand("ger.stack.focus");
        return;
      }
      const target = headlineTarget(model);
      if (!target && task.focused) {
        task.showAll();
      }
      await (target ? openChange(target) : vscode.commands.executeCommand("ger.overview.focus"));
    },
    "ger.markSeen": async () => {
      await model.markSeen();
      feedback("All activity marked as seen");
    },
    "ger.openChange": (arg: unknown) => openChange(targetOf(arg)),
    "ger.change.reload": () => changeView.open(),
    "ger.openInGerrit": (arg: unknown) => {
      const target = targetOf(arg);
      const url = target?.url ?? changeView.shown?.gerrit_url;
      if (url) {
        feedback(`Opened ${target?.number ?? "the change"} in the browser`);
        return ui().openExternal(vscode.Uri.parse(url));
      }
      return undefined;
    },
    "ger.review": (arg: unknown) => review(model, targetOf(arg), reviewSteps),
    "ger.checkout": (arg: unknown) => checkout(model, targetOf(arg)),
    "ger.stack.edit": (arg: unknown) => editCommit(model, targetOf(arg)),
    "ger.setUpstream": (upstream?: unknown) => setUpstream(model, typeof upstream === "string" ? upstream : undefined),
    "ger.reviewDone": () => leaveReview(model, reviewSteps),
    "ger.vote": (arg: unknown) => {
      const target = targetOf(arg);
      return voteAndPublish(model, target, { chain: isChainRow(arg), after: afterVote, next: isChainRow(arg) ? undefined : nextAfterVote(target) });
    },
    "ger.reviewUpdate": () => updateReview(model, reviewSteps),
    "ger.reviewNext": () => stepReview(model, 1, reviewSteps),
    "ger.reviewPrev": () => stepReview(model, -1, reviewSteps),
    "ger.publishDrafts": (arg: unknown) => voteAndPublish(model, targetOf(arg), { draftsOnly: true, after: afterVote }),
    "ger.push": () => {
      task.pressed(); // what the last step said is history: the push says its own outcome
      return push(model);
    },
    "ger.rebase.continue": async () => {
      if (await stepInTerminal("continue", "Continuing the rebase in the ger terminal…")) {
        walk.continued();
      }
    },
    "ger.workOnNext": () => walk.next(),
    "ger.rebase.abort": () => stepInTerminal("abort", "Aborting the rebase in the ger terminal…"),
    "ger.rebase.skip": () => stepInTerminal("skip", "Skipping the commit in the ger terminal…"),
    "ger.rebase.upstream": () => rebaseOntoUpstream(model),
    "ger.setup": () => {
      runInTerminal(repo, ["setup"]);
      feedback("ger setup runs in the ger terminal", { icon: "terminal" });
    },
    "ger.installCli": () => installGer(repo, context.extension.packageJSON),
    "ger.stack.squash": (arg: CommitArg) => stackView.fold(arg, false),
    "ger.stack.squashTakeMessage": (arg: CommitArg) => stackView.fold(arg, true),
    "ger.stack.drop": (arg: CommitArg) => stackView.drop(arg),
    "ger.copyChangeId": (arg: unknown) => copyIds(arg, "changeId"),
    "ger.copySha": (arg: unknown) => copyIds(arg, "sha"),
    "ger.sortChains": () => overviewView.chooseSort(),
    "ger.openDiff": (file: FileChange, diff?: Diff) => changeView.openDiff(file, diff),
    "ger.openAllChanges": async (arg: unknown) => {
      // A row's change is shown first (the keyboard stays where it is), then all its files in one editor.
      const target = arg === undefined ? changeView.target : targetOf(arg);
      if (target && !(changeView.target && sameChange(changeView.target, target))) {
        await openChange(target, true);
      }
      await changeView.idle();
      await changeView.openAll();
    },
    "ger.openLocation": (location: Location) => changeView.openLocation(location),
    "ger.next": () => {
      const action = changeView.next();
      return action ? next[action.kind]() : undefined;
    },
    "ger.showAllChains": () => task.showAll(),
    "ger.focusReview": () => task.focus(),
    "ger.comment.goto": async (link: CommentLink) => {
      const chain = await chainOf(link);
      return chain ? threads.goTo(chain) : undefined;
    },
    "ger.comment.done": async (link: CommentLink) => {
      const chain = await chainOf(link);
      return chain ? threads.replyToChain(chain, true) : undefined;
    },
    "ger.comment.apply": async (link: CommentLink) => {
      const chain = await chainOf(link);
      return chain ? threads.applySuggestion(chain) : undefined;
    },
    "ger.nextComment": () => threads.step(1),
    "ger.prevComment": () => threads.step(-1),
    "ger.reply": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.replyToChain(chain, false) : undefined;
    },
    "ger.replySaveResolved": () => saveReplyResolved(),
    "ger.replyDone": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.replyToChain(chain, true) : undefined;
    },
    "ger.comment.saveDraft": (reply: vscode.CommentReply) => threads.saveNew(reply, "draft"),
    "ger.comment.saveLocalDraft": (reply: vscode.CommentReply) => threads.saveNew(reply, "local-draft"),
    "ger.comment.saveNote": (reply: vscode.CommentReply) => threads.saveNew(reply, "note"),
    "ger.comment.add": (arg?: { lineNumber?: number }) => threads.add(typeof arg?.lineNumber === "number" ? arg.lineNumber - 1 : undefined),
    "ger.comment.cancelNew": (reply: vscode.CommentReply) => threads.cancelNew(reply),
    "ger.comment.suggest": () => threads.suggest(),
    "ger.comment.applySuggestion": (comment: ThreadComment) => threads.applyInThread(comment),
    "ger.applySuggestion": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.applySuggestion(chain) : undefined;
    },
    "ger.comment.file": (file: string) => threads.commentOnFile(file),
    "ger.comment.edit": (comment: ThreadComment) => threads.startEdit(comment),
    "ger.comment.save": (comment: ThreadComment) => threads.saveEdit(comment),
    "ger.comment.cancel": (comment: ThreadComment) => threads.cancelEdit(comment),
    "ger.comment.delete": (comment: ThreadComment) => threads.discardInThread(comment),
    // Discard in the box of a comment you are editing, as on Gerrit: the trash in its title, by another name.
    "ger.comment.discard": (comment: ThreadComment) => threads.discardInThread(comment),
    "ger.comment.escape": () => leaveCommentBox(),
    "ger.comment.close": () => closeComment(),
    "ger.chain.edit": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.editChain(chain) : undefined;
    },
    "ger.chain.delete": (arg: unknown) => {
      const chain = threadOf(arg);
      return chain ? threads.discardChain(chain) : undefined;
    },
    "ger.thread.reply": (reply: vscode.CommentReply) => threads.replyInThread(reply),
    "ger.thread.done": (reply: vscode.CommentReply) => threads.doneInThread(reply),
    "ger.thread.cancel": (reply: vscode.CommentReply) => threads.cancelReply(reply),
    "ger.thread.markResolved": (thread: vscode.CommentThread) => threads.setResolved(thread, true),
    "ger.thread.markUnresolved": (thread: vscode.CommentThread) => threads.setResolved(thread, false),
    "ger.agentBrief": (arg: unknown) => handToAgent(model, targetOf(arg)),
    "ger.agentWalk": () => runAgent(model, undefined),
    "ger.hold": (arg: unknown) => setHold(arg, true),
    "ger.release": (arg: unknown) => setHold(arg, false),
  };
  for (const [name, run] of Object.entries(commands)) {
    context.subscriptions.push(vscode.commands.registerCommand(name, tracedCommand(name, run as (...args: unknown[]) => unknown)));
  }

  // Files as they are in a commit, for diffs and for changes that are not checked out.
  const files: vscode.TextDocumentContentProvider = {
    provideTextDocumentContent: async (uri) => (await (await model.client()).file(uri.query, filePath(uri, repo))) ?? "",
  };

  // Gerrit's event stream, when ger serve has one: a refresh on news, and only a slow poll.
  const events = new EventFollower(model, () => void model.refresh({ background: true }), (line) => log.appendLine(line));
  const statusItem = statusBar(model, events, note);
  // Local git (a commit, a checkout, a rebase) is news Gerrit's events do not carry. Not news to notify about: you did it.
  const dirs = gitDirs(repo);
  const localGit = dirs
    ? new RepoWatcher(dirs, repo, {
        changed: (lastChange) => {
          // A refresh that began after the last change (your own stack edit refreshes itself) already knows.
          if (!waitsForYou(model.state) && !model.loadedSince(lastChange)) {
            void model.refresh();
          }
        },
        // What a stop or a review says of the files (changed, not amended; staged): the stack alone, one request.
        treeChanged: (lastChange) => (model.state === "ready" && !model.loadedSince(lastChange) ? void model.refreshLocal() : undefined),
        following: () => Boolean(model.stack?.rebase || model.stack?.staged_review),
      })
    : undefined;
  // A command of ours ended in the ger terminal: look at once (the step outcome, a refusal that moved nothing).
  const terminalEnded = onTerminalCommandEnd((name) => {
    if (name === GER_TERMINAL && model.state === "ready") {
      void model.refreshLocal();
    }
  });
  context.subscriptions.push(
    terminalEnded,
    model,
    overviewTree,
    stackPane,
    changePane,
    task,
    walking.disposable,
    threads,
    follower,
    walk,
    diffTabs,
    statusItem,
    vscode.workspace.registerTextDocumentContentProvider(FILE_SCHEME, files),
    vscode.window.registerFileDecorationProvider(commentDecorations()),
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
  return { model, overviewView, stackView, changeView, task, threads, statusItem, events, diffTabs, useUi: showTo };
}

export function deactivate(): void {
  // Disposing the model (a subscription) stops ger serve.
}
