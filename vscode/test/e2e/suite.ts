// Runs inside a real VS Code (see run.ts): activates the extension against fake_ger.py and
// presses every button a user can press, in an order a user might. For each press it writes
// down what the user expects, what the extension said and did (through the scripted Ui), and
// how the views changed. That transcript goes to GER_E2E_OUT; run.ts compares it with the
// golden file, so any change in behaviour is a visible diff to judge against the expectation.

import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import type { CommentChain } from "../../src/api";
import { fileUri } from "../../src/diffTabs";
import { readSnapshot } from "../../src/snapshot";
import { vscodeUi } from "../../src/ui";
import type { ExtensionApi } from "../../src/extension";
import { gerCommandLine } from "../../src/launch";
import { Camera, pictureName } from "./picture";
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
      .replace(/ger extension \S+ · VS Code \S+ · \S+ \S+/g, "ger extension <version> · VS Code <version> · <os>")
      .replace(/\b[0-9a-f]{7,40}\b/g, "<sha>")
      .replace(/\b\d+[mhd]\b/g, "<age>");
  };
}

/** The side bar's width in pictures, in CSS pixels: as wide as users who work in these views make it. */
const SIDE_BAR_WIDTH = 420;

export async function run(): Promise<void> {
  const out: string[] = [];
  const repo = vscode.workspace.workspaceFolders?.[0].uri.fsPath ?? "";
  const normalize = normalizer(repo);
  const ui = new ScriptedUi(normalize);
  const problems: string[] = [];
  let camera: Camera | undefined;
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
      // The merge editor's input (TabInputTextMerge) is not in the 1.85 API: it has the file it writes as `result`.
      const merged = (input as { result?: unknown }).result;
      const kind =
        merged instanceof vscode.Uri
          ? `merge editor, ${merged.scheme}`
          : input instanceof vscode.TabInputTextDiff
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
        ...api.stackView.lines().map((line) => `stack  | ${line}`),
        `change | title: ${shown ? (api.changeView.heading ?? "(view not drawn)") : "(nothing shown)"}`,
        ...(await api.changeView.lines()).map((line) => `change | ${line}`),
        `editor | ${editor()}`,
      ].map(normalize);
    };
    /** Let follow-up refreshes and the conflict preview land. */
    const settle = async () => {
      await wait(300);
      await api.model.refresh();
      await wait(300);
      await api.changeView.idle();
      await api.diffTabs.idle();
    };

    // GER_E2E_PNG: a picture of the window on start and after every press, into that folder.
    const pictures = process.env.GER_E2E_PNG;
    if (pictures) {
      fs.mkdirSync(pictures, { recursive: true });
      camera = await Camera.open(process.env.GER_E2E_DEVTOOLS ?? "");
      // The Gerrit side bar, wide as users make it, and no chat panel taking the right side.
      await vscode.commands.executeCommand("workbench.view.extension.ger");
      await vscode.commands.executeCommand("workbench.action.closeAuxiliaryBar").then(undefined, () => undefined);
      await camera.widenSideBar(SIDE_BAR_WIDTH);
      await settle();
    }
    const snap = async (step: number, label: string) => {
      if (camera && pictures) {
        await wait(200); // the webview pages draw what they were sent
        await camera.take(path.join(pictures, pictureName(step, label)));
      }
    };

    let step = 0;
    let before = await screen();
    await snap(0, "on start");
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
      await snap(step, button);
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

    /** A row of Local stack, as its context menu and its buttons hand it to a command. */
    const stackRow = async (summary: string) => {
      const commit = api.model.stack?.commits.find((c) => c.summary === summary);
      assert.ok(commit, `stack row "${summary}"`);
      return { webviewSection: "commit", sha: commit.sha, shas: [commit.sha] };
    };
    /** Where a row is in Local stack as shown, newest first: gap `n` is the line above it, `n + 1` the line under it. */
    const shownAt = (summary: string): number => {
      const shown = [...(api.model.stack?.commits ?? [])].reverse();
      const index = shown.findIndex((c) => c.summary === summary);
      assert.ok(index >= 0, `stack row "${summary}"`);
      return index;
    };
    /** Drag rows and let go on the line at `gap`. */
    const drag = (summaries: string[], gap: () => number) => async () => {
      const shas = await Promise.all(summaries.map(async (summary) => (await stackRow(summary)).sha));
      await api.stackView.move(shas, gap());
    };
    const chainRow = async (subject: string) => {
      const row = await find(api.overviewView, subject);
      assert.ok(row, `overview row "${subject}"`);
      return row;
    };
    const chain = (first: RegExp): CommentChain => {
      const found = api.changeView.chains().find((c) => first.test(c.comments[0]?.message ?? ""));
      assert.ok(found, `chain ${first}`);
      return found;
    };
    /** A comment chain of the Change view, as its buttons hand it to a command. */
    const threadRow = async (first: RegExp) => ({ kind: "thread" as const, chain: chain(first) });
    const exec = (command: string, ...args: unknown[]) => () => vscode.commands.executeCommand(command, ...args);
    /** What the Change view's page posts when a row is clicked or typed in: the message its extension side acts on. */
    const page = api.changeView as unknown as { receive(message: { type: string; [field: string]: unknown }): Promise<unknown> };

    // --- The overview -------------------------------------------------------------
    await press("Click the status bar item", "The Change view opens on the change the status bar names (CI failed on 102).", exec("ger.openHeadline"));
    await press("Review chains › Mark All Activity as Seen", "Every \"● new activity\" marker goes; nothing else changes.", exec("ger.markSeen"));
    await press(
      "Review chains › Sort Chains… › By last activity, oldest first",
      "A picker offers the three orders, the current one marked; both sections say \"oldest first\" and list their chains that way.",
      exec("ger.sortChains"),
      [{ pick: "By last activity, oldest first" }],
    );
    await press(
      "Review chains › Sort Chains… › By chain state",
      "Back to the order that puts what needs you first; the sections no longer name an order.",
      exec("ger.sortChains"),
      [{ pick: "By chain state" }],
    );
    await press("Review chains › Refresh from Gerrit", "Both Review chains and Local stack show progress while they reload from Gerrit. With nothing new there, nothing changes.", exec("ger.refresh"));
    const theirs = await chainRow("Add retry to the uploader");
    await press(
      "Click \"Add retry to the uploader\" under To review",
      "The Change view shows change 900: what it needs from you, its files, its comments.",
      exec("ger.openChange", theirs),
    );
    await press("Inline Open in Gerrit on change 900", "The browser opens change 900 on Gerrit.", exec("ger.openInGerrit", theirs));
    await press(
      "Right-click change 900 › Copy SHA",
      "900 is not in your stack: the SHA of its current patch set is on the clipboard; the status bar says so.",
      exec("ger.copySha", theirs),
    );
    await press("Right-click change 900 › Copy Change-Id", "Its Change-Id is on the clipboard; the status bar says so.", exec("ger.copyChangeId", theirs));
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
      "ger checkout change:900 runs in the ger terminal, where it can ask before it changes anything; the status bar says so. The views follow by themselves once the checkout moves HEAD.",
      exec("ger.checkout", theirs),
    );
    await press(
      "Right-click the chain \"Cleanup after experiment\" › Copy SHA",
      "A chain row stands for its top change, 103, which is in your stack: its local commit is on the clipboard.",
      exec("ger.copySha", await chainRow("Cleanup after experiment")),
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
      "Review chains › Install ger",
      "A terminal opens and installs the ger release with this extension's version, with uv (the suite needs uv, so it is there); nothing else changes until Retry.",
      exec("ger.installCli"),
    );
    await press(
      "Settings › ger.executable set back",
      "ger serve starts again at once, without a reload, and the views show what they showed before.",
      () => settings.update("executable", executable, vscode.ConfigurationTarget.Global),
    );

    // --- Your commit with comments ----------------------------------------------------
    await press(
      "Click \"Extract command routing\" in Local stack",
      "The Change view is titled Change 101 and shows its commit message (editable: it is your commit), the local commit against its base, b.txt under Files with its added and removed lines, and both unresolved comments.",
      exec("ger.openChange", await stackRow("Extract command routing")),
    );
    await press(
      "Change › Compare › Gerrit vs. local",
      "The files list becomes what your commit changes compared with Gerrit's patchset: nothing, as the commit is the pushed one.",
      () => api.changeView.choose("local", "gerrit"),
    );
    await press(
      "Change › Show › Gerrit's current patchset",
      "The view shows patchset 1 as Gerrit has it, against its base; its message is read-only.",
      () => api.changeView.choose("current", "base"),
    );
    await press("Change › Show › Local commit", "Back on your commit: the message is editable again.", () => api.changeView.choose("local", "base"));
    await press("Change › Next Unresolved Comment (Alt+F9)", "b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.", exec("ger.nextComment"));
    await press("Next Unresolved Comment again", "The second thread: Comment 2 of 2.", exec("ger.nextComment"));
    await press("Next Unresolved Comment a third time", "It wraps around to Comment 1 of 2.", exec("ger.nextComment"));
    await press("Change › Previous Unresolved Comment", "Back to Comment 2 of 2.", exec("ger.prevComment"));
    await press(
      "Click \"Please rename this.\" in the Change view",
      "b.txt opens at line 1 with that comment's thread expanded there, the other one closed.",
      async () => {
        await page.receive({ type: "thread", id: chain(/^Please rename this/).root_id, action: "open" });
        const expanded = (first: RegExp) => api.threads.threadOf(chain(first))?.collapsibleState === vscode.CommentThreadCollapsibleState.Expanded;
        assert.deepEqual([expanded(/^Please rename this/), expanded(/^Is this the right design/)], [true, false]);
      },
    );
    await press(
      "Click b.txt under Files",
      "b.txt opens on its own: the commit adds it, so there is nothing to compare it with.",
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
    await press(
      "Expand \"Looks good overall.\" (about the whole change) in the Change view, type a reply, Resolved left ticked › Save",
      "The comment has no line in a file, so it is answered in the view: the reply is saved as a draft, as a reply in an editor thread is, and shows under it; the status bar says it was saved, the comment staying resolved.",
      () => page.receive({ type: "thread", id: chain(/^Looks good overall/).root_id, action: "replyText", text: "Added a test for the parser.", resolved: true }),
    );
    await press(
      "Click \"Please rename this.\" in the Change view again",
      "b.txt comes back in front of the runbook with that thread expanded: Alice's comment and its suggested fix, your draft reply under it, the reply box, and the Resolved toggle on in its header.",
      async () => {
        await page.receive({ type: "thread", id: chain(/^Please rename this/).root_id, action: "open" });
        const thread = api.threads.threadOf(chain(/^Please rename this/));
        assert.equal(thread?.collapsibleState, vscode.CommentThreadCollapsibleState.Expanded, "the thread is expanded");
      },
    );
    const rename = api.threads.threadOf(chain(/^Please rename this/));
    await press(
      "Type in the editor thread of \"Please rename this.\", whose Resolved toggle is on (your draft resolves it) › Save",
      "What you type is added to your draft reply there, after what it already says, and it still resolves the comment; the status bar says the draft was updated.",
      exec("ger.thread.reply", { thread: rename, text: "And updated the callers." }),
    );
    await press(
      "Click the Resolved toggle in the header of the editor thread of \"Please rename this.\"",
      "Your draft there is saved again at once, now leaving the comment unresolved; the status bar says so.",
      exec("ger.thread.markUnresolved", api.threads.threadOf(chain(/^Please rename this/))),
    );
    await press(
      "Click the Resolved toggle in the header of the editor thread of \"Please rename this.\" again",
      "Your draft resolves the comment again, as before.",
      exec("ger.thread.markResolved", api.threads.threadOf(chain(/^Please rename this/))),
    );
    const commit = api.changeView.diff?.commit ?? "";
    const newThread = { uri: fileUri(commit, "b.txt"), range: new vscode.Range(0, 0, 0, 0), dispose: () => undefined };
    const save = api.changeView.newComment === "local-draft" ? "ger.comment.saveLocalDraft" : "ger.comment.saveDraft";
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
      "Inline Edit… on your comment \"Should this be configurable?\" in the Change view",
      "b.txt opens at its line and the comment turns into a text box in its thread, with Save and Cancel; nothing is saved yet.",
      exec("ger.chain.edit", await threadRow(/^Should this be configurable/)),
    );
    await press(
      "Change the text in that box › Save",
      "Your comment says the new text, in the thread and in the Change view; the status bar says it was updated. It is still yours and not published.",
      () => {
        const own = api.threads.ownInThread(chain(/^Should this be configurable/));
        assert.ok(own && own.mode === vscode.CommentMode.Editing, "the comment is being edited in its thread");
        own.body = "Should this be a setting?\n\nWith a default.";
        return vscode.commands.executeCommand("ger.comment.save", own);
      },
    );
    await press(
      "Pencil on your private note in its editor thread, change the text › Cancel",
      "The note says what it said before; nothing is saved and nothing is said.",
      async () => {
        const own = api.threads.ownInThread(chain(/^Remember to check the docs/));
        assert.ok(own, "the note has a thread in the editor");
        await vscode.commands.executeCommand("ger.comment.edit", own);
        assert.equal(own.mode, vscode.CommentMode.Editing);
        own.body = "Something else entirely.";
        await vscode.commands.executeCommand("ger.comment.cancel", own);
        assert.equal(own.text, "Remember to check the docs.");
      },
    );
    await press(
      "Inline Delete Your Draft Reply… on \"Is this the right design?\" › Cancel",
      "A dialog shows the draft reply and asks before deleting it; Cancel leaves it.",
      exec("ger.chain.delete", await threadRow(/^Is this the right design/)),
      [{ dismiss: true }],
    );
    await press(
      "Trash on your private note in its editor thread › Delete",
      "A dialog shows the note and asks; Delete removes it from the thread and from the Change view, and the status bar says so.",
      () => vscode.commands.executeCommand("ger.comment.delete", api.threads.ownInThread(chain(/^Remember to check the docs/))),
      [{ button: "Delete" }],
    );
    await press(
      "Command palette › ger: Add Comment, with your own b.txt in the editor",
      "The file in the editor is yours as it is now, not change 101 as shown (its menu does not offer Add Comment): it says where a comment on 101 goes, and opens no box.",
      exec("ger.comment.add"),
    );
    await press(
      "Open b.txt of the commit, cursor on line 1 › Add Comment (Ctrl+Alt+C), type › Save",
      "A comment box opens on that line with the same Save buttons as the \"+\" in the gutter; saving it adds your comment to the change.",
      async () => {
        await vscode.window.showTextDocument(fileUri(commit, "b.txt"), { selection: new vscode.Range(0, 0, 0, 0) });
        await vscode.commands.executeCommand("ger.comment.add");
        const [box] = api.threads.openBoxes;
        assert.ok(box && box.comments.length === 0 && box.range?.start.line === 0, "an empty comment box on line 1");
        await vscode.commands.executeCommand(save, { thread: box, text: "Added from the keyboard." });
        assert.deepEqual(api.threads.openBoxes, [], "the box closed once the comment was saved");
      },
    );
    await press(
      "Add Comment again › Cancel",
      "The box closes; nothing is saved.",
      async () => {
        await vscode.commands.executeCommand("ger.comment.add");
        const [box] = api.threads.openBoxes;
        assert.ok(box, "a comment box opened");
        await vscode.commands.executeCommand("ger.comment.cancelNew", { thread: box, text: "never mind" });
        assert.deepEqual(api.threads.openBoxes, []);
      },
    );
    await press(
      "Inline Comment on This File… on b.txt under Files",
      "An input box asks for a comment about the whole file; after Enter it is saved like any new comment and shows under Comments as yours.",
      exec("ger.comment.file", "b.txt"),
      [{ type: "This file needs a header." }],
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
    await press("Local stack › Push to Gerrit…", "ger push runs in the ger terminal (a new one when a command still runs there), which shows what it will push and asks first.", exec("ger.push"));
    await press(
      "Right-click \"Refactor parser init\" › Copy SHA",
      "The commit's full id is on the clipboard; the status bar says so.",
      exec("ger.copySha", await stackRow("Refactor parser init")),
    );
    await press(
      "Select two rows, right-click › Copy Change-Id",
      "Both Change-Ids are on the clipboard, one per line, newest first as shown.",
      async () => {
        const [one, two] = [await stackRow("Refactor parser init"), await stackRow("Extract command routing")];
        await vscode.commands.executeCommand("ger.copyChangeId", { ...one, shas: [one.sha, two.sha] });
      },
    );
    await press(
      "Drag \"Refactor parser init\" to the line above \"Extract command routing\"",
      "It swaps places with the commit above it; a notification offers Undo. Every commit keeps its votes: none changed its code, so Gerrit keeps them on the push.",
      drag(["Refactor parser init"], () => shownAt("Extract command routing")),
    );
    await press(
      "Drag \"Refactor parser init\" to the bottom, then Undo",
      "It swaps back with the commit below; Undo puts the stack back as it was before this move.",
      drag(["Refactor parser init"], () => api.model.stack?.commits.length ?? 0),
      [{ button: "Undo" }],
    );
    await press(
      "Select \"Cleanup after experiment\" and \"Extract command routing\", drag them to the bottom, then Undo",
      "Both land under the last row, together and in the order they had; Undo puts the stack back.",
      drag(["Cleanup after experiment", "Extract command routing"], () => api.model.stack?.commits.length ?? 0),
      [{ button: "Undo" }],
    );
    await meanwhile("you edit a.txt and do not commit it", () => fs.writeFileSync(path.join(repo, "a.txt"), "file a.txt\nnot committed yet\n", "utf8"));
    await press(
      "Drag \"Extract command routing\" up one row, with uncommitted changes › Cancel",
      "A dialog asks whether to stash the uncommitted changes and continue; Cancel leaves the stack and the edit as they were.",
      drag(["Extract command routing"], () => shownAt("Refactor parser init")),
      [{ dismiss: true }],
    );
    await press(
      "The same drag › Stash and Continue, then Undo",
      "The commits swap, and the edit to a.txt is back in the worktree afterwards; Undo puts the stack back and keeps the edit.",
      drag(["Extract command routing"], () => shownAt("Refactor parser init")),
      [{ button: "Stash and Continue" }, { button: "Undo" }],
    );
    await meanwhile("you take the edit to a.txt back", () => {
      assert.equal(fs.readFileSync(path.join(repo, "a.txt"), "utf8"), "file a.txt\nnot committed yet\n", "the stash was put back");
      execFileSync("git", ["checkout", "--", "a.txt"], { cwd: repo });
    });
    await press(
      "Change › edit the commit message of \"test! temporary experiment\" › Apply, then Undo",
      "The commit gets the new message and keeps its Change-Id, though the text typed had none; the commits above get new ids, nothing else changes. Undo puts the old message back.",
      async () => {
        const row = await stackRow("test! temporary experiment");
        await api.changeView.reword(row.sha, "test! a better name\n\nAnd why.", () => undefined);
      },
      [{ button: "Undo" }],
    );
    await meanwhile("you commit a rework of d.txt in the terminal", () => {
      fs.writeFileSync(path.join(repo, "d.txt"), "reworked\n", "utf8");
      execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qam", "Rework d.txt"], { cwd: repo });
    });
    await press(
      "Drag \"Rework d.txt\" to the line under \"Cleanup after experiment\"",
      "One place down: the conflict preview already knows the two cannot swap, so it says at once which commit would conflict, and that nothing changed.",
      drag(["Rework d.txt"], () => shownAt("Cleanup after experiment") + 1),
    );
    await press(
      "Drag \"Rework d.txt\" to the bottom",
      "Refused before anything is touched, saying which commit does not apply in its new place; the stack stays as it was.",
      drag(["Rework d.txt"], () => api.model.stack?.commits.length ?? 0),
    );
    await press(
      "Drag \"Cleanup after experiment\" to the line under \"Extract command routing\"",
      "It lands there, and the commits between move up one; Undo is offered.",
      drag(["Cleanup after experiment"], () => shownAt("Extract command routing") + 1),
    );
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

    // --- Reporting an issue --------------------------------------------------------------
    await press(
      "Review chains › … › Report Issue… › Copy and Open GitHub Issue",
      "The report opens first, as an untitled JSON document to read: what the three views show, with names, paths, text and ids replaced. A notification says so, and that nothing has left the machine. Copy and Open GitHub Issue puts the report on the clipboard and opens a new issue that has only the versions in it and says to paste the report.",
      async () => {
        await vscode.commands.executeCommand("ger.reportIssue");
        const report = JSON.parse(ui.copied);
        assert.equal(readSnapshot(ui.copied).stack?.rows.length, api.model.stack?.commits.length, "the report is a snapshot with Local stack's rows");
        assert.ok(report.change.message.text && report.chains.length === 3 && report.state === "ready", "and with the Change view, Review chains and the state");
        // Nothing of the fixture repository and the fake Gerrit: subjects, files, people, the project, CI's words, where it is.
        assert.equal(/Extract|routing|parser|Cleanup|experiment|uploader|\.txt|alice|testproj|Widget|pytest|ZeroDivision/i.exec(ui.copied)?.[0], undefined);
        assert.ok(!ui.copied.includes(repo) && !ui.copied.includes(path.basename(path.dirname(repo))) && !ui.copied.includes(os.userInfo().username));
        const ids = api.model.stack?.commits.flatMap((c) => [c.short_sha, c.change_id ?? c.sha]) ?? [];
        assert.deepEqual(ids.filter((id) => ui.copied.includes(id)), [], "no commit id or Change-Id");
      },
      [{ button: "Copy and Open GitHub Issue" }],
    );
    await press(
      "Command palette › ger: Report Issue… › Save to File…",
      "The report opens again to read; Save to File… asks where, suggesting ger-report.json, and the status bar says where it was saved. Nothing is copied and no browser opens.",
      exec("ger.reportIssue"),
      [{ button: "Save to File…" }, { save: "~/ger-report.json" }],
    );
    await press(
      "Command palette › ger: Report Issue… › close the report without saving › Copy and Open GitHub Issue",
      "The user may have taken things out of the report before closing it, so nothing is copied, not the original either, and no browser opens; a notification says nothing was shared and to run Report Issue… again.",
      async () => {
        ui.copied = "";
        await vscode.commands.executeCommand("ger.reportIssue");
        assert.equal(ui.copied, "", "nothing copied");
      },
      [{ closeUnsaved: true }, { button: "Copy and Open GitHub Issue" }],
    );
    {
      // The real document(), which the scripted user stands in for above: what Copy and Save read once the user has
      // edited the report and closed its tab without saving (Don't Save, here without the dialog) is nothing, not the
      // original: VS Code empties the document before it closes it, so the edits are gone too.
      const read = await vscodeUi.document('{\n  "secret": "keep me out"\n}\n', "json");
      const shown = vscode.window.activeTextEditor;
      assert.ok(shown, "the report is the active editor");
      const document = shown.document;
      await shown.edit((edit) => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), "{}\n"));
      assert.equal(read(), "{}\n", "while open: the text as it stands");
      await vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor");
      assert.ok(await until(() => document.isClosed), "the untitled document is closed");
      assert.equal(read(), undefined, "once closed without saving: nothing to share");
    }

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

      // A ger of another release: its serve API has a major this extension does not speak.
      const servedApi = process.env.GER_E2E_API ?? "";
      fs.writeFileSync(servedApi, "2.0.0\n");
      await note(
        "ger serve restarts as a ger whose API is 2.0, while the extension needs 1.0",
        "The views refuse it rather than break on it: Local stack and the Change view say ger speaks API 2.0 and the extension needs 1.0, and that the extension is what to update, with a button to install the ger that matches it; the status bar shows a warning.",
        async () => {
          api.model.restart();
          await api.model.refresh();
          assert.equal(api.model.state, "incompatible");
          assert.equal(api.model.overview, undefined, "nothing from a service it cannot read");
          await api.changeView.idle();
        },
      );
      fs.rmSync(servedApi);
      await note(
        "ger is back at the matching release, and you press Retry",
        "ger serve starts again, its API fits, and the views show the stack and the chains as before.",
        async () => {
          await vscode.commands.executeCommand("ger.refresh");
          assert.equal(api.model.state, "ready");
          await wait(10_500); // the stack's trust window, as above
          await api.model.refresh({ background: true });
          assert.ok(await until(() => api.events.live), "the event stream is followed again");
          await api.changeView.idle();
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

    // --- Rebasing in the terminal ------------------------------------------------------------
    {
      // Most of it is git in the terminal, as a user runs it, with the extension only watched; where the extension offers
      // the way on (the banner's Continue, Abort and Skip, the conflicted files, Push), it is pressed, and what it starts
      // in the ger terminal is then run as the terminal would.
      // Fixed identities and dates for what this block commits (the commits it builds on are not; their ids are
      // normalised). Where the views are wrong today, **Today** says how and names the issue that fixes it: that
      // fix is then a diff of this transcript. Afterwards main and origin/main have moved on and feature is rebased
      // onto them: steps added after this block start from there.
      const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "ger-e2e-rebase-"));
      const gitEnv = {
        ...process.env,
        GIT_AUTHOR_NAME: "Test",
        GIT_AUTHOR_EMAIL: "test@example.com",
        GIT_AUTHOR_DATE: "2026-11-12T09:00:00Z",
        GIT_COMMITTER_NAME: "Test",
        GIT_COMMITTER_EMAIL: "test@example.com",
        GIT_COMMITTER_DATE: "2026-11-12T09:00:00Z",
        GIT_EDITOR: "true",
      };
      const gitRun = (args: string[], env: NodeJS.ProcessEnv = {}, input?: string): string =>
        execFileSync("git", args, { cwd: repo, env: { ...gitEnv, ...env }, input, stdio: ["pipe", "pipe", "pipe"] }).toString().trim();
      /** What `ger` printed in the ger terminal during the step (gerInTerminal), for the transcript. */
      const terminalSaid: string[] = [];
      /** Run `ger <args>` as the ger terminal would (the fake: the real command over the fake Gerrit); what it prints goes into the step. */
      const gerInTerminal = (args: string[]): number => {
        const ran = spawnSync(gerCommandLine(args), { cwd: repo, env: gitEnv, shell: true, encoding: "utf8" });
        const printed = `${ran.stdout}${ran.stderr}`.split("\n").filter((line) => line.trim());
        terminalSaid.push(...printed.map((line) => `ger terminal | ${line}`), `ger terminal | (exit ${ran.status})`);
        return ran.status ?? -1;
      };
      /** A git command that stops the rebase on a conflict: it exits non-zero, which is what is meant. */
      const gitStops = (args: string[]) => assert.throws(() => gitRun(args), Error, `git ${args.join(" ")} stops on a conflict`);
      /** `ger edit`'s rebase: interactive onto the fork point, no autosquash, stopping at the commit with this subject. */
      const editAt = (subject: string) =>
        gitRun(["rebase", "-i", "--no-autosquash", gitRun(["merge-base", "HEAD", "@{upstream}"])], {
          GIT_SEQUENCE_EDITOR: `sed -i -e "/ ${subject}$/s/^pick/edit/"`,
        });
      const write = (file: string, content: string) => fs.writeFileSync(path.join(repo, file), content, "utf8");
      const stackHeader = () => (api.stackView as unknown as { view?: vscode.WebviewView }).view?.description ?? "(view not drawn)";
      const below = (sha: string, rev: string) => {
        try {
          gitRun(["merge-base", "--is-ancestor", sha, rev]);
          return true;
        } catch {
          return false;
        }
      };
      /** Where a commit is in git now: during a rebase, the branch still points where it was. */
      const whereIs = (sha: string | undefined): string =>
        !sha ? "none" : sha === gitRun(["rev-parse", "HEAD"]) ? "HEAD" : below(sha, "HEAD") ? "below HEAD" : below(sha, "feature") ? "only on the branch as it was before" : "gone from both HEAD and the branch";
      const shownCommit = (): string => whereIs(api.changeView.diff?.commit);
      /** What the three places this is about show now, in full: the status bar, Local stack with its header, the Change view. */
      const shown = async () =>
        [
          `status bar | ${status()}`,
          `stack header | ${stackHeader()}`,
          ...api.stackView.lines().map((line) => `stack  | ${line}`),
          // Row ids are normalised away: say where each row's commit is, so "still the old commit" shows.
          ...(api.model.stack?.commits ?? []).map((c) => `stack  | (${c.summary} is ${whereIs(c.sha)})`),
          `change | title: ${api.changeView.heading ?? "(view not drawn)"}`,
          ...(await api.changeView.lines()).map((line) => `change | ${line}`),
          `change | (the commit shown is ${shownCommit()})`,
          // The extension's diff tabs, in every group: whether one is stale is in its title.
          ...vscode.window.tabGroups.all
            .flatMap((group) => group.tabs)
            .filter((tab) => tab.input instanceof vscode.TabInputTextDiff && tab.input.original.scheme === "ger-file")
            .map((tab) => `diff tab | ${tab.label} (${(tab.input as vscode.TabInputTextDiff).modified.scheme} on the right${tab.isDirty ? ", unsaved" : ""}${tab.isActive ? ", active" : ""})`),
        ].map(normalize);
      let n = 0;
      /** One step of this block: something done in the terminal, or (`pressed`) a button of the extension, numbered as the presses are. */
      const inTerminal = async (what: string, expect: string, today: string | undefined, action: () => unknown, answers: Answer[] = [], pressed = false) => {
        step += pressed ? 1 : 0;
        ui.script(answers);
        await action();
        await wait(1_000); // past the watcher's quiet time, so the refresh it starts is not still to come
        await settle();
        const after = await screen();
        const said = ui.drain();
        const changed = lineDiff(before, after);
        out.push(pressed ? `## ${step}. ${what}` : `## Meanwhile: ${what}`, "", `**Expect:** ${expect}`, "", ...(today ? [`**Today:** ${today}`, ""] : []), "```");
        out.push(...said.lines, ...terminalSaid.splice(0).map(normalize), ...(changed.length ? changed : ["(no change)"]), "--- shown now:", ...(await shown()), "```", "");
        problems.push(...said.problems.map((problem) => `${what}: ${problem}`));
        before = after;
        n += 1;
        await snap(step, `rebase ${n}`);
      };
      /** Press a button, then let the ger terminal run what it started: ScriptedUi only writes the command down, so this runs the rebase step it stands for. */
      const pressAndRun = (button: string, expect: string, today: string | undefined, command: string, terminal: string[] | undefined, answers: Answer[] = []) =>
        inTerminal(
          button,
          expect,
          today,
          async () => {
            await exec(command)();
            if (terminal) {
              gitRun(terminal);
            }
          },
          answers,
          true,
        );
      /** What Local stack's page posts when a conflicted file under the banner is clicked. */
      const stackPage = api.stackView as unknown as { receive(message: { type: string; [field: string]: unknown }): Promise<unknown> };

      await press(
        "Click \"test! temporary experiment\" in Local stack",
        "The Change view shows change 103 as it is now: the commit is the same as on Gerrit. It should have shown that already after ger serve restarted (it kept Change 102's heading and \"ahead of Gerrit\", no issue yet); this click makes the rebase steps start from a fresh view.",
        exec("ger.openChange", await stackRow("test! temporary experiment")),
      );

      // A ger edit-style stop on a commit, amend, continue.
      await inTerminal(
        "you start ger edit's rebase on \"test! temporary experiment\", and it stops there",
        "Local stack says a rebase is underway, with Continue and Abort, and marks that commit as where it stopped, with HEAD on it; the commits below are done, \"Add e.txt\" is dimmed, still to be replayed. The header says the branch is being rebased. The Change view of 103 says its message cannot be edited during the rebase. The status bar says the rebase stopped at 103.",
        undefined,
        () => editAt("test! temporary experiment"),
      );
      await inTerminal(
        "you add a line to c.txt and amend the stopped commit",
        "The stopped commit shows what it is now: a new SHA, HEAD, its code changed since the last push. The Change view follows it to the amended commit: c.txt with +2 lines.",
        undefined,
        () => {
          write("c.txt", "file c.txt\namended at the stop\n");
          gitRun(["commit", "-q", "--amend", "--no-edit", "-a"]);
        },
      );
      await pressAndRun(
        "Local stack › Continue (the rebase stopped at the amended commit)",
        "ger rebase --continue runs in the ger terminal, and the status bar says so; once it has run, the banner and the marks go: the branch is back, the amended commit with its code changed since the last push and \"Add e.txt\" on top of it; the header names the branch; the Change view shows the amended commit, its message editable again.",
        undefined,
        "ger.rebase.continue",
        ["rebase", "--continue"],
      );

      // A conflict while continuing, resolved.
      await inTerminal(
        "you stop at \"Refactor parser init\" the same way, and amend it to also add e.txt",
        "Local stack marks the rebase stopped at that commit, which shows its new code (its code changed since the last push) and HEAD; the two above are dimmed, still to be replayed. The status bar says the rebase stopped at 102.",
        undefined,
        () => {
          editAt("Refactor parser init");
          write("e.txt", "from the amend\n");
          gitRun(["add", "e.txt"]);
          gitRun(["commit", "-q", "--amend", "--no-edit"]);
        },
      );
      await inTerminal(
        "you run git rebase --continue, and \"Add e.txt\" conflicts in e.txt",
        "Local stack says the rebase stopped on a conflict, lists e.txt under the banner, with Abort and Skip (no Continue while e.txt is conflicted); \"Add e.txt\" has a red conflict icon and is not dimmed; the commits below are done, showing what they are now. The status bar says Conflict in \"Add e.txt\".",
        undefined,
        () => gitStops(["rebase", "--continue"]),
      );
      await inTerminal(
        "Local stack › click e.txt under the banner",
        "e.txt opens in VS Code's merge editor (its file in the worktree is what it writes); nothing else changes.",
        undefined,
        () => stackPage.receive({ type: "conflict", path: "e.txt" }),
        [],
        true,
      );
      await inTerminal(
        "you resolve e.txt and git add it",
        "Nothing is conflicted any more: e.txt leaves the list and the conflict icon goes; the rebase is still stopped at \"Add e.txt\", ready to Continue, which now appears as the prominent button.",
        undefined,
        async () => {
          // Resolved by hand here, not in the merge editor: close it without keeping what it put in e.txt.
          await vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor");
          write("e.txt", "from the amend\nfrom the terminal\n");
          gitRun(["add", "e.txt"]);
        },
      );
      await pressAndRun(
        "Local stack › Continue (the conflict resolved)",
        "ger rebase --continue runs in the ger terminal; once it has run, the banner and the marks go; Local stack shows the branch with \"Refactor parser init\" changed here since the last push and \"Add e.txt\" on top.",
        undefined,
        "ger.rebase.continue",
        ["rebase", "--continue"],
      );

      // The upstream moves on.
      await inTerminal(
        "somebody adds c.txt on main, and you git fetch, so origin/main moves on",
        "Local stack says origin/main has 1 new commit, offers Rebase onto origin/main, and warns that \"test! temporary experiment\" would conflict in c.txt; the commits themselves are unchanged. The status bar does not change: an upstream that moved on is not urgent.",
        undefined,
        () => {
          const index = path.join(scratch, "index");
          const blob = gitRun(["hash-object", "-w", "--stdin"], {}, "file c.txt, from main\n");
          gitRun(["read-tree", "main"], { GIT_INDEX_FILE: index });
          gitRun(["update-index", "--add", "--cacheinfo", `100644,${blob},c.txt`], { GIT_INDEX_FILE: index });
          const tree = gitRun(["write-tree"], { GIT_INDEX_FILE: index });
          const commit = gitRun(["commit-tree", tree, "-p", "main", "-m", "Add c.txt on main"], { GIT_AUTHOR_NAME: "alice", GIT_AUTHOR_EMAIL: "alice@example.com" });
          gitRun(["update-ref", "refs/heads/main", commit]);
          gitRun(["fetch", "-q", "origin", "main"]);
        },
      );
      await inTerminal(
        "Local stack › Rebase onto origin/main (\"test! temporary experiment\" conflicts in c.txt, as the row said)",
        "ger rebase origin/main runs in the ger terminal, and the status bar says so; once it has run, the upstream row goes and Local stack says the rebase stopped on a conflict in c.txt, with Abort and Skip; \"test! temporary experiment\" has a red conflict icon, not dimmed; the three below are replayed onto the new origin/main and show their new SHAs; \"Add e.txt\" is dimmed. The Change view of 103 says \"Conflict in c.txt\" and lists it under Conflicts, without the vote explanation. The status bar says Conflict in 103.",
        undefined,
        async () => {
          await exec("ger.rebase.upstream")();
          // What the ger terminal runs: ger rebase's todo, saved as it is, is git rebase onto the upstream.
          gitStops(["rebase", "origin/main"]);
        },
        [],
        true,
      );
      await pressAndRun(
        "Local stack › Skip › Cancel",
        "A dialog asks before \"test! temporary experiment\" is left out: the commit and the conflict resolution so far would be thrown away, Abort brings back the branch as before the rebase but not the resolution, and its change stays open on Gerrit. Cancelled, nothing runs and nothing changes.",
        undefined,
        "ger.rebase.skip",
        undefined,
        [{ dismiss: true }],
      );
      await pressAndRun(
        "Local stack › Push to Gerrit… (during the rebase) › Abort",
        "No ger push: a dialog says to finish the rebase first, why (in ger push's own words), and offers only Abort: Continue is not a way on while c.txt is conflicted. Abort runs ger rebase --abort in the ger terminal; once it has run, everything is as before the rebase: the branch, its commits, the header; Local stack still says origin/main has 1 new commit.",
        undefined,
        "ger.push",
        ["rebase", "--abort"],
        [{ button: "Abort" }],
      );

      // Diff tabs of commits the rebase rewrites (#69): Continue saves first, asking; a tab of a rewritten commit is marked stale, or closed.
      const fileOfShown = (name: string) => api.changeView.diff?.files.find((file) => file.path === name);
      const openFile = (name: string) => async () => exec("ger.openDiff", fileOfShown(name), api.changeView.diff)();
      const openRow = (summary: string) => async () => exec("ger.openChange", await stackRow(summary))();
      await inTerminal(
        "you stop at \"Refactor parser init\" with ger edit's rebase",
        "Local stack marks the rebase stopped at \"Refactor parser init\", HEAD on it; the two above are dimmed, still to be replayed.",
        undefined,
        () => editAt("Refactor parser init"),
      );
      await inTerminal("Local stack › click \"Add e.txt\" (still to be replayed)", "The Change view shows \"Add e.txt\" as it was: it changes e.txt.", undefined, openRow("Add e.txt"), [], true);
      await inTerminal(
        "Change › click e.txt under Files",
        "The diff of e.txt in \"Add e.txt\" opens, its title naming the commit; both sides are that commit's, as it is not the one you stand on. You keep it open (double-click its tab), so the next file opened does not take its place.",
        undefined,
        async () => {
          await openFile("e.txt")();
          await vscode.commands.executeCommand("workbench.action.keepEditor");
        },
        [],
        true,
      );
      await inTerminal("Local stack › click \"Refactor parser init\" (where it stopped)", "The Change view shows the commit you stand on; the diff tab of \"Add e.txt\" stays as it is.", undefined, openRow("Refactor parser init"), [], true);
      await inTerminal(
        "Change › click e.txt under Files",
        "e.txt opens on its own (the commit adds it), as your file: you stand on this commit.",
        undefined,
        openFile("e.txt"),
        [],
        true,
      );
      await inTerminal(
        "you type a line at the top of e.txt, and do not save it",
        "e.txt has unsaved edits; nothing else changes.",
        undefined,
        async () => {
          const active = vscode.window.activeTextEditor;
          assert.ok(active && active.document.uri.scheme === "file" && path.basename(active.document.uri.fsPath) === "e.txt", "e.txt is the active editor");
          assert.ok(await active.edit((edit) => edit.insert(new vscode.Position(0, 0), "added in the editor\n")));
        },
      );
      await inTerminal(
        "Local stack › Continue (e.txt not saved) › Save All",
        "Before anything runs, a dialog names e.txt, says ger rebase --continue sees only the files on disk and then says what saved edits still need, and offers Save All, Continue without saving (git would rewrite e.txt under the edits) and Cancel. Save All pressed, e.txt is saved, then ger rebase --continue runs in the ger terminal, where it refuses: e.txt is changed and not committed, amend it in or stash it. The rebase stays where it stopped.",
        undefined,
        async () => {
          await exec("ger.rebase.continue")();
          assert.equal(fs.readFileSync(path.join(repo, "e.txt"), "utf8"), "added in the editor\nfrom the amend\n", "e.txt is saved");
          assert.equal(gerInTerminal(["rebase", "--continue"]), 2, "ger rebase --continue refuses over e.txt changed and not committed");
        },
        [{ button: "Save All" }],
        true,
      );
      await inTerminal(
        "you amend \"Refactor parser init\" with e.txt, and git rebase --continue",
        "The rebase finishes: \"Refactor parser init\" and the two above it have new SHAs. The diff tab of \"Add e.txt\" stays open, as it still shows that commit as it was, and its title says it is stale.",
        undefined,
        () => {
          gitRun(["commit", "-q", "--amend", "--no-edit", "-a"]);
          gitRun(["rebase", "--continue"]);
        },
      );
      try {
        await inTerminal(
          "Settings › ger.closeDiffsOnRebase: on",
          "The stale diff tab of \"Add e.txt\" closes.",
          undefined,
          () => vscode.workspace.getConfiguration("ger").update("closeDiffsOnRebase", true, vscode.ConfigurationTarget.Global),
          [],
          true,
        );
        await inTerminal("Local stack › click \"Add e.txt\"", "The Change view shows \"Add e.txt\", now HEAD.", undefined, openRow("Add e.txt"), [], true);
        await inTerminal(
          "Change › click e.txt under Files",
          "The diff of e.txt in \"Add e.txt\" opens with your file on its right: you stand on this commit.",
          undefined,
          openFile("e.txt"),
          [],
          true,
        );
        const eTxt = vscode.Uri.file(path.join(repo, "e.txt"));
        await inTerminal(
          "you type a line at the top of e.txt in that diff, and do not save it",
          "The diff tab of \"Add e.txt\" has unsaved edits; nothing else changes.",
          undefined,
          async () => {
            const edit = new vscode.WorkspaceEdit();
            edit.insert(eTxt, new vscode.Position(0, 0), "typed in the diff\n");
            assert.ok(await vscode.workspace.applyEdit(edit));
          },
        );
        await inTerminal(
          "you rebase onto origin/main, and \"test! temporary experiment\" conflicts in c.txt",
          "Local stack says the rebase stopped on a conflict in c.txt. \"Add e.txt\" is still to be replayed, but its diff tab has your file on the right, which is now the worktree of the stopped rebase, not that commit: its title says it is stale. It is not closed, though ger.closeDiffsOnRebase is on: it holds unsaved edits, which are kept.",
          "the Change view still explains votes and shows the commit as it was (#59).",
          () => gitStops(["rebase", "origin/main"]),
        );
        await inTerminal(
          "you resolve c.txt and git add it",
          "Nothing is conflicted any more; the rebase is ready to Continue.",
          undefined,
          () => {
            write("c.txt", "file c.txt, from main\namended at the stop\n");
            gitRun(["add", "c.txt"]);
          },
        );
        const notes = vscode.Uri.file(path.join(repo, "notes.txt"));
        const tabsOf = (uri: vscode.Uri): vscode.Tab[] =>
          vscode.window.tabGroups.all.flatMap((group) => group.tabs).filter((tab) => tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === uri.toString());
        await inTerminal(
          "you create notes.txt, a file git does not track, and an extension edits it without saving",
          "Nothing in the views changes: notes.txt is no part of the stack. VS Code opens a tab of it behind the others, as it does for every file with unsaved edits.",
          undefined,
          async () => {
            write("notes.txt", "");
            await vscode.workspace.openTextDocument(notes);
            const edit = new vscode.WorkspaceEdit();
            edit.insert(notes, new vscode.Position(0, 0), "a note\n");
            assert.ok(await vscode.workspace.applyEdit(edit));
            assert.ok(await until(() => tabsOf(notes).length === 1), "VS Code opened a tab of notes.txt");
            assert.ok(!tabsOf(notes)[0].isActive, "behind the others");
          },
        );
        await pressAndRun(
          "Local stack › Continue (e.txt and notes.txt not saved) › Cancel",
          "A dialog names e.txt, whose edits are only in the stale diff tab, and notes.txt, though git does not track it (asking once too often loses nothing), and offers Save All, Continue without saving and Cancel. Cancelled, nothing is saved and nothing runs: the rebase stays stopped, the tab stale and unsaved.",
          undefined,
          "ger.rebase.continue",
          undefined,
          [{ dismiss: true }],
        );
        await inTerminal("you save notes.txt, close its tab and delete it", "Nothing changes.", undefined, async () => {
          const document = vscode.workspace.textDocuments.find((open) => open.uri.toString() === notes.toString());
          assert.ok(document && (await document.save()), "notes.txt is saved");
          await vscode.window.tabGroups.close(tabsOf(notes));
          fs.rmSync(notes.fsPath);
        });
        await inTerminal(
          "you revert e.txt in the stale diff tab (File › Revert File)",
          "The stale diff tab of \"Add e.txt\" has no unsaved edits any more, so with ger.closeDiffsOnRebase on it closes once the views refresh; the editor of e.txt opened earlier is in front again.",
          undefined,
          async () => {
            await vscode.commands.executeCommand("workbench.action.files.revert");
            const document = vscode.workspace.textDocuments.find((open) => open.uri.toString() === eTxt.toString());
            assert.ok(document && !document.isDirty, "e.txt is reverted");
          },
        );
        await pressAndRun(
          "Local stack › Continue (ger.closeDiffsOnRebase on)",
          "Nothing is unsaved, so nothing is asked: ger rebase --continue runs in the ger terminal; once it has run, the rebase is finished, \"Add e.txt\" is replayed onto origin/main with a new SHA. The Change view follows \"Add e.txt\" to its new commit.",
          "the Change view loses \"Add e.txt\" and shows nothing: it follows a rewritten commit by its Change-Id, and this one has none (no issue yet).",
          "ger.rebase.continue",
          ["rebase", "--continue"],
        );
      } finally {
        await vscode.workspace.getConfiguration("ger").update("closeDiffsOnRebase", undefined, vscode.ConfigurationTarget.Global);
      }
      fs.rmSync(scratch, { recursive: true, force: true });
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
    camera?.close();
    const target = process.env.GER_E2E_OUT;
    if (target) {
      fs.writeFileSync(target, out.join("\n"), "utf8");
    }
  }
}
