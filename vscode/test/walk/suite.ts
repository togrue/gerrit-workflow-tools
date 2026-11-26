// The walk (test/walk/README.md): the review loop and the rework loop, step by step, in a real
// VS Code against a real Gerrit, with tracing on. Each step is given as a user gives it where it
// can be: clicks and keys through the window's DevTools protocol (cdp.ts). Where the target is not
// found, the step runs the command instead, and its record says so (`input`). Each step's record
// (`kind: "step"`) goes into the trace beside the extension's own records, with a picture.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import type { ExtensionApi } from "../../src/extension";
import { activeTracer, now } from "../../src/trace";
import { vscodeUi } from "../../src/ui";
import { Camera, pictureName } from "../e2e/picture";
import { Box, Window, middle } from "./cdp";
import { Budget, Loop, StepRecord } from "./scorecard";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** What one step's input was: counted as a user would give it. */
class Input {
  clicks = 0;
  keys = 0;
  typed = 0;
  readonly given: string[] = [];
  real = true;
  readonly fallbacks: string[] = [];
  /** When the first and the last input were given. */
  first: number | undefined;
  last = now();

  private mark(): void {
    this.last = now();
    this.first ??= this.last;
  }

  constructor(private readonly window: Window) {}

  async click(box: Box | null, what: string, count = 1): Promise<boolean> {
    if (!box) {
      return false;
    }
    await this.window.click(middle(box), count);
    this.mark();
    this.clicks += count;
    this.given.push(`${count > 1 ? "double-" : ""}click ${what}`);
    return true;
  }

  async key(spec: string): Promise<void> {
    await this.window.key(spec);
    this.mark();
    this.keys += 1;
    this.given.push(`key ${spec}`);
  }

  async type(text: string): Promise<void> {
    await this.window.type(text);
    this.mark();
    this.typed += 1;
    this.given.push(`type "${text.length > 30 ? `${text.slice(0, 30)}…` : text}"`);
  }

  /** The command a user's click or key runs, when the click could not be given: counted as that one input. */
  async command(id: string, why: string, as: "click" | "key", ...args: unknown[]): Promise<void> {
    this.real = false;
    this.fallbacks.push(`${id} (${why})`);
    this[as === "click" ? "clicks" : "keys"] += 1;
    this.given.push(`command ${id}`);
    this.mark();
    // As a click does not wait for what it starts: a command that asks a question waits for its answer, which a later step gives.
    void vscode.commands.executeCommand(id, ...args).then(undefined, () => undefined);
    await wait(50);
  }
}

interface StepSpec {
  loop: Loop;
  title: string;
  budget: Budget;
  /** Where the user's eyes and hands are during the step, in order: each change of place is a context switch. */
  where: string[];
  /** Done once this holds (polled). */
  until?: () => boolean | Promise<boolean>;
  timeoutMs?: number;
}

export async function run(): Promise<void> {
  const shots = process.env.GER_WALK_SHOTS ?? "";
  const tracer = activeTracer();
  if (!tracer) {
    throw new Error("walk: tracing is off (GER_TRACE)");
  }
  const extension = vscode.extensions.getExtension<ExtensionApi>("gerrit-workflow-tools.ger-vscode");
  const api = await extension?.activate();
  if (!api) {
    throw new Error("walk: the extension did not start");
  }
  const notes: string[] = [];
  const note = (line: string) => {
    notes.push(line);
    tracer.write({ kind: "note", text: line });
  };
  for (let i = 0; i < 1200 && (api.model.state !== "ready" || !api.model.overview); i++) {
    await wait(100);
  }
  // VS Code answers every modal dialog as cancelled while it runs extension tests, which the walk
  // is: a modal question is asked as a notification with the same buttons instead, clicked there.
  // What the status bar said last, and when: an answer too (Next Unresolved Comment says where it is, or that none is left).
  let said = { text: "", at: 0 };
  api.useUi({
    ...vscodeUi,
    status: (text, milliseconds) => {
      said = { text, at: now() };
      vscodeUi.status(text, milliseconds);
    },
    message: (level, text, options, buttons) =>
      options.modal ? vscodeUi.message(level, options.detail ? `${text} ${options.detail}` : text, {}, buttons) : vscodeUi.message(level, text, options, buttons),
  });
  note("modal dialogs are shown as notifications with the same buttons: VS Code skips dialogs while it runs extension tests");
  const window = await Window.open(process.env.GER_WALK_DEVTOOLS ?? "");
  const camera = await Camera.open(process.env.GER_WALK_DEVTOOLS ?? "");
  // Set the scene, not measured: the Explorer in front, the side bar wide enough to read.
  await vscode.commands.executeCommand("workbench.action.closeAuxiliaryBar").then(undefined, () => undefined);
  await vscode.commands.executeCommand("workbench.view.extension.ger");
  await camera.widenSideBar(460);
  // The panel tall enough for `ger push` to draw its preview (it says "Window too small" below ~15 rows).
  await vscode.commands.executeCommand("workbench.action.terminal.focus");
  await wait(1000);
  const sash = await window.boxOf(`const p = document.querySelector(".part.panel"); return p ? { getBoundingClientRect: () => { const r = p.getBoundingClientRect(); return { left: r.left + r.width / 2, top: r.top, width: 1, height: 1 }; } } : null;`);
  if (sash) {
    await window.drag({ x: sash.x, y: sash.y - 1 }, { x: sash.x, y: Math.max(200, sash.y - 320) });
  }
  await vscode.commands.executeCommand("workbench.action.closePanel");
  await vscode.commands.executeCommand("workbench.view.explorer");
  await wait(1500);
  await tracer.idle();

  let n = 0;
  let place = "explorer";
  /** What the Change view's Next button names now ("" for none). */
  const nextNamed = () => api.changeView.next()?.label ?? "";
  const step = async (spec: StepSpec, perform: (input: Input) => Promise<void>): Promise<void> => {
    n += 1;
    const input = new Input(window);
    const namedBefore = nextNamed();
    const t0 = now();
    input.last = t0;
    tracer.write({ kind: "step-start", loop: spec.loop, n, title: spec.title });
    let error: string | undefined;
    const phase = (name: string) => tracer.write({ kind: "debug", n, phase: name });
    try {
      await Promise.race([perform(input), wait(45_000).then(() => Promise.reject(new Error("its input took over 45 s")))]);
    } catch (thrown) {
      error = thrown instanceof Error ? thrown.message : String(thrown);
    }
    phase("given");
    let reached: number | null = null;
    const deadline = Date.now() + (spec.timeoutMs ?? 20_000);
    while (!error && Date.now() < deadline) {
      if (!spec.until || (await spec.until())) {
        reached = now() - t0;
        break;
      }
      await wait(20);
    }
    phase("outcome");
    await Promise.race([tracer.idle(), wait(15_000)]);
    await Promise.race([api.changeView.idle(), wait(10_000)]);
    const idle = now() - t0;
    phase("idle");
    const switches = [place, ...spec.where].reduce((count, where, index, all) => count + (index > 0 && where !== all[index - 1] ? 1 : 0), 0);
    place = spec.where.at(-1) ?? place;
    const record: StepRecord = {
      kind: "step",
      loop: spec.loop,
      n,
      title: spec.title,
      budget: spec.budget,
      t: t0,
      reached_ms: reached === null ? null : Math.round(reached),
      idle_ms: Math.round(idle),
      lead_ms: Math.round((input.first ?? t0) - t0),
      input_ms: Math.round(input.last - t0),
      clicks: input.clicks,
      keys: input.keys,
      typed: input.typed,
      switches,
      where: spec.where,
      input: input.real ? "real" : input.given.length === input.fallbacks.length ? "command" : "mixed",
      given: input.given,
      ...(input.fallbacks.length ? { fallbacks: input.fallbacks } : {}),
      ...(error ? { error } : {}),
      ...(reached === null && !error ? { error: "its outcome did not show in time" } : {}),
      picture: pictureName(n, `${spec.loop}-${spec.title}`),
      next: [namedBefore, nextNamed()],
    };
    tracer.write({ ...record });
    await wait(300);
    await camera.take(path.join(shots, record.picture)).catch((thrown: unknown) => note(`picture ${n}: ${String(thrown)}`));
  };

  // --- Where things are -------------------------------------------------------------------
  const pane = (title: string) =>
    `[...document.querySelectorAll(".pane")].find((p) => (p.querySelector(".pane-header")?.textContent || "").toLowerCase().includes(${JSON.stringify(title.toLowerCase())}))`;
  /** A tree row by the words it shows (its `aria-label` is its tooltip): all of `words`, in its label and description. */
  const findRow = (paneTitle: string, words: string[]) =>
    `const p = ${pane(paneTitle)}; return p && [...p.querySelectorAll(".monaco-list-row")].find((r) => ${JSON.stringify(words)}.every((w) => (r.textContent || "").includes(w)));`;
  const treeRow = (paneTitle: string, ...words: string[]) => window.boxOf(findRow(paneTitle, words));
  const titleAction = async (paneTitle: string, label: string) => {
    const header = await window.boxOf(`const p = ${pane(paneTitle)}; return p && p.querySelector(".pane-header");`);
    if (header) {
      await window.hover(middle(header));
      await wait(150);
    }
    return window.boxOf(`const p = ${pane(paneTitle)}; return p && [...p.querySelectorAll(".pane-header .action-label")].find((a) => (a.getAttribute("aria-label") || a.title || "").startsWith(${JSON.stringify(label)}));`);
  };
  const rowAction = async (paneTitle: string, words: string[], action: string) => {
    const row = await treeRow(paneTitle, ...words);
    if (!row) {
      return null;
    }
    await window.hover(middle(row));
    await wait(150);
    return window.boxOf(
      `const r = (() => { ${findRow(paneTitle, words)} })(); return r && [...r.querySelectorAll(".action-label")].find((a) => (a.getAttribute("aria-label") || a.title || "").startsWith(${JSON.stringify(action)}));`,
    );
  };
  const inChange = (find: string) => window.boxInView("change", find);
  const threadRow = (text: string) => `return [...document.querySelectorAll(".item.thread")].find((r) => r.textContent.includes(${JSON.stringify(text)}));`;
  const threadButton = async (text: string, title: string) => {
    const row = await inChange(threadRow(text));
    if (row) {
      await window.hover(middle(row));
      await wait(120);
    }
    return inChange(`const r = [...document.querySelectorAll(".item.thread")].find((r) => r.textContent.includes(${JSON.stringify(text)})); return r && [...r.querySelectorAll("button")].find((b) => (b.title || "").startsWith(${JSON.stringify(title)}));`);
  };
  const dialogButton = (label: string) =>
    window.boxOf(`return [...document.querySelectorAll(".monaco-dialog-box .monaco-button, .notification-toast .monaco-button, .notifications-toasts .monaco-button")].find((b) => b.textContent.trim() === ${JSON.stringify(label)});`);
  /**
   * Click `label` on a question (a notification's button) once it has come and stopped moving: a
   * toast slides in, and a click on where it was misses. Tried again while the button is still there.
   */
  const answer = async (input: Input, label: string, what: string, waitMs = 10_000): Promise<boolean> => {
    let box: Box | null = null;
    for (const until = Date.now() + waitMs; !box && Date.now() < until; ) {
      box = await dialogButton(label);
      if (!box) await wait(100);
    }
    for (let attempt = 0; box && attempt < 3; attempt++) {
      await wait(400);
      box = await dialogButton(label);
      if (!(await input.click(box, what))) {
        return attempt > 0;
      }
      // Gone once it took the click: look every 100 ms, not a fixed pause the step's time would count.
      for (let i = 0; i < 15 && box; i++) {
        await wait(100);
        box = await dialogButton(label);
      }
    }
    return Boolean(box) === false;
  };
  const quickInputOpen = () => window.evaluate<boolean>(`const w = document.querySelector(".quick-input-widget"); return !!w && getComputedStyle(w).display !== "none" && w.getBoundingClientRect().height > 0;`);
  const editorLine = (text: string) =>
    window.boxOf(`return [...document.querySelectorAll(".editor-instance .view-line, .monaco-editor .view-line")].find((l) => l.textContent.replace(/\\u00a0/g, " ").includes(${JSON.stringify(text)}) && l.getBoundingClientRect().width > 0);`);
  const changeShown = (number: number) => () => api.changeView.target?.number === number && api.changeView.heading !== undefined;
  const activeEditor = () => vscode.window.activeTextEditor?.document.uri.toString() ?? vscode.window.tabGroups.activeTabGroup.activeTab?.label ?? "";
  /**
   * Next, as a user is led: when the Change view's button names `what` (within `ms`: it follows the
   * change being loaded), Alt+Enter, wherever the keyboard is. False when it names something else.
   */
  const pressNext = async (input: Input, what: RegExp, ms = 3000): Promise<boolean> => {
    for (const until = Date.now() + ms; !what.test(nextNamed()) && Date.now() < until; ) await wait(50);
    const named = nextNamed();
    if (!what.test(named)) {
      note(`step ${n}: Next names "${named || "nothing"}", not ${what}`);
      return false;
    }
    await input.key("alt+enter");
    input.given[input.given.length - 1] += ` (Next: "${named}")`;
    return true;
  };
  /** A button of the task header, by its label: in the Change view or in Local stack, wherever it is drawn. */
  const headerButton = async (label: string) => {
    const find = `return [...document.querySelectorAll(".task .task-action button")].find((b) => b.textContent.trim() === ${JSON.stringify(label)} && !b.disabled);`;
    return (await window.boxInView("change", find)) ?? (await window.boxInView("stack", find));
  };

  // --- (a) The review loop ------------------------------------------------------------------
  const toReview = api.model.overview?.sections?.find((section) => section.name === "to-review");
  const wanted = Number(process.env.GER_WALK_REVIEW || 0);
  const chain = toReview?.chains.find((c) => (wanted ? c.members.some((m) => m.number === wanted) : c.members.length > 1)) ?? toReview?.chains[0];
  // Members are listed bottom first (the tree shows them top first): the review starts at the bottom.
  const bottom = chain?.members[0];
  note(`review: chain ${chain?.key ?? "none"} "${chain?.top.subject ?? ""}", bottom ${bottom?.number ?? "-"}`);
  const only = process.env.GER_WALK_ONLY;
  if (chain && bottom && only !== "rework") {
    await step({ loop: "review", title: "Open the review queue", budget: "walk", where: ["activity bar", "Review chains"], until: async () => Boolean(await treeRow("Review chains", "To review")) }, async (input) => {
      if (!(await input.click(await window.boxOf(`return [...document.querySelectorAll(".activitybar .action-item .action-label")].find((a) => (a.getAttribute("aria-label") || "").startsWith("Gerrit"));`), "the Gerrit icon in the activity bar"))) {
        await input.command("workbench.view.extension.ger", "no Gerrit icon found", "click");
      }
    });
    await step({ loop: "review", title: "Open a chain", budget: "walk", where: ["Review chains"], until: async () => Boolean(await treeRow("Review chains", bottom.subject, `${bottom.number} ·`)) }, async (input) => {
      if (!(await input.click(await treeRow("Review chains", chain.top.subject), `the chain row "${chain.top.subject}"`))) {
        throw new Error("no chain row");
      }
    });
    await step({ loop: "review", title: "Read the commit message", budget: "change", where: ["Review chains", "Change view"], until: changeShown(bottom.number) }, async (input) => {
      if (!(await input.click(await treeRow("Review chains", bottom.subject, `${bottom.number} ·`), `the change row ${bottom.number}`))) {
        await input.command("ger.openChange", "no change row found", "click", { rev: `change:${bottom.number}`, title: bottom.subject, number: bottom.number, local: false });
      }
    });
    await step({ loop: "review", title: "Start the review (Review Change)", budget: "slow", where: ["Review chains", "Change view", "editor"], until: () => Boolean(api.model.stack?.staged_review), timeoutMs: 60_000 }, async (input) => {
      if (!(await input.click(await rowAction("Review chains", [bottom.subject, `${bottom.number} ·`], "Review Change"), "Review Change on the change row"))) {
        if (!(await input.click(await titleAction("Change", "Review Change"), "Review Change in the Change view"))) {
          await input.command("ger.review", "no Review Change button found", "click");
        }
      }
    });
    const reviewed = () => api.model.stack?.staged_review?.number ?? bottom.number;
    // Review Change opened all its files (the combined diff): what is left of the files is to see
    // them, as Next leads (Next file per file, or Mark N files seen once they were read there).
    const fileStep = /^(Next file|Mark (all|\d+) files? seen)$/;
    const filesLeft = () => fileStep.test(nextNamed());
    await step({ loop: "review", title: "See every file (Next)", budget: "change", where: ["Change view", "editor"], until: () => !filesLeft() }, async (input) => {
      let pressed = 0;
      let stuck = 0;
      const why = () => `${nextNamed()} ${api.changeView.next()?.title ?? ""}`;
      for (let i = 0; i < 12 && stuck < 2; i++) {
        const before = why();
        if (!(await pressNext(input, fileStep, i === 0 ? 3000 : 500))) {
          break;
        }
        pressed += 1;
        // Ticked once the file was shown to its end (a moment after it is laid out), or by Mark seen's one request.
        for (let j = 0; j < 60 && why() === before; j++) await wait(50);
        stuck = why() === before ? stuck + 1 : 0;
      }
      if (stuck >= 2) {
        // Pressed twice and nothing moved: a user ticks the files by hand, in the Change view's Files.
        note(`step ${n}: Next "${nextNamed()}" changed nothing twice: the files ticked by hand`);
        const unseen = async () => (await window.inView<number>("change", `return document.querySelectorAll(".item.file:not(.seen)").length;`)) ?? 0;
        for (let i = 0; i < 12 && filesLeft(); i++) {
          const left = await unseen();
          if (!(await input.click(await inChange(`return document.querySelector(".item.file:not(.seen) button.seen");`), "a file's tick in the Change view"))) {
            break;
          }
          for (let j = 0; j < 40 && (await unseen()) === left; j++) await wait(50);
        }
      }
      if (!pressed) {
        // Next leads elsewhere: the old way, all the files in one editor.
        if (!(await input.click(await titleAction("Change", "Open All Changes"), "Open All Changes in the Change view"))) {
          await input.command("ger.openAllChanges", "no Open All Changes button found", "click");
        }
      }
    });
    note(`review: reviewing ${reviewed()}; tab "${vscode.window.tabGroups.activeTabGroup.activeTab?.label ?? ""}"`);
    const firstThread = api.changeView.chains().find((c) => c.path && c.path !== "/PATCHSET_LEVEL" && c.line);
    if (firstThread) {
      let before = activeEditor();
      await step({ loop: "review", title: "Go to a comment", budget: "walk", where: ["Change view", "editor"], until: () => activeEditor() !== before || (vscode.window.activeTextEditor?.selection.active.line ?? -1) + 1 === firstThread.line }, async (input) => {
        before = activeEditor();
        if (await pressNext(input, /^Next comment$/, 500)) {
          return;
        }
        if (!(await input.click(await inChange(threadRow(firstThread.comments[0]?.message.split("\n")[0].slice(0, 40) ?? "")), "the comment in the Change view"))) {
          await input.command("ger.nextComment", "no comment row found", "key");
        }
      });
    } else {
      note("review: the reviewed change has no comment on a line: Go to a comment uses Next Unresolved Comment");
      await step({ loop: "review", title: "Go to a comment", budget: "walk", where: ["editor"] }, async (input) => input.key("alt+f9"));
    }
    const drafts = () => api.changeView.chains().filter((c) => c.comments.some((m) => m.state === "draft" && m.message.includes("walk:"))).length;
    const draftsBefore = drafts();
    await step({ loop: "review", title: "Write a draft comment", budget: "change", where: ["editor", "comment box", "editor"], until: () => drafts() > draftsBefore }, async (input) => {
      const editor = vscode.window.activeTextEditor;
      const line = editor ? editor.document.lineAt(Math.min(editor.document.lineCount - 1, Math.max(0, editor.selection.active.line))).text.trim().slice(0, 20) : "";
      const at = line ? await editorLine(line) : null;
      if (at) {
        await input.click({ ...at, width: Math.min(at.width, 200) }, "the line in the editor");
      }
      await input.key("ctrl+alt+c");
      // Type only once the comment box has the keyboard: else the words land in the file.
      const inBox = () => window.evaluate<boolean>(`return !!document.activeElement?.closest(".review-widget, .comment-form, .comment-thread-widget");`);
      for (let i = 0; i < 60 && !(await inBox()); i++) await wait(50);
      if (!(await inBox())) {
        // The new thread's box: the one with Save Draft, not the reply box of a thread already there.
        await input.click(
          await window.boxOf(`const w = [...document.querySelectorAll(".review-widget")].reverse().find((w) => w.textContent.includes("Save Draft")); return w && w.querySelector(".comment-form .monaco-editor, textarea");`),
          "into the comment box",
        );
        await wait(200);
      }
      if (!(await inBox())) {
        throw new Error("the comment box did not take the keyboard");
      }
      await input.type("walk: is this the intended behaviour?");
      await input.key("ctrl+enter");
    });
    let voted = false;
    await step({ loop: "review", title: "Vote and Publish", budget: "slow", where: ["Change view", "quick input", "notification"], until: async () => voted && Boolean(await dialogButton("Next Change")), timeoutMs: 45_000 }, async (input) => {
      if (!(await pressNext(input, /^Vote and publish$/)) && !(await input.click(await titleAction("Change", "Vote and Publish"), "Vote and Publish… in the Change view"))) {
        void vscode.commands.executeCommand("ger.vote");
        input.real = false;
        input.fallbacks.push("ger.vote (no Vote button found)");
        input.clicks += 1;
      }
      for (let i = 0; i < 50 && !(await quickInputOpen()); i++) await wait(50);
      await input.type("+1");
      await wait(200);
      await input.key("enter");
      // The message (left empty), then Publish: each prompt once the one before it has closed.
      const closed = async () => {
        for (let i = 0; i < 40 && (await quickInputOpen()); i++) await wait(50);
      };
      await closed();
      for (let i = 0; i < 40 && !(await quickInputOpen()); i++) await wait(50);
      await input.key("enter");
      await closed();
      voted = await answer(input, "Publish", "Publish in the question");
    });
    await step({ loop: "review", title: "Next change", budget: "change", where: ["notification", "Change view", "editor"], until: () => (api.model.stack?.staged_review?.number ?? 0) !== bottom.number, timeoutMs: 60_000 }, async (input) => {
      // Next names it once the vote is in; the notification's Next Change is the same, where the eyes already are.
      if (!(await pressNext(input, /^Next change$/)) && !(await answer(input, "Next Change", "Next Change in the notification", 3000))) {
        await input.key("ctrl+alt+pagedown");
      }
    });
    note(`review: now reviewing ${api.model.stack?.staged_review?.number ?? "nothing"}`);
    // Not a step: leave the review checkout, so the rework loop finds its branch.
    await vscode.commands.executeCommand("ger.reviewDone");
    for (let i = 0; i < 300 && api.model.stack?.staged_review; i++) await wait(100);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    await wait(1500);
    await tracer.idle();
  }

  // --- (b) The rework loop ------------------------------------------------------------------
  if (only !== "review") {
    // Set the scene, not measured: the Gerrit side bar in front (after the review loop it is).
    await vscode.commands.executeCommand("workbench.view.extension.ger");
    await wait(500);
    const stopped = () => api.model.stack?.rebase?.current ?? null;
    const stoppedNumber = () => api.model.stack?.commits.find((c) => c.sha === stopped())?.number ?? null;
    note(`rework: stack ${api.model.stack?.commits.map((c) => c.number).join(" ")}, needs edit ${api.model.stack?.summary.needs_edit}`);
    await step(
      { loop: "rework", title: "Work on Next", budget: "slow", where: ["Local stack", "terminal", "Change view", "editor"], until: () => stopped() !== null && api.changeView.target?.number === stoppedNumber(), timeoutMs: 90_000 },
      async (input) => {
        if (!(await input.click(await titleAction("Local stack", "Work on Next"), "Work on Next in Local stack"))) {
          await input.command("ger.workOnNext", "no Work on Next button found", "click");
        }
      },
    );
    const first = stoppedNumber();
    note(`rework: stopped at ${first}; editor ${activeEditor()}`);
    const position = () => `${activeEditor()}:${vscode.window.activeTextEditor?.selection.active.line ?? -1}`;
    for (const which of ["second", "third"]) {
      const at = position();
      const focus = await window.evaluate<string>(`const a = document.activeElement; return a ? (a.tagName + "." + a.className).slice(0, 80) + (a.closest(".pane") ? " in " + (a.closest(".pane").querySelector(".pane-header")?.textContent || "").slice(0, 30) : "") : "none";`);
    const inPage = await window.inView<string>("change", `window.__walkKeys = []; document.addEventListener("keydown", (e) => window.__walkKeys.push((e.altKey ? "alt+" : "") + e.key), true); const a = document.activeElement; return document.hasFocus() + " " + (a ? a.tagName + "." + a.className : "none");`);
    note(`rework: before the ${which} Alt+F9 the keyboard is at ${focus} (Change view page: focus ${inPage}); editor ${position()}`);
    const pressed = now();
    await step({ loop: "rework", title: `Next comment (${which})`, budget: "walk", where: ["editor"], until: () => position() !== at || said.at > pressed, timeoutMs: 5000 }, async (input) => {
      if (!(await pressNext(input, /^Next comment$/, 500))) {
        await input.key("alt+f9");
      }
    });
    note(`rework: after the key the editor is at ${position()}${said.at > pressed ? `; the status bar says "${said.text}"` : ""}`);
    note(`rework: the Change view page saw the keys ${await window.inView<string>("change", `return JSON.stringify(window.__walkKeys || null);`)}`);
    }
    const suggestion = api.changeView.chains().find((c) => /```suggestion/.test(c.comments.map((m) => m.message).join("\n")) && !c.resolved);
    if (suggestion) {
      const text = suggestion.comments[0].message.split("\n")[0].slice(0, 30);
      const dirty = () => vscode.workspace.textDocuments.some((d) => d.isDirty && d.uri.path.endsWith(suggestion.path));
      await step({ loop: "rework", title: "Apply the suggested edit", budget: "walk", where: ["Change view", "editor"], until: dirty }, async (input) => {
        if (!(await input.click(await threadButton(text, "Apply Suggested Edit"), "Apply Suggested Edit on the comment row"))) {
          await input.command("ger.applySuggestion", "no Apply button found", "click", { kind: "thread", chain: suggestion });
        }
      });
      // Apply may ask whether to answer the comment too: that answer is the Reply Done.
      const asked = await dialogButton("Reply Done");
      const done = () => api.changeView.chains().some((c) => c.root_id === suggestion.root_id && c.comments.some((m) => m.state === "draft"));
      await step({ loop: "rework", title: "Reply Done", budget: "change", where: asked ? ["notification"] : ["Change view"], until: done }, async (input) => {
        if (!asked || !(await answer(input, "Reply Done", "Reply Done in the notification", 1000))) {
          if (!(await input.click(await threadButton(text, "Reply Done"), "Reply Done on the comment row"))) {
            await input.command("ger.replyDone", "no Reply Done button found", "click", { kind: "thread", chain: suggestion });
          }
        }
      });
      await step({ loop: "rework", title: "Save the file", budget: "walk", where: ["editor"], until: () => !dirty() }, async (input) => {
        const name = suggestion.path.split("/").at(-1) ?? "";
        if (!(await input.click(await window.boxOf(`return [...document.querySelectorAll(".tabs-container .tab")].find((t) => (t.getAttribute("aria-label") || t.textContent || "").includes(${JSON.stringify(name)}));`), `the ${name} tab`))) {
          await vscode.window.showTextDocument(vscode.workspace.textDocuments.find((d) => d.uri.path.endsWith(suggestion.path))!);
          input.real = false;
          input.fallbacks.push(`showTextDocument (no ${name} tab found)`);
        }
        await input.key("ctrl+s");
      });
    } else {
      note("rework: no unresolved suggested edit at this stop");
    }
    await step(
      { loop: "rework", title: "Next stop (Work on Next)", budget: "slow", where: ["Change view", "notification", "terminal", "Change view", "terminal", "Change view", "editor"], until: () => stoppedNumber() !== null && stoppedNumber() !== first && api.changeView.target?.number === stoppedNumber(), timeoutMs: 90_000 },
      async (input) => {
        const workOnNext = async (what: string) => {
          if (!(await pressNext(input, /^(Work on next|Finish the rebase)$/)) && !(await input.click(await titleAction("Local stack", "Work on Next"), what))) {
            await input.command("ger.workOnNext", "no Work on Next button found", "click");
          }
        };
        await workOnNext("Work on Next in Local stack");
        // Refused while the fix is saved and not amended (a warning, with no way on): amend it in the terminal, as a user must, and press again.
        for (let i = 0; i < 40 && stoppedNumber() === first; i++) await wait(100);
        if (stoppedNumber() === first) {
          note("rework: Work on Next refused (the fix is not amended): amending in the terminal");
          await input.key("ctrl+`");
          await wait(800);
          await input.type("git commit -q -a --amend --no-edit");
          await input.key("enter");
          await wait(2500);
          await workOnNext("Work on Next in Local stack, again");
        }
      },
    );
    note(`rework: stopped at ${stoppedNumber()} (rebase ${stopped() ? "underway" : "finished"})`);
    const terminalText = () => window.evaluate<string>(`return [...document.querySelectorAll(".xterm-rows")].map((r) => r.textContent).join("\\n");`);
    let shownBefore = "";
    let pushed = false;
    let continuing: number | false = false;
    await step(
      { loop: "rework", title: "Push preview", budget: "slow", where: ["task header", "terminal", "Local stack", "terminal"], until: async () => pushed && (await terminalText()) !== shownBefore && /push/i.test(await terminalText()), timeoutMs: 60_000 },
      async (input) => {
        const push = async (what: string) => {
          if (!(await input.click(await titleAction("Local stack", "Push to Gerrit"), what))) {
            await input.command("ger.push", "no Push button found", "click");
          }
        };
        // The header's Continue says what it does (replays the commits above): the way out of the rework, then Push.
        if (await input.click(await headerButton("Continue"), "Continue in the task header")) {
          const clicked = now();
          for (let i = 0; i < 600 && api.model.stack?.rebase_in_progress; i++) {
            continuing ||= Boolean(api.task.vm()?.busy) && now() - clicked;
            await wait(100);
          }
          note(`push: the rebase ${api.model.stack?.rebase_in_progress ? "is still underway" : "is finished"}${continuing ? `; the header said so after ${Math.round(continuing)} ms` : "; the header said nothing while it ran"}`);
          await wait(500);
          shownBefore = await terminalText();
          await push("Push to Gerrit… in Local stack");
          pushed = true;
          return;
        }
        await push("Push to Gerrit… in Local stack");
        // During the rebase Push says to finish it first, and offers Continue: take it, then push.
        if (await answer(input, "Continue", "Continue in the question", 2000)) {
          for (let i = 0; i < 600 && api.model.stack?.rebase_in_progress; i++) await wait(100);
          note(`push: the rebase ${api.model.stack?.rebase_in_progress ? "is still underway" : "is finished"}`);
          await wait(500);
          shownBefore = await terminalText();
          await push("Push to Gerrit… in Local stack, again");
        } else {
          shownBefore = await terminalText();
        }
        pushed = true;
      },
    );
    note(`rework: terminals ${vscode.window.terminals.map((t) => `${t.name} env ${JSON.stringify((t.creationOptions as vscode.TerminalOptions).env ?? null)}`).join("; ")}`);
    await wait(3000); // the preview, as ger push prints it, for the picture
    n += 1;
    await camera.take(path.join(shots, pictureName(n, "rework-push-preview-in-the-terminal"))).catch(() => undefined);
    // Not a step: answer the push's question with no, and leave the rebase as it was found.
    vscode.window.activeTerminal?.sendText("\u0003", false);
    await wait(1000);
    if (stopped()) {
      await vscode.commands.executeCommand("ger.rebase.abort").then(undefined, () => undefined);
    }
  }
  // The terminals' text, for run.ts to look for a traceback from ger in.
  fs.writeFileSync(path.join(shots, "terminal.txt"), `${await window.evaluate<string>(`return [...document.querySelectorAll(".xterm-rows")].map((r) => [...r.children].map((l) => l.textContent).join("\\n")).join("\\n----\\n");`).catch(() => "")}\n`);
  // What the run reset undoes on Gerrit (run.ts): the changes it voted on and saved drafts on.
  tracer.write({ kind: "touched", changes: [...(chain?.members.map((m) => m.number) ?? []), ...(api.model.stack?.commits.map((c) => c.number ?? 0).filter(Boolean) ?? [])] });
  window.close();
  camera.close();
  fs.writeFileSync(path.join(shots, "notes.txt"), `${notes.join("\n")}\n`);
  await tracer.idle();
  await wait(500);
}
