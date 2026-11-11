// Runs inside a real VS Code (see run.ts): activates the extension against fake_ger.py and
// presses every button a user can press, in an order a user might. For each press it writes
// down what the user expects, what the extension said and did (through the scripted Ui), and
// how the views changed. That transcript goes to GER_E2E_OUT; run.ts compares it with the
// golden file, so any change in behaviour is a visible diff to judge against the expectation.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import type { CommentChain } from "../../src/api";
import { fileUri } from "../../src/changeView";
import type { ExtensionApi } from "../../src/extension";
import { gerCommandLine } from "../../src/launch";
import { Answer, ScriptedUi, lineDiff } from "./scriptedUi";

type Provider<T> = vscode.TreeDataProvider<T>;

function text(value: string | vscode.TreeItemLabel | boolean | undefined): string {
  return typeof value === "string" ? value : typeof value === "object" ? value.label : "";
}

/** The tree as indented lines: `label — description  [contextValue]`. */
async function dump<T>(provider: Provider<T>, node?: T, depth = 0): Promise<string[]> {
  const lines: string[] = [];
  for (const child of (await provider.getChildren(node)) ?? []) {
    const item = await provider.getTreeItem(child);
    const description = text(item.description);
    const context = item.contextValue ? `  [${item.contextValue}]` : "";
    lines.push(`${"  ".repeat(depth)}${text(item.label)}${description ? ` — ${description}` : ""}${context}`);
    lines.push(...(await dump(provider, child, depth + 1)));
  }
  return lines;
}

async function find<T>(provider: Provider<T>, wanted: string | RegExp, node?: T): Promise<T | undefined> {
  for (const child of (await provider.getChildren(node)) ?? []) {
    const label = text((await provider.getTreeItem(child)).label);
    if (typeof wanted === "string" ? label === wanted : wanted.test(label)) {
      return child;
    }
    const below = await find(provider, wanted, child);
    if (below) {
      return below;
    }
  }
  return undefined;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Whether `condition` came true within `ms`. */
async function until(condition: () => boolean, ms = 15_000): Promise<boolean> {
  for (const deadline = Date.now() + ms; !condition() && Date.now() < deadline; ) {
    await wait(100);
  }
  return condition();
}

/** What stays the same from run to run: no commit ids, temp paths or ages. */
function normalizer(repo: string): (line: string) => string {
  const roots = [repo, repo.replaceAll("\\", "/"), os.tmpdir(), os.tmpdir().replaceAll("\\", "/")];
  // The fake stands in for `ger`: show the command a user would see run.
  const executable = vscode.workspace.getConfiguration("ger").get<string>("executable", "ger");
  const commandLine = gerCommandLine([]);
  return (line) => {
    let out = line.split(executable).join("ger").split(commandLine).join("ger");
    for (const [i, root] of roots.entries()) {
      out = out.split(root).join(i < 2 ? "<repo>" : "<tmp>");
      out = out.split(root.toLowerCase()).join(i < 2 ? "<repo>" : "<tmp>");
    }
    return out
      .replace(/ger-(runbook|brief)-[\w.-]+\.md/g, "ger-$1-<id>.md")
      .replace(/<tmp>[\\/]ger-e2e-\w+[\\/]repo/g, "<repo>")
      .replace(/\b[0-9a-f]{7,40}\b/g, "<sha>")
      .replace(/\b\d+[mhd]\b/g, "<age>");
  };
}

export async function run(): Promise<void> {
  const out: string[] = [];
  const repo = vscode.workspace.workspaceFolders?.[0].uri.fsPath ?? "";
  const normalize = normalizer(repo);
  const ui = new ScriptedUi(normalize);
  const problems: string[] = [];
  try {
    const extension = vscode.extensions.getExtension<ExtensionApi>("gerrit-workflow-tools.ger-vscode");
    assert.ok(extension, "the extension is installed in the development host");
    const api = await extension.activate();
    assert.ok(api, "the extension activated on the workspace folder");
    api.useUi(ui);
    await api.model.refresh();
    assert.equal(api.model.failure, undefined, "ger serve started and answered");
    assert.ok(await until(() => api.events.live), "fake_ger.py's event stream is live");

    // Every command the manifest offers is registered.
    const declared: string[] = extension.packageJSON.contributes.commands.map((c: { command: string }) => c.command);
    const registered = new Set(await vscode.commands.getCommands(true));
    assert.deepEqual(declared.filter((command) => !registered.has(command)), []);

    const editor = (): string => {
      const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
      if (!tab) {
        return "(none)";
      }
      const input = tab.input;
      const kind =
        input instanceof vscode.TabInputTextDiff
          ? `diff, ${input.original.scheme} ↔ ${input.modified.scheme}`
          : input instanceof vscode.TabInputText
            ? input.uri.scheme
            : "webview";
      const active = vscode.window.activeTextEditor;
      const line = active && input instanceof vscode.TabInputText && active.document.uri.toString() === input.uri.toString() ? `, line ${active.selection.active.line + 1}` : "";
      return `${tab.label} (${kind}${line})`;
    };
    // The status bar item as shown, its icons as `$(name)`; and the state the Gerrit views' welcome text follows.
    const status = (): string => api.statusItem.text;
    /** Everything a user could see, one line each, prefixed by where it is. */
    const screen = async (): Promise<string[]> => {
      const shown = api.changeView.target;
      return [
        `status bar | ${status()}`,
        `state  | ${api.model.state}`,
        `events | ${api.events.events?.state ?? "(not said)"}`,
        ...(await dump(api.overviewView)).map((line) => `chains | ${line}`),
        ...(await dump(api.stackView)).map((line) => `stack  | ${line}`),
        `change | title: ${shown ? `${shown.number ? `Change ${shown.number}` : "Change"} — ${api.changeView.shown?.summary ?? shown.title}` : "(nothing shown)"}`,
        ...(await dump(api.changeView)).map((line) => `change | ${line}`),
        `editor | ${editor()}`,
      ].map(normalize);
    };
    /** Let follow-up refreshes and the conflict preview land. */
    const settle = async () => {
      await wait(300);
      await api.model.refresh();
      await wait(300);
      await api.changeView.idle();
    };

    let step = 0;
    let before = await screen();
    out.push("# What a user sees and gets, button by button", "", "## On start", "", "```", ...before, "```", "");

    /** Press one button: write the expectation, what was said and done, and what changed on screen. */
    const press = async (button: string, expect: string, action: () => unknown, answers: Answer[] = []) => {
      step += 1;
      ui.script(answers);
      let threw: string | undefined;
      try {
        await action();
      } catch (error) {
        threw = error instanceof Error ? error.message : String(error);
      }
      await settle();
      const after = await screen();
      const said = ui.drain();
      const changed = lineDiff(before, after);
      if (process.env.GER_E2E_SCREENS) {
        // Full screens, for when a diff alone does not explain a step.
        fs.appendFileSync(process.env.GER_E2E_SCREENS, [`## ${step} before`, ...before, `## ${step} after`, ...after, ""].join("\n"));
      }
      before = after;
      out.push(`## ${step}. ${button}`, "", `**Expect:** ${expect}`, "", "```");
      out.push(...(said.lines.length ? said.lines : ["(nothing said or done outside the views)"]));
      if (threw) {
        out.push(`!! the command threw: ${normalize(threw)}`);
      }
      out.push("--- on screen:", ...(changed.length ? changed : ["(no change)"]), "```", "");
      problems.push(...said.problems.map((problem) => `${step}. ${button}: ${problem}`));
    };
    /** Something the user does outside the extension (a commit in the terminal). */
    const meanwhile = async (what: string, action: () => unknown, expect?: string) => {
      await action();
      await settle();
      const after = await screen();
      out.push(`## Meanwhile: ${what}`, "", ...(expect ? [`**Expect:** ${expect}`, ""] : []), "```", ...lineDiff(before, after), "```", "");
      before = after;
    };

    const stackRow = async (summary: string) => {
      const row = await find(api.stackView, summary);
      assert.ok(row, `stack row "${summary}"`);
      return row;
    };
    const chainRow = async (subject: string) => {
      const row = await find(api.overviewView, subject);
      assert.ok(row, `overview row "${subject}"`);
      return row;
    };
    const threadRow = async (first: RegExp) => {
      const row = await find(api.changeView, first);
      assert.ok(row, `thread ${first}`);
      return row;
    };
    const chain = (first: RegExp): CommentChain => {
      const found = api.changeView.chains().find((c) => first.test(c.comments[0]?.message ?? ""));
      assert.ok(found, `chain ${first}`);
      return found;
    };
    const exec = (command: string, ...args: unknown[]) => () => vscode.commands.executeCommand(command, ...args);

    // --- The overview -------------------------------------------------------------
    await press("Click the status bar item", "The Change view opens on the change the status bar names (CI failed on 102).", exec("ger.openHeadline"));
    await press("Review chains › Mark All Activity as Seen", "Every \"● new activity\" marker goes; nothing else changes.", exec("ger.markSeen"));
    await press("Review chains › Refresh from Gerrit", "Both Review chains and Local stack show progress while they reload from Gerrit. With nothing new there, nothing changes.", exec("ger.refresh"));
    const theirs = await chainRow("Add retry to the uploader");
    await press(
      "Click \"Add retry to the uploader\" under To review",
      "The Change view shows change 900: what it needs from you, its files, its comments.",
      exec("ger.openChange", theirs),
    );
    await press("Inline Open in Gerrit on change 900", "The browser opens change 900 on Gerrit.", exec("ger.openInGerrit", theirs));
    await press(
      "Right-click change 900 › Hand to Agent",
      "The comments and CI findings of 900 open as a preview, and a dialog offers to copy them (no agent command is set). It says truthfully that 900 is not checked out, so the agent can only read the brief; it does not promise amended commits.",
      exec("ger.agentBrief", theirs),
      [{ button: "Copy to Clipboard" }],
    );
    await press(
      "Right-click change 900 › Hand to Agent › Check Out to Work On",
      "The same dialog offers to check 900 out to work on instead; pressing it starts ger checkout in a terminal, as the agent could not change a change that is not checked out.",
      exec("ger.agentBrief", theirs),
      [{ button: "Check Out to Work On" }],
    );
    await press(
      "Inline Check Out to Work On on change 900",
      "ger checkout change:900 runs in a new terminal, where it can ask before it changes anything; the status bar says so. Nothing else changes until you refresh.",
      exec("ger.checkout", theirs),
    );
    const mine = api.overviewView.getChildren(await chainRow("Cleanup after experiment"))[0]; // 103, the newest of the chain
    await press(
      "Inline Edit Commit Here on change 103, a local commit in Review chains",
      "Your own commit is edited where it is: ger edit runs in the terminal, and the status bar says what to do once it stops (not a review checkout, not a notification).",
      exec("ger.stack.edit", mine),
    );
    await press(
      "Inline Review Change on change 900",
      "Progress while it fetches; then where change 900 is checked out for review (the review worktree, as ger.reviewCheckout is not set), with a button to open that worktree in a new window.",
      exec("ger.review", theirs),
      [{ button: "Open Review Worktree" }],
    );
    await press("Change › … › Leave Review Checkout", "The review checkout of 900 is closed, and a notification says so.", exec("ger.reviewDone"));
    await press(
      "Command palette › ger: Leave Review Checkout, with none open",
      "It says there is no review checkout to leave (the Change view menu no longer offers it).",
      exec("ger.reviewDone"),
    );

    // --- When ger cannot start ---------------------------------------------------------
    const settings = vscode.workspace.getConfiguration("ger");
    const executable = settings.get<string>("executable");
    await press(
      "Settings › ger.executable set to a program that is not installed",
      "ger serve restarts with it and cannot start: the Gerrit views go empty and their welcome text says the program was not found, with Open Settings and Retry; the status bar shows a warning.",
      () => settings.update("executable", "ger-not-installed", vscode.ConfigurationTarget.Global),
    );
    await press(
      "Review chains › Retry, the program still missing",
      "It says it is restarting ger serve; the program is still missing, so the views still say so.",
      exec("ger.refresh"),
    );
    await press(
      "Settings › ger.executable set back",
      "ger serve starts again at once, without a reload, and the views show what they showed before.",
      () => settings.update("executable", executable, vscode.ConfigurationTarget.Global),
    );

    // --- Your commit with comments ----------------------------------------------------
    await press(
      "Click \"Extract command routing\" in Local stack",
      "The Change view is titled Change 101 and shows b.txt under Files and both unresolved comments.",
      exec("ger.openChange", await stackRow("Extract command routing")),
    );
    await press("Change › Next Unresolved Comment (Alt+F9)", "b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.", exec("ger.nextComment"));
    await press("Next Unresolved Comment again", "The second thread: Comment 2 of 2.", exec("ger.nextComment"));
    await press("Next Unresolved Comment a third time", "It wraps around to Comment 1 of 2.", exec("ger.nextComment"));
    await press("Change › Previous Unresolved Comment", "Back to Comment 2 of 2.", exec("ger.prevComment"));
    await press(
      "Click b.txt under Files",
      "A diff opens: the parent on the left, the commit on the right.",
      exec("ger.openDiff", api.changeView.diff?.files[0], api.changeView.diff),
    );
    await press(
      "Inline Keep for Me on \"Is this the right design?\"",
      "The thread is marked as kept for you; the agent will leave it alone.",
      exec("ger.hold", await threadRow(/^Is this the right design/)),
    );
    await press(
      "Local stack › Fix Stack with Agent…",
      "The runbook opens as a preview; a dialog counts what the agent gets, without the comment kept for you; Copy to Clipboard copies it.",
      exec("ger.agentWalk"),
      [{ button: "Copy to Clipboard" }],
    );
    await press(
      "Inline Let the Agent Handle It on \"Is this the right design?\"",
      "The thread is no longer marked as kept for you.",
      exec("ger.release", await threadRow(/^Is this the right design/)),
    );
    await press(
      "Inline Reply as Draft… on \"Please rename this.\"",
      "An input box asks for the reply; after Enter the draft shows under the thread and the status bar says it was saved.",
      exec("ger.reply", await threadRow(/^Please rename this/)),
      [{ type: "Renamed it." }],
    );
    await press(
      "Inline Reply as Draft… on \"Please rename this.\" again",
      "The box opens with your draft in it, to edit; saving updates that draft, nothing you wrote is lost unseen.",
      exec("ger.reply", await threadRow(/^Please rename this/)),
      [{ type: "Renamed it to route()." }],
    );
    await press(
      "Inline Reply Done and Resolve as Draft on \"Is this the right design?\"",
      "A \"Done\" draft that resolves the thread is saved at once; the thread shows that your draft resolves it.",
      exec("ger.replyDone", await threadRow(/^Is this the right design/)),
    );
    await press(
      "Inline Reply Done and Resolve as Draft on \"Please rename this.\", which has your draft",
      "Your draft stays as you wrote it and now resolves the thread.",
      exec("ger.replyDone", await threadRow(/^Please rename this/)),
    );
    const rename = api.threads.threadOf(chain(/^Please rename this/));
    await press(
      "Type in the editor thread of \"Please rename this.\" › Save Draft Reply",
      "What you type is added to your draft reply there, after what it already says; the status bar says the draft was updated.",
      exec("ger.thread.reply", { thread: rename, text: "And updated the callers." }),
    );
    const commit = api.changeView.diff?.commit ?? "";
    const newThread = { uri: fileUri(commit, "b.txt"), range: new vscode.Range(0, 0, 0, 0), dispose: () => undefined };
    const save = api.changeView.shown?.new_comment === "local-draft" ? "ger.comment.saveLocalDraft" : "ger.comment.saveDraft";
    await press(
      `"+" on line 1 of b.txt in the diff › ${save === "ger.comment.saveDraft" ? "Save Draft" : "Save Local Draft"}`,
      "The comment is saved as a draft and appears as a thread marked as your draft; it is not counted as unresolved feedback.",
      exec(save, { thread: newThread, text: "Should this be configurable?" }),
    );
    await press(
      "\"+\" on line 1 of b.txt in the diff › Save Private Note",
      "The note is saved in this repository only and appears marked as your private note; it is not counted as unresolved feedback.",
      exec("ger.comment.saveNote", { thread: { ...newThread }, text: "Remember to check the docs." }),
    );
    await press(
      "Change › Edit Commit Here",
      "ger edit runs in the terminal; you are told what to do once the rebase stops at 101, not that it already has.",
      exec("ger.stack.edit"),
    );
    await press("Change › Open in Gerrit", "The browser opens change 101 on Gerrit.", exec("ger.openInGerrit"));

    // --- A commit with failed CI ------------------------------------------------------
    const failing = await stackRow("test! temporary experiment");
    await press(
      "Click \"test! temporary experiment\" in Local stack",
      "The Change view says CI failed; the CI group lists the findings, the ones this commit introduced first.",
      exec("ger.openChange", failing),
    );
    await press(
      "Right-click it › Hand to Agent › Cancel",
      "A runbook for this one commit and a dialog to start or copy it; Cancel does nothing more.",
      exec("ger.agentBrief", failing),
      [{ dismiss: true }],
    );

    // --- Rewriting the stack ----------------------------------------------------------
    await press("Local stack › Push to Gerrit…", "ger push runs in a new terminal of its own (never typed into one that is busy), which shows what it will push and asks first.", exec("ger.push"));
    await press(
      "Inline ↑ on \"Refactor parser init\"",
      "It swaps places with the commit above it; a notification offers Undo. Every commit keeps its votes: none changed its code, so Gerrit keeps them on the push.",
      exec("ger.stack.moveUp", await stackRow("Refactor parser init")),
    );
    await press(
      "Inline ↓ on \"Refactor parser init\", then Undo",
      "It swaps back with the commit below; Undo puts the stack back as it was before this move.",
      exec("ger.stack.moveDown", await stackRow("Refactor parser init")),
      [{ button: "Undo" }],
    );
    await meanwhile("you commit a rework of d.txt in the terminal", () => {
      fs.writeFileSync(path.join(repo, "d.txt"), "reworked\n", "utf8");
      execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qam", "Rework d.txt"], { cwd: repo });
    });
    await press(
      "Inline ⊘ (Show Why It Cannot Move Earlier) on \"Rework d.txt\"",
      "It says which commit it would conflict with, and that nothing changed.",
      exec("ger.stack.moveDownBlocked", await stackRow("Rework d.txt")),
    );
    const dragged = async (summary: string) => {
      const data = new vscode.DataTransfer();
      api.stackView.handleDrag([await stackRow(summary)], data);
      return data;
    };
    {
      const [target, data] = [await stackRow("Cleanup after experiment"), await dragged("Rework d.txt")];
      await press("Drag \"Rework d.txt\" onto \"Cleanup after experiment\"", "Refused before anything is touched, saying why; the stack stays as it was.", () =>
        api.stackView.handleDrop(target, data),
      );
    }
    {
      const [target, data] = [await stackRow("Extract command routing"), await dragged("Cleanup after experiment")];
      await press(
        "Drag \"Cleanup after experiment\" onto \"Extract command routing\"",
        "It takes that commit's place, and the commits between move up one; Undo is offered.",
        () => api.stackView.handleDrop(target, data),
      );
    }
    await press(
      "Right-click \"Rework d.txt\" › Squash into Commit Below, then Undo",
      "It folds into the commit below, keeping that commit's message; Undo brings it back.",
      exec("ger.stack.squash", await stackRow("Rework d.txt")),
      [{ button: "Undo" }],
    );
    await press(
      "Right-click \"Rework d.txt\" › Squash into Commit Below, Keeping This Message, then Undo",
      "Folds, keeping this commit's message, with a note that the Gerrit change of the one below stays open; Undo brings both back, and the Change view shows again what it showed.",
      exec("ger.stack.squashTakeMessage", await stackRow("Rework d.txt")),
      [{ button: "Undo" }],
    );
    await press(
      "Right-click \"Rework d.txt\" › Drop Commit… › Cancel",
      "A dialog asks to confirm; Cancel leaves everything as it was.",
      exec("ger.stack.drop", await stackRow("Rework d.txt")),
      [{ dismiss: true }],
    );
    await press(
      "Right-click \"Rework d.txt\" › Drop Commit… › Drop, then Undo",
      "The commit goes; Undo brings it back. It was never pushed, so there is nothing to push.",
      exec("ger.stack.drop", await stackRow("Rework d.txt")),
      [{ button: "Drop" }, { button: "Undo" }],
    );
    await press(
      "Right-click \"Rework d.txt\" › Drop Commit… › Drop",
      "The commit is gone from the stack and from the checkout; nothing to push, as Gerrit never had it.",
      exec("ger.stack.drop", await stackRow("Rework d.txt")),
      [{ button: "Drop" }],
    );

    await meanwhile(
      "you change the code of the top commit and amend it",
      () => {
        fs.writeFileSync(path.join(repo, "c.txt"), "file c.txt, reworked\n", "utf8");
        execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--amend", "--no-edit", "-a"], { cwd: repo });
      },
      "Its votes now show as the last pushed patchset's (Gerrit may not keep them for this code); the Change view follows the commit and says so.",
    );

    // --- The branch -------------------------------------------------------------------
    await press(
      "Local stack › … › Set Upstream Branch… › Escape",
      "A picker lists the branches it could track, the current one marked; Escape changes nothing.",
      exec("ger.setUpstream"),
      [{ dismiss: true }],
    );

    // --- A failure nobody asked for ------------------------------------------------------
    {
      // Not through `press`/`meanwhile`: they refresh on their own (as a user would), which would fail the same way and
      // blank the views the way a refresh you asked for does.
      const down = process.env.GER_E2E_DOWN ?? "";
      const note = async (what: string, expect: string, action: () => Promise<void>) => {
        ui.script([]);
        await action();
        await wait(300);
        const after = await screen();
        out.push(`## Meanwhile: ${what}`, "", `**Expect:** ${expect}`, "", "```", ...ui.drain().lines, ...lineDiff(before, after), "```", "");
        before = after;
      };
      fs.writeFileSync(down, "");
      await note(
        "a background refresh finds ger serve gone and cannot start it again",
        "Nothing is said and the views keep what they showed; the status bar item gets a warning mark, its tooltip says the answer is out of date, and Review chains is labelled so.",
        async () => {
          (api.model as unknown as { process: { stop(): void } }).process.stop();
          await api.model.refresh({ background: true });
          assert.ok(api.model.stale, "the model knows its overview is old");
        },
      );
      await note(
        "you press Refresh while ger serve is still gone",
        "The views keep the last answer, marked out of date; a notification says why it is still out of date, and nothing is cleared.",
        async () => {
          await vscode.commands.executeCommand("ger.refresh");
          assert.ok(api.model.stale, "the model still knows its overview is old");
          assert.ok(api.model.overview, "the last overview was kept");
        },
      );
      fs.rmSync(down);
      await note(
        "ger serve can start again, and the next background refresh works",
        "The warning mark goes and the views show what Gerrit says now (the fake Gerrit starts afresh from the repository, so the amended top commit is no longer ahead of it); its event stream is followed again.",
        async () => {
          // From Gerrit: the cache of the service that went away would otherwise be served or not, by timing.
          await api.model.refresh({ background: true, fromGerrit: true });
          assert.equal(api.model.stale, undefined);
          // The stack's own annotations are served from the cache for its trust window (10 s): without this wait they
          // would catch up in whichever later step first refreshes, and the transcript would depend on timing.
          await wait(10_500);
          await api.model.refresh({ background: true });
          assert.ok(await until(() => api.events.live), "the event stream is followed again");
        },
      );
    }

    // --- News from Gerrit ----------------------------------------------------------------
    {
      const what = "a reviewer comments on change 900 on Gerrit, and its event stream says so";
      const expect = "With no click and no poll, Review chains shows the new unresolved comment on 900 within a few seconds; nothing is said.";
      const comment = (id: string, message: string) => ({ id, line: 1, message, unresolved: true, patch_set: 1, author: { name: "Bob Architect" } });
      const event = {
        type: "comment-added",
        change: { project: "testproj", branch: "main", id: "I" + (900).toString(16).padStart(40, "0"), number: 900, owner: { username: "alice" } },
        author: { username: "bob" },
        comment: "Patch Set 1:\n\n(1 comment)",
        _fake: {
          number: 900,
          update: { unresolved_comment_count: 2, updated: "2026-11-10 09:00:00.000000000" },
          comments: { "uploader.txt": [comment("c900", "Should it back off between retries?"), comment("c901", "And cap the delay.")] },
        },
      };
      const comments = () => api.model.overview?.sections?.find((s) => s.name === "to-review")?.chains[0]?.comments_unresolved;
      fs.appendFileSync(process.env.GER_E2E_EVENTS ?? "", `${JSON.stringify(event)}\n`);
      const refreshed = await until(() => comments() === 2);
      await wait(300);
      await api.changeView.idle();
      const after = await screen();
      out.push(`## Meanwhile: ${what}`, "", `**Expect:** ${expect}`, "", "```", ...(refreshed ? [] : ["!! the views did not refresh by themselves"]), ...lineDiff(before, after), "```", "");
      before = after;
    }

    // --- Local git -----------------------------------------------------------------------
    {
      const what = "you commit in the terminal, with no click and no focus change";
      const expect = "Within a second or two Local stack shows the new commit on top (not yet on Gerrit); nothing is said, as you did it yourself.";
      fs.writeFileSync(path.join(repo, "e.txt"), "from the terminal\n", "utf8");
      execFileSync("git", ["add", "e.txt"], { cwd: repo });
      execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "Add e.txt"], { cwd: repo });
      const refreshed = await until(() => api.model.stack?.commits.some((c) => c.summary === "Add e.txt") ?? false);
      await wait(300);
      await api.changeView.idle();
      const after = await screen();
      out.push(`## Meanwhile: ${what}`, "", `**Expect:** ${expect}`, "", "```", ...(refreshed ? [] : ["!! Local stack did not follow the repository by itself"]), ...lineDiff(before, after), "```", "");
      before = after;
    }

    out.push("## At the end", "", "```", ...before, "```", "");
    if (problems.length) {
      out.push("## The script and the extension disagreed", "", ...problems.map((p) => `- ${p}`), "");
    }
    assert.deepEqual(problems, [], "every scripted answer met the prompt it was meant for");
  } catch (error) {
    out.push("## FAILED", "", "```", error instanceof Error ? (error.stack ?? error.message) : String(error), "```", "");
    throw error;
  } finally {
    const target = process.env.GER_E2E_OUT;
    if (target) {
      fs.writeFileSync(target, out.join("\n"), "utf8");
    }
  }
}
