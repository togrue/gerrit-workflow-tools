// package.json (menus, keybindings, welcome texts) and the code that feeds it are coupled only by
// strings: `viewItem =~ /regex/` against the contextValue a row gets, `ger.*` context keys against
// `setContext`, command ids against what is registered. Nothing else notices when one side moves.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { allRowContexts } from "../src/rows";

const root = path.join(__dirname, "..", "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const source = (file: string) => fs.readFileSync(path.join(root, "src", file), "utf8");
const allSource = fs
  .readdirSync(path.join(root, "src"))
  .filter((file) => file.endsWith(".ts"))
  .map(source)
  .join("\n");

interface Entry {
  command?: string;
  when?: string;
  view?: string;
  contents?: string;
  key?: string;
}
const menus: Record<string, Entry[]> = manifest.contributes.menus;
const itemMenu = menus["view/item/context"];
const REGEX = /viewItem\s*=~\s*\/((?:\\.|[^/\\])+)\//g;

/** The `viewItem` regexes of one alternative of a `when` clause. */
function viewItemPatterns(clause: string): RegExp[] {
  return [...clause.matchAll(REGEX)].map((m) => new RegExp(m[1]));
}

/** The row contexts a menu entry can show on, looking at its `viewItem` tests only (each `||` alternative separately). */
function matchedBy(when: string): Set<string> {
  const matched = new Set<string>();
  for (const alternative of when.split("||")) {
    const patterns = viewItemPatterns(alternative);
    if (!patterns.length) {
      continue;
    }
    for (const value of allRowContexts()) {
      if (patterns.every((pattern) => pattern.test(value))) {
        matched.add(value);
      }
    }
  }
  return matched;
}

test("every viewItem pattern in the menus matches a row the views can produce", () => {
  const dead: string[] = [];
  for (const entry of itemMenu) {
    for (const alternative of (entry.when ?? "").split("||")) {
      const patterns = viewItemPatterns(alternative);
      if (patterns.length && !allRowContexts().some((value) => patterns.every((p) => p.test(value)))) {
        dead.push(`${entry.command}: ${alternative.trim()}`);
      }
    }
  }
  assert.deepEqual(dead, [], "these menu entries can never show: a contextValue changed, or the regex is wrong");
});

test("each row action is offered on exactly the rows it is meant for", () => {
  const is = (value: string, kind: string) => new RegExp(`^ger\\.(${kind})`).test(value);
  const local = (value: string) => /\.local\b/.test(value);
  const expected: Record<string, (value: string) => boolean> = {
    "ger.review": (v) => is(v, "change|chain") && !local(v),
    "ger.checkout": (v) => is(v, "change|chain") && !local(v),
    "ger.stack.edit": (v) => (is(v, "change|chain") && local(v)) || is(v, "commit"),
    "ger.openInGerrit": (v) => is(v, "change|chain|commit") && /\.url\b/.test(v),
    "ger.stack.moveUp": (v) => is(v, "commit") && /\.up\b/.test(v),
    "ger.stack.moveUpBlocked": (v) => is(v, "commit") && /\.upx\b/.test(v),
    "ger.stack.moveDown": (v) => is(v, "commit") && /\.down\b/.test(v),
    "ger.stack.moveDownBlocked": (v) => is(v, "commit") && /\.downx\b/.test(v),
    "ger.stack.squash": (v) => is(v, "commit") && /\.downx?\b/.test(v),
    "ger.stack.squashTakeMessage": (v) => is(v, "commit") && /\.downx?\b/.test(v),
    "ger.stack.drop": (v) => is(v, "commit"),
    "ger.agentBrief": (v) => is(v, "change|commit"),
    "ger.reply": (v) => is(v, "thread"),
    "ger.replyDone": (v) => is(v, "thread.open"),
    "ger.hold": (v) => is(v, "thread.open.free"),
    "ger.release": (v) => is(v, "thread.open.held"),
  };
  const offered = new Map<string, Set<string>>();
  for (const entry of itemMenu) {
    const rows = offered.get(entry.command ?? "") ?? new Set<string>();
    matchedBy(entry.when ?? "").forEach((value) => rows.add(value));
    offered.set(entry.command ?? "", rows);
  }
  assert.deepEqual([...offered.keys()].filter((command) => !(command in expected)), [], "a row action without an expectation here");
  for (const [command, wanted] of Object.entries(expected)) {
    const shouldHave = allRowContexts().filter(wanted).sort();
    assert.deepEqual([...(offered.get(command) ?? [])].sort(), shouldHave, command);
  }
});

test("every row has some action, except your own notes", () => {
  const matched = new Set<string>();
  for (const entry of itemMenu) {
    matchedBy(entry.when ?? "").forEach((value) => matched.add(value));
  }
  assert.deepEqual(
    allRowContexts().filter((value) => !matched.has(value)),
    ["ger.mine"],
    "a row nothing can be done with: its contextValue is not what the menus expect",
  );
});

test("every command in a menu or keybinding is declared, and every declared command is registered", () => {
  const declared = new Set<string>(manifest.contributes.commands.map((c: Entry) => c.command));
  const used = [...Object.values(menus).flat(), ...manifest.contributes.keybindings].map((entry: Entry) => entry.command);
  assert.deepEqual(
    used.filter((command) => command && !declared.has(command)),
    [],
    "used in a menu or keybinding, not declared",
  );
  // The table in extension.ts (or, with another command table, anywhere in src): `"ger.x": …` keys.
  const registered = new Set([...allSource.matchAll(/^\s*"(ger\.[\w.]+)":/gm)].map((m) => m[1]));
  assert.deepEqual([...declared].filter((command) => !registered.has(command)), [], "declared, not registered");
  assert.deepEqual([...registered].filter((command) => !declared.has(command)), [], "registered, not declared");
  // Every declared command has a palette decision: shown (when it can work) or hidden (when: false).
  const palette = new Set(menus.commandPalette.map((entry) => entry.command));
  const undecided = [...declared].filter((command) => !palette.has(command) && !/^(ger\.(refresh|showLog))$/.test(command));
  assert.deepEqual(undecided, [], "no commandPalette entry: it would be offered when it cannot work");
});

test("every ger.* context key a when clause reads is set by the code", () => {
  const clauses: string[] = [
    ...Object.values(menus).flat().map((entry) => entry.when ?? ""),
    ...manifest.contributes.viewsWelcome.map((entry: Entry & { when?: string }) => entry.when ?? ""),
    ...manifest.contributes.keybindings.map((entry: Entry) => entry.when ?? ""),
  ];
  const read = new Set<string>();
  for (const clause of clauses) {
    // Without regex literals and the right-hand side of comparisons (`view == ger.stack` names a view, not a key).
    const bare = clause.replace(/\/(?:\\.|[^/\\])+\//g, "").replace(/(==|!=)\s*[\w.\-]+/g, "$1");
    for (const [key] of bare.matchAll(/\bger\.\w+/g)) {
      read.add(key);
    }
  }
  const set = new Set([...allSource.matchAll(/"setContext",\s*"(ger\.\w+)"/g)].map((m) => m[1]));
  assert.deepEqual([...read].filter((key) => !set.has(key)).sort(), [], "read in package.json, never set: the menu or welcome text can never show");
  assert.deepEqual([...set].filter((key) => !read.has(key)).sort(), [], "set, never read in package.json");
});
