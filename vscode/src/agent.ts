// Handing work to a coding agent: the runbook of the local stack (or one commit), or the
// comments and CI findings of someone else's change. Shown first; then started or copied.

import { execFile } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { setting } from "./config";
import { plural } from "./format";
import { executable } from "./launch";
import { ChangeTarget, Model, describeError } from "./model";
import { ui } from "./ui";

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

/** Put what goes to the agent in front of the user, then start the agent on it or copy it. */
async function handOver(repo: string, markdown: string, name: string, summary: string, rev: string | undefined): Promise<void> {
  const file = path.join(os.tmpdir(), name);
  await fs.writeFile(file, markdown, "utf8");
  await vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(file));
  const template = setting("agentCommand", "").trim();
  const start = template ? "Start Agent" : "Copy to Clipboard";
  const detail = "The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.";
  if ((await ui().message("info", summary, { modal: true, detail }, [start])) !== start) {
    return;
  }
  if (!template) {
    await ui().copy(markdown);
    void ui().message("info", "Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.", {}, []);
    return;
  }
  // GER_NO_PUBLISH makes ger refuse to publish drafts, whatever the agent decides to try.
  ui().terminal({ name: "ger agent", cwd: repo, env: { GER_NO_PUBLISH: "1" } }, template.replaceAll("{rev}", rev ?? "").replaceAll("{brief}", file));
}

/** The runbook for the local stack, or one of its commits, handed to the agent. */
export async function runAgent(model: Model, rev: string | undefined): Promise<void> {
  try {
    const client = await model.client();
    const runbook = await ui().progress(vscode.ProgressLocation.Notification, "Collecting comments and CI findings", () => client.runbook(rev));
    const { commits, comments, findings, held } = runbook.totals;
    const kept = held ? ` ${plural(held, "comment")} kept for you.` : "";
    if (!commits) {
      void ui().message("info", `Nothing here for an agent.${kept}`, {}, []);
      return;
    }
    const summary = `${plural(commits, "commit")}: ${plural(comments, "comment")} and ${plural(findings, "CI finding")} for the agent.${kept}`;
    await handOver(model.repo, runbook.markdown, `ger-runbook-${Date.now()}.md`, summary, rev);
  } catch (error) {
    void ui().message("warning", `Nothing handed over: ${describeError(error)}`, {}, []);
  }
}

/** One change to the agent: a commit of your stack gets its runbook, any other change its comments and findings. */
export async function handToAgent(model: Model, target: ChangeTarget | undefined): Promise<void> {
  if (!target) {
    return;
  }
  if (target.local) {
    await runAgent(model, target.rev);
    return;
  }
  try {
    const brief = await ui().progress(vscode.ProgressLocation.Notification, "Collecting comments and CI findings", () => agentBrief(model.repo, target.rev));
    const name = `ger-brief-${target.rev.replace(/[^\w.-]/g, "_").slice(0, 40)}.md`;
    await handOver(model.repo, brief, name, `The comments and CI findings of ${target.title}, for the agent.`, target.rev);
  } catch (error) {
    void ui().message("warning", `Nothing handed over: ${describeError(error)}`, {}, []);
  }
}
