// Runs inside a real VS Code (see run.ts): activates the extension against fake_ger.py and walks
// every view the way a user would expand it. Asserts the essentials and writes what the views
// showed to GER_E2E_OUT, so a change in what users see is a visible diff.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { fileUri } from "../../src/changeView";
import type { ExtensionApi } from "../../src/extension";

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

async function find<T>(provider: Provider<T>, wanted: string, node?: T): Promise<T | undefined> {
  for (const child of (await provider.getChildren(node)) ?? []) {
    if (text((await provider.getTreeItem(child)).label) === wanted) {
      return child;
    }
    const below = await find(provider, wanted, child);
    if (below) {
      return below;
    }
  }
  return undefined;
}

export async function run(): Promise<void> {
  const out: string[] = [];
  const section = (title: string, lines: string[]) => out.push(`## ${title}`, ...lines, "");
  try {
    const extension = vscode.extensions.getExtension<ExtensionApi>("gerrit-workflow-tools.ger-vscode");
    assert.ok(extension, "the extension is installed in the development host");
    const api = await extension.activate();
    assert.ok(api, "the extension activated on the workspace folder");
    await api.model.refresh();
    assert.equal(api.model.failure, undefined, "ger serve started and answered");

    // Every command the manifest offers is registered.
    const declared: string[] = extension.packageJSON.contributes.commands.map((c: { command: string }) => c.command);
    const registered = new Set(await vscode.commands.getCommands(true));
    assert.deepEqual(declared.filter((command) => !registered.has(command)), []);

    section("Status bar", [api.model.overview?.headline?.text ?? "(no headline)"]);
    const overview = await dump(api.overviewView);
    section("Review chains", overview);
    assert.ok(overview.some((line) => line.startsWith("CI failed on 102")), "the headline leads the overview");
    assert.ok(overview.some((line) => line.startsWith("Outgoing")) && overview.some((line) => line.startsWith("To review")));

    const stack = await dump(api.stackView);
    section("Local stack", stack);
    assert.equal(stack.length, 4);
    assert.match(stack[0], /^Cleanup after experiment — HEAD/, "newest first, HEAD marked");

    // A commit of yours with comments: files, both comment chains, no CI group.
    const commented = await find(api.stackView, "Extract command routing");
    assert.ok(commented);
    await vscode.commands.executeCommand("ger.openChange", commented);
    section("Change: your commit with comments", await dump(api.changeView));
    assert.equal(api.changeView.unresolved().length, 2);
    assert.deepEqual(api.changeView.diff?.files.map((f) => f.path), ["b.txt"]);

    // Stepping to a comment opens the file at its line, and the editor shows the thread.
    await vscode.commands.executeCommand("ger.nextComment");
    const editor = vscode.window.activeTextEditor;
    assert.equal(path.basename(editor?.document.fileName ?? ""), "b.txt");
    assert.equal(editor?.selection.active.line, 0);

    // Clicking a file opens a diff, parent on the left.
    await vscode.commands.executeCommand("ger.openDiff", api.changeView.diff?.files[0], api.changeView.diff);
    const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
    assert.ok(tab?.input instanceof vscode.TabInputTextDiff, "a diff editor is open");
    assert.equal((tab.input as vscode.TabInputTextDiff).original.scheme, "ger-file");
    section("Diff tab", [tab.label]);

    // A commit with failed CI: the CI group fetches findings and marks what it introduced.
    const failing = await find(api.stackView, "test! temporary experiment");
    await vscode.commands.executeCommand("ger.openChange", failing);
    const change = await dump(api.changeView);
    section("Change: your commit with failed CI", change);
    assert.ok(change.some((line) => line.includes("introduced here")), "introduced findings are marked");

    // Someone else's change: a chain of one is the change itself, nothing to expand.
    const theirs = await find(api.overviewView, "Add retry to the uploader");
    assert.ok(theirs);
    assert.deepEqual(await dump(api.overviewView, theirs), []);
    await vscode.commands.executeCommand("ger.openChange", theirs);
    assert.equal(api.changeView.target?.number, 900);
    // (The fake Gerrit only knows change 900 as a query row, so its detail is an error here.)

    // Keeping a comment for yourself shows on the thread and stays out of the agent runbook.
    await vscode.commands.executeCommand("ger.openChange", commented);
    const design = await find(api.changeView, "Is this the right design?");
    await vscode.commands.executeCommand("ger.hold", design);
    const client = await api.model.client();
    const runbook = await client.runbook();
    section("Change: one comment kept for you", await dump(api.changeView));
    section("Agent runbook", runbook.markdown.split("\n"));
    assert.deepEqual([runbook.totals.comments, runbook.totals.held], [1, 1]);

    // New comments, as the "+" in the gutter starts them: the box's text and the thread's place.
    const reply = (uri: vscode.Uri, line: number, text: string): vscode.CommentReply => ({
      thread: { uri, range: new vscode.Range(line, 0, line, 0), dispose: () => undefined } as unknown as vscode.CommentThread,
      text,
    });
    const states = () => api.changeView.unresolved().flatMap((chain) => chain.comments.map((c) => `${c.state}: ${c.message}`));
    // Gerrit has this very commit: a new comment is a draft there.
    await vscode.commands.executeCommand("ger.openChange", await find(api.stackView, "Extract command routing"));
    assert.equal(api.changeView.shown?.new_comment, "draft");
    const routed = api.changeView.diff?.commit ?? "";
    await api.threads.saveNew(reply(fileUri(routed, "b.txt"), 0, "Typo here."), "draft");
    assert.ok(states().includes("draft: Typo here."), "the draft comes back in its chain");

    // A rewrite gives the commits new ids; the shown change follows by Change-Id.
    const before = api.model.stack?.commits.map((c) => c.sha) ?? [];
    api.model.setStack(await client.rewrite([before[1], before[0], before[2], before[3]]));
    await api.model.refresh();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    section("Local stack after moving a commit", await dump(api.stackView));
    assert.equal(api.changeView.shown?.summary, "Extract command routing");
    assert.notEqual(api.changeView.target?.rev, before[1]);

    // The conflict preview: a commit that reworks d.txt cannot go below the one that adds it.
    assert.ok(!(await dump(api.stackView)).some((line) => /\.(up|down)x/.test(line)), "four independent commits: every move is offered");
    const repo = vscode.workspace.workspaceFolders?.[0].uri.fsPath ?? "";
    fs.writeFileSync(path.join(repo, "d.txt"), "reworked\n", "utf8");
    execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qam", "Rework d.txt"], { cwd: repo });
    await api.model.refresh();
    const blocked = await dump(api.stackView);
    section("Local stack with a move that would conflict", blocked);
    assert.match(blocked[0], /^Rework d\.txt .*\.downx\]$/, "the arrow down is replaced by the blocked marker");
    assert.match(blocked[1], /^Cleanup after experiment .*\.upx\.down\]$/);
    assert.ok(blocked.slice(2).every((line) => !/x\b/.test(line.split("[")[1] ?? "")));

    // Drag and drop: the dragged commit takes the place of the one it lands on, downwards too.
    const shown = () => [...(api.model.stack?.commits ?? [])].reverse().map((c) => c.summary);
    const row = async (summary: string) => {
      const found = await find(api.stackView, summary);
      assert.ok(found, summary);
      return found;
    };
    const dragged = async (summary: string) => {
      const data = new vscode.DataTransfer();
      api.stackView.handleDrag([await row(summary)], data);
      return data;
    };
    const head = api.model.stack?.head;
    // Onto the commit it would conflict with: refused before anything is touched.
    await api.stackView.handleDrop(await row("Cleanup after experiment"), await dragged("Rework d.txt"));
    assert.equal(api.model.stack?.head, head, "a drop that would conflict leaves the stack as it was");
    const below = shown()[2];
    // Not awaited: the rewrite then waits on its Undo notification.
    void api.stackView.handleDrop(await row(below), await dragged("Cleanup after experiment"));
    for (let tries = 0; tries < 50 && shown()[2] !== "Cleanup after experiment"; tries++) {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    section("Local stack after dropping a commit on the one below it", await dump(api.stackView));
    assert.deepEqual(shown().slice(1, 3), [below, "Cleanup after experiment"]);

    // New comments where Gerrit does not have the commit as it is now.
    // Rewritten by the drop above, so Gerrit has not got it: a local draft, and a private note.
    await vscode.commands.executeCommand("ger.openChange", await row("Cleanup after experiment"));
    assert.equal(api.changeView.shown?.new_comment, "local-draft");
    const cleanup = api.changeView.diff?.commit ?? "";
    const file = api.changeView.diff?.files[0]?.path ?? "";
    await api.threads.saveNew(reply(fileUri(cleanup, file), 0, "Explain why."), "local-draft");
    await api.threads.saveNew(reply(fileUri(cleanup, file), 0, "Ask Alice."), "note");
    section("Change: new comments on a commit Gerrit does not have", await dump(api.changeView));
    assert.ok(states().includes("local-draft: Explain why.") && states().includes("note: Ask Alice."));

    // A branch without an upstream: the stack view offers the suggested one in one click.
    execFileSync("git", ["branch", "--unset-upstream"], { cwd: repo });
    await api.model.refresh();
    const unset = await dump(api.stackView);
    section("Local stack of a branch without upstream", unset);
    assert.match(unset[0], /^feature has no upstream, so ger cannot tell where your stack starts/);
    assert.match(unset[1], /^Track origin\/\S+ — /, "the suggestion, with why, in one click");
    assert.match(unset[2], /^Choose another upstream branch/);
    await vscode.commands.executeCommand("ger.setUpstream", "origin/main");
    const tracked = await dump(api.stackView);
    assert.ok(tracked.length >= 4 && tracked.every((line) => !line.startsWith("Track ")), "the stack is back");
  } catch (error) {
    out.push("## FAILED", error instanceof Error ? (error.stack ?? error.message) : String(error), "");
    throw error;
  } finally {
    const target = process.env.GER_E2E_OUT;
    if (target) {
      fs.writeFileSync(target, out.join("\n"), "utf8");
    }
  }
}
