// The webview gallery (test/gallery): that it stands in for VS Code completely (every theme
// variable the pages use, the files the real page loads), that its built-in scenarios are snapshot
// files showing the states that matter for layout, and how it finds a browser for the pictures.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { readSnapshot } from "../src/snapshot";
import { SYNC_ICONS } from "../src/stackRows";
import { BROWSER_ENV, browserCandidates, findBrowser } from "./gallery/browser";
import { PAGE_FILES, frameHtml, frames, indexHtml, slugs } from "./gallery/build";
import { Scenario, scenarios } from "./gallery/scenarios";
import { themeCss, themeVariables, themedVariables } from "./gallery/theme";

const root = path.join(__dirname, "..", "..");
const read = (...file: string[]) => fs.readFileSync(path.join(root, ...file), "utf8");
const used = (text: string) => [...new Set(text.match(/--vscode-[A-Za-z0-9-]+/g) ?? [])].sort();

// --- Standing in for VS Code ----------------------------------------------------------

test("every --vscode- variable the pages use is one the gallery's themes know", () => {
  // The pages' own files; codicon.css is VS Code's and names none.
  const pages = fs.readdirSync(path.join(root, "media")).filter((file) => /\.(css|js)$/.test(file));
  const known = new Set(themedVariables());
  const unknown = pages.flatMap((file) =>
    used(read("media", file))
      .filter((name) => !known.has(name))
      .map((name) => `${file}: ${name}`),
  );
  assert.deepEqual(unknown, [], "add it to test/gallery/theme.ts: its Dark Modern and Light Modern values, or null where those themes leave it unset");
  assert.ok(used(read("media", "views.css")).length > 30, "the pages' stylesheet was read");
});

test("a theme defines what it and VS Code's default styles use, and both themes decide on the same variables", () => {
  for (const theme of ["dark", "light"] as const) {
    const css = themeCss(theme);
    const set = themeVariables(theme);
    const defaults = used(css.slice(css.indexOf("}")));
    assert.deepEqual(defaults.filter((name) => !(name in set)), [], `${theme}: the default styles use a variable the theme does not set`);
    assert.match(css, new RegExp(`color-scheme: ${theme};`));
    // The pages fall back where a theme has no colour (a selection's text, here): that is drawn as in VS Code, not papered over.
    assert.equal(set["--vscode-list-inactiveSelectionForeground"], undefined);
    assert.ok(set["--vscode-foreground"] && set["--vscode-font-family"] && set["--vscode-sideBar-background"]);
  }
  assert.notEqual(themeVariables("dark")["--vscode-foreground"], themeVariables("light")["--vscode-foreground"]);
});

test("a frame loads the files the real page loads, in its order, behind the theme and the stand-in for VS Code", () => {
  const [frame] = frames(scenarios());
  const html = frameHtml(frame);
  // What src/webview.ts puts into a page: uri("codicon.css"), …, uri(`${name}.js`).
  const real = [...read("src", "webview.ts").matchAll(/uri\((?:"([^"]+)"|`\$\{name\}(\.js)`)\)/g)].map((match) => match[1] ?? `${frame.view}${match[2]}`);
  assert.deepEqual(real, ["codicon.css", "views.css", "common.js", `${frame.view}.js`], "src/webview.ts loads something else now: the gallery's frame has to follow");
  const loaded = [...html.matchAll(/(?:href|src)="\.\.\/([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(loaded, [`theme-${frame.theme}.css`, "media/codicon.css", "media/views.css", "frame.js", "media/common.js", `media/${frame.view}.js`]);
  assert.match(html, new RegExp(`<body class="${frame.view} vscode-${frame.theme}"`));
  assert.match(html, /<div id="root"><\/div>/);
  for (const file of real) {
    assert.ok(PAGE_FILES.includes(file), `${file} is copied into the gallery`);
  }
});

test("a view model cannot end the frame's script early, whatever a subject says", () => {
  const nasty: Scenario = { format: 1, name: "a </title><script>", stack: null, change: { empty: "</script><script>alert(1)</script>" } };
  const html = frameHtml(frames([nasty])[0]);
  assert.equal(html.split("</script>").length - 1, 4, "the data, the stand-in and the page's two scripts");
  assert.doesNotMatch(html, /<title>[^<]*<\/title><script>/);
  const data = /<script type="application\/json" id="gallery">(.*)<\/script>/.exec(html)?.[1] ?? "";
  assert.deepEqual(JSON.parse(data), { vm: nasty.change, click: [] });
  assert.doesNotMatch(indexHtml([nasty]), /a <\/title>/);
});

// --- Scenarios ------------------------------------------------------------------------

test("every built-in scenario is a snapshot file, and each has its own name", () => {
  const all = scenarios();
  for (const scenario of all) {
    const { click: _click, ...snapshot } = scenario;
    assert.deepEqual(readSnapshot(JSON.stringify(scenario)), JSON.parse(JSON.stringify(snapshot)), scenario.name);
    assert.ok(scenario.stack || scenario.change, `${scenario.name} shows something`);
  }
  assert.equal(new Set(slugs(all)).size, all.length);
  assert.equal(new Set(all.map((scenario) => scenario.name)).size, all.length);
});

test("a scenario's views each get a frame per theme; two scenarios of one name get files of their own", () => {
  const one: Scenario = { format: 1, name: "From a report", stack: { info: [], rows: [], frozen: null, movable: 0 }, change: { empty: "Nothing" } };
  assert.deepEqual(
    frames([one, { ...one, stack: null }]).map((frame) => frame.file),
    [
      "frames/from-a-report.stack.dark.html",
      "frames/from-a-report.stack.light.html",
      "frames/from-a-report.change.dark.html",
      "frames/from-a-report.change.light.html",
      "frames/from-a-report-2.change.dark.html",
      "frames/from-a-report-2.change.light.html",
    ],
  );
  const shots = [...indexHtml([one]).matchAll(/data-shot="([^"]+)" width="(\d+)"/g)].map((match) => `${match[1]} ${match[2]}`);
  assert.deepEqual(shots.slice(0, 6), ["from-a-report.stack.dark.200 200", "from-a-report.stack.dark.300 300", "from-a-report.stack.dark.450 450", "from-a-report.stack.light.200 200", "from-a-report.stack.light.300 300", "from-a-report.stack.light.450 450"]);
  assert.equal(shots.length, 12);
});

test("the scenarios show the states of Local stack that matter for layout", () => {
  const stacks = scenarios().flatMap((scenario) => (scenario.stack ? [scenario.stack] : []));
  const rows = stacks.flatMap((vm) => vm.rows);
  const info = stacks.flatMap((vm) => vm.info);
  const badges = rows.flatMap((row) => row.badges.flatMap((badge) => (badge ? [badge] : [])));
  assert.deepEqual([...new Set(rows.map((row) => row.sync))].sort(), Object.keys(SYNC_ICONS).sort(), "every sync icon");
  assert.deepEqual([...new Set(badges.map((badge) => `${badge.icon} ${badge.tone}`))].sort(), ["comment info", "comment plain", "comment-unresolved warn", "error bad", "pass good", "person-add warn", "thumbsdown bad", "thumbsup good"]);
  assert.deepEqual([...new Set(badges.flatMap((badge) => (badge.ring ? [badge.ring.shape] : [])))].sort(), ["dashed", "solid", "thin", "tick"], "every comment state's ring");
  assert.ok(badges.some((badge) => badge.stale), "votes of the last pushed patchset");
  assert.ok(rows.some((row) => row.badges.every(Boolean)), "a row with all three badges");
  assert.deepEqual([...new Set(rows.map((row) => String(row.marker)))].sort(), ["conflict", "null", "rebase"], "a rebase stopped with the commit applied, and on a conflict");
  for (const note of ["HEAD", "held back", "merge", "abandoned"]) {
    assert.ok(rows.some((row) => row.notes.includes(note)), note);
  }
  assert.ok(rows.some((row) => row.dim) && rows.some((row) => row.merge) && rows.some((row) => row.pinned));
  assert.ok(rows.some((row) => /cannot change places with/.test(row.tooltip)), "the hint on neighbours that cannot swap");
  assert.ok(rows.some((row) => row.summary.length > 100), "a long subject");
  assert.ok(info.some((line) => line.command?.id === "ger.setUpstream" && line.command.args.length), "no upstream, one to track");
  assert.ok(info.some((line) => line.text === "No local commits"), "an empty stack");
  assert.ok(stacks.some((vm) => vm.waiting?.link) && stacks.some((vm) => vm.waiting && !vm.waiting.link));
});

test("the scenarios show the states of Change that matter for layout", () => {
  const changes = scenarios().flatMap((scenario) => (scenario.change ? [scenario.change] : []));
  const threads = changes.flatMap((vm) => vm.comments?.threads ?? []);
  const files = changes.flatMap((vm) => vm.files?.rows ?? []);
  const links = changes.flatMap((vm) => vm.ci?.links ?? []);
  const findings = links.flatMap((link) => [...link.findings, ...link.warnings]);
  assert.ok(changes.some((vm) => vm.message && !vm.message.readOnly), "a message that can be edited");
  assert.ok(changes.some((vm) => vm.message?.readOnly && vm.hint?.show), "someone's change, with what is new since your review");
  assert.ok(changes.some((vm) => vm.loading?.message && vm.head?.icon === "loading" && vm.comments?.summary === "loading…"), "partly arrived");
  assert.ok(changes.some((vm) => vm.waiting) && changes.some((vm) => vm.empty) && changes.some((vm) => vm.problem));
  assert.ok(changes.some((vm) => (vm.notes ?? []).length) && changes.some((vm) => vm.head?.badges.some((badge) => badge.stale)));
  assert.ok(changes.some((vm) => vm.compare?.to.options.length === 1) && changes.some((vm) => (vm.compare?.to.options.length ?? 0) > 4), "one version to show, and many");
  assert.ok(files.some((row) => row.path.length > 80) && files.some((row) => (row.added ?? 0) > 999) && files.some((row) => (row.deleted ?? 0) > 999));
  assert.ok(files.some((row) => row.added === null) && files.some((row) => row.tooltip.includes(" → ")), "a binary file and a renamed one");
  assert.deepEqual([...new Set(threads.map((row) => row.icon))].sort(), ["comment-unresolved", "edit", "note", "pass", "person"], "open, answered or yours, a private note, resolved, kept for you");
  assert.ok(threads.some((row) => !row.file && row.reply && row.body.length > 300), "a long comment on the whole change, with its reply box");
  assert.ok(threads.some((row) => row.body.includes("suggested fix:")) && threads.some((row) => row.edit && row.discard) && threads.some((row) => row.draft));
  assert.deepEqual([...new Set(changes.flatMap((vm) => (vm.ci ? [vm.ci.state] : [])))].sort(), ["closed", "error", "loaded", "loading"]);
  assert.ok(changes.some((vm) => vm.ci?.state === "loaded" && !vm.ci.links.length), "a failed build without a link");
  assert.ok(changes.some((vm) => (vm.conflicts?.rows.length ?? 0) > 1 && vm.head?.actions?.length), "a rebase stopped on a conflict in several files, with its buttons");
  assert.ok(changes.some((vm) => !vm.conflicts && vm.head?.actions?.some((action) => action.primary)), "its conflicts resolved, Continue the one to press");
  assert.deepEqual([...new Set(findings.map((row) => row.icon))].sort(), ["circle-outline", "flame", "question"], "inherited, introduced, origin unknown");
  assert.ok(links.some((link) => link.warnings.length) && links.some((link) => link.detail.startsWith("could not be read")));
  // What a page only shows once opened is pressed open, so a picture has it.
  const opened = scenarios().flatMap((scenario) => scenario.click ?? []);
  assert.deepEqual(opened.sort(), [".finding[aria-expanded]", ".fold-after", ".thread .twist"]);
});

// --- Pictures -------------------------------------------------------------------------

test("the browser for pictures is the one the environment names, else Edge or Chrome where they are installed", () => {
  const windows = browserCandidates("win32", { "ProgramFiles(x86)": "C:\\Program Files (x86)", ProgramFiles: "C:\\Program Files", LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local" });
  assert.equal(windows[0], "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe");
  assert.ok(windows.includes("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe") && windows.includes("C:\\Users\\me\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"));
  assert.ok(browserCandidates("linux", { PATH: "/usr/local/bin:/usr/bin" }).includes("/usr/bin/chromium"));
  assert.equal(browserCandidates("darwin", {})[0], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  const installed = (file: string) => file === "/usr/bin/chromium" || file === "/opt/brave";
  assert.equal(findBrowser("linux", { PATH: "/usr/local/bin:/usr/bin" }, installed), "/usr/bin/chromium");
  assert.equal(findBrowser("linux", { PATH: "/usr/bin", [BROWSER_ENV]: "/opt/brave" }, installed), "/opt/brave");
  assert.equal(findBrowser("linux", { PATH: "/usr/bin", [BROWSER_ENV]: "/opt/gone" }, installed), undefined, "a named browser that is not there is not replaced by another");
  assert.equal(findBrowser("linux", { PATH: "/bin" }, installed), undefined);
});

test("the gallery does not ship in the .vsix", () => {
  const ignored = read(".vscodeignore").split(/\r?\n/);
  for (const pattern of ["out/gallery/**", "out/test/**", "test/**"]) {
    assert.ok(ignored.includes(pattern), `${pattern} is in .vscodeignore`);
  }
});
