// The state colours of the two webview views, measured (WCAG 2: 4.5:1 for words, 3:1 for an icon
// that carries a state, and for the focus ring) in Dark Modern, Light Modern and both high-contrast
// themes, against the side bar and the row backgrounds they sit on. No browser: the colours come
// from media/views.css (its `--ger-*` tokens, per theme kind) and test/gallery/theme.ts (each
// theme's `--vscode-*` values); `var()` fallbacks, `color-mix(in srgb, …)` and alpha are resolved
// here as a browser would. GER_CONTRAST_TABLE=1 prints the table (docs, NOTES).

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { ThemeName, themeVariables } from "./gallery/theme";

const css = fs.readFileSync(path.join(__dirname, "..", "..", "media", "views.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Each rule as its selectors and declarations (views.css has no nesting and no at-rule around a colour). */
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({
  selectors: selector.split(",").map((one) => one.trim()),
  declarations: new Map([...body.matchAll(/([-\w]+)\s*:\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()])),
}));

const KIND: Record<ThemeName, string> = { dark: "vscode-dark", light: "vscode-light", hcDark: "vscode-high-contrast", hcLight: "vscode-high-contrast-light" };
const THEMES: ThemeName[] = ["dark", "light", "hcDark", "hcLight"];

/** The `--ger-*` tokens as a page in `theme` has them: `:root`, then the rules for its theme kind. */
function tokens(theme: ThemeName): Record<string, string> {
  const set: Record<string, string> = {};
  const applies = (selector: string) => selector === ":root" || selector === `body[data-vscode-theme-kind="${KIND[theme]}"]`;
  for (const rule of rules.filter((r) => r.selectors.some(applies))) {
    for (const [name, value] of rule.declarations) {
      if (name.startsWith("--ger-")) {
        set[name] = value;
      }
    }
  }
  return set;
}

type Rgba = [number, number, number, number];

function hex(text: string): Rgba {
  const digits = text.slice(1);
  const full = digits.length <= 4 ? [...digits].map((d) => d + d).join("") : digits;
  const [r, g, b, a] = [0, 2, 4, 6].map((at) => parseInt(full.slice(at, at + 2) || "ff", 16));
  return [r, g, b, a / 255];
}

/** The argument list of a function call, split at its top-level commas. */
function args(inner: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  [...inner].forEach((c, at) => {
    depth += c === "(" ? 1 : c === ")" ? -1 : 0;
    if (c === "," && depth === 0) {
      out.push(inner.slice(start, at).trim());
      start = at + 1;
    }
  });
  return [...out, inner.slice(start).trim()];
}

/** A colour expression of views.css in `theme`; undefined where it resolves to nothing (an unset variable, no fallback). */
function resolve(expression: string, theme: ThemeName): Rgba | undefined {
  const text = expression.trim();
  const call = /^([-\w]+)\(([\s\S]*)\)$/.exec(text);
  if (text.startsWith("#")) {
    return hex(text);
  }
  if (call?.[1] === "var") {
    const [name, fallback] = args(call[2]);
    const value = name.startsWith("--ger-") ? tokens(theme)[name] : themeVariables(theme)[name];
    return (value !== undefined ? resolve(value, theme) : undefined) ?? (fallback ? resolve(fallback, theme) : undefined);
  }
  if (call?.[1] === "color-mix") {
    // color-mix(in srgb, A p%, B): each channel p of A and the rest of B, gamma-encoded as srgb mixes.
    const [, first, second] = args(call[2]);
    const share = Number(/(\d+(?:\.\d+)?)%\s*$/.exec(first)?.[1] ?? "50") / 100;
    const [a, b] = [resolve(first.replace(/\s+\d+(?:\.\d+)?%\s*$/, ""), theme), resolve(second, theme)];
    return a && b ? (a.map((channel, at) => channel * share + b[at] * (1 - share)) as Rgba) : undefined;
  }
  throw new Error(`views.css: cannot read the colour ${text}`);
}

/** `top` drawn over the opaque `under`, with the element's `opacity` on top of its alpha. */
function over(top: Rgba, under: Rgba, opacity = 1): Rgba {
  const alpha = top[3] * opacity;
  return [0, 1, 2].map((at) => top[at] * alpha + under[at] * (1 - alpha)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const linear = (channel: number) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function ratio(a: Rgba, b: Rgba): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

/** The backgrounds a state sits on: the side bar, a row under the pointer, a selected row (a list's, the current step). */
function backgrounds(theme: ThemeName): Record<string, Rgba> {
  const side = resolve("var(--vscode-sideBar-background)", theme)!;
  const on = (name: string) => {
    const colour = resolve(`var(${name})`, theme);
    return colour ? over(colour, side) : side;
  };
  return { "side bar": side, hover: on("--vscode-list-hoverBackground"), selected: on("--vscode-list-inactiveSelectionBackground"), "selected, focused list": on("--vscode-list-activeSelectionBackground") };
}

/** A declared opacity of views.css, by its selector: the theme kind's own rule for it first. */
function opacity(selector: string, theme: ThemeName): number {
  const own = rules.find((rule) => rule.selectors.includes(`body[data-vscode-theme-kind="${KIND[theme]}"] ${selector}`));
  const found = (own ?? rules.find((rule) => rule.selectors.includes(selector)))?.declarations.get("opacity");
  assert.ok(found, `views.css has no opacity for ${selector}`);
  return Number(found);
}

interface Pair {
  what: string;
  colour: string;
  /** Words need 4.5:1, an icon or a ring 3:1; small words drawn on a selection need room for anti-aliasing (5:1). */
  minimum: 5 | 4.5 | 3;
  on: string[];
  /** The selector whose opacity fades it. */
  faded?: string;
}

const ALL = ["side bar", "hover", "selected", "selected, focused list"];
const PAIRS: Pair[] = [
  { what: "good: a +1 vote, replayed, seen, merged", colour: "var(--ger-good)", minimum: 4.5, on: ALL },
  { what: "bad: a -1 vote, CI failed, outdated", colour: "var(--ger-bad)", minimum: 4.5, on: ALL },
  { what: "warn: to answer, code changed, stopped here", colour: "var(--ger-warn)", minimum: 4.5, on: ALL },
  // 22 of 20-states-after: "stopped here", "needs edits" measured 4.3:1 in the pixels at 11 px, where the token gave 4.6.
  { what: "a step's warn mark (stopped here, needs edits) on the current step", colour: "var(--ger-warn)", minimum: 5, on: ["selected"] },
  { what: "info: your drafts, diverged", colour: "var(--ger-info)", minimum: 4.5, on: ALL },
  { what: "dim icon: not on Gerrit, to come", colour: "var(--ger-dim)", minimum: 3, on: ALL },
  { what: "lines added", colour: "var(--ger-added)", minimum: 4.5, on: ["side bar", "hover"] },
  { what: "lines deleted", colour: "var(--ger-deleted)", minimum: 4.5, on: ["side bar", "hover"] },
  { what: "a step's words, a row's details", colour: "var(--vscode-descriptionForeground)", minimum: 4.5, on: ["side bar", "hover"] },
  { what: "a vote of the last pushed patchset (+2?)", colour: "var(--vscode-descriptionForeground)", minimum: 4.5, on: ["side bar", "hover"] },
  { what: "a commit still to be replayed (faded)", colour: "var(--vscode-foreground)", minimum: 4.5, on: ["side bar"], faded: ".row.dim .label" },
  { what: "its details, faded", colour: "var(--vscode-descriptionForeground)", minimum: 4.5, on: ["side bar"], faded: ".row.dim .detail" },
  { what: "a step to come (faded title)", colour: "var(--vscode-foreground)", minimum: 4.5, on: ["side bar"], faded: ".step.phase-todo .step-title" },
  { what: "a link (All chains, Mark all seen)", colour: "var(--vscode-textLink-foreground)", minimum: 4.5, on: ["side bar"] },
  { what: "the focus ring", colour: "var(--vscode-focusBorder)", minimum: 3, on: ["side bar", "hover"] },
  { what: "the focus ring on a selected row", colour: "var(--ger-focus-on-selected)", minimum: 3, on: ["selected", "selected, focused list"] },
];

function measure(): { theme: ThemeName; pair: Pair; on: string; ratio: number }[] {
  return THEMES.flatMap((theme) => {
    const under = backgrounds(theme);
    return PAIRS.flatMap((pair) => {
      const colour = resolve(pair.colour, theme);
      assert.ok(colour, `${pair.colour} is unset in ${theme}`);
      const fade = pair.faded ? opacity(pair.faded, theme) : 1;
      return pair.on.map((on) => ({ theme, pair, on, ratio: ratio(over(colour, under[on], fade), under[on]) }));
    });
  });
}

test("every state colour of the webviews reads in Dark and Light Modern and both high-contrast themes (WCAG 4.5:1 words, 3:1 icons)", () => {
  const all = measure();
  const short = all.filter((row) => row.ratio < row.pair.minimum).map((row) => `${row.theme}, ${row.pair.what} on ${row.on}: ${row.ratio.toFixed(2)} < ${row.pair.minimum}`);
  if (process.env.GER_CONTRAST_TABLE) {
    const head = `| What | Needs | ${THEMES.join(" | ")} |`;
    const lines = PAIRS.flatMap((pair) =>
      pair.on.map((on) => {
        const cells = THEMES.map((theme) => all.find((row) => row.theme === theme && row.pair === pair && row.on === on)!.ratio.toFixed(2));
        return `| ${pair.what} (on ${on}) | ${pair.minimum} | ${cells.join(" | ")} |`;
      }),
    );
    console.log([head, `|---|---|${THEMES.map(() => "---").join("|")}|`, ...lines].join("\n"));
  }
  assert.deepEqual(short, []);
});

test("a state is coloured only through the --ger-* tokens: no rule reaches for a theme's state colour on its own", () => {
  const state = /--vscode-(charts-|testing-iconPassed|list-errorForeground|list-warningForeground|disabledForeground|gitDecoration-)/;
  const tokenRule = (selectors: string[]) => selectors.every((s) => s === ":root" || s.startsWith("body[data-vscode-theme-kind="));
  const bypass = rules
    .filter((rule) => !tokenRule(rule.selectors))
    .flatMap((rule) => [...rule.declarations].filter(([name, value]) => /color|fill|stroke|background/.test(name) && state.test(value)).map(([name]) => `${rule.selectors.join(", ")} { ${name} }`));
  assert.deepEqual(bypass, [], "colour it with var(--ger-…) in views.css, so test/contrast.test.ts measures it");
  // Greyed is not a state on its own (Q4): no state is drawn at half opacity.
  const faint = rules.filter((rule) => Number(rule.declarations.get("opacity") ?? 1) < 0.6 && rule.selectors.some((s) => /badge|sync|tone|step|mark/.test(s)));
  assert.deepEqual(faint.map((rule) => rule.selectors.join(", ")), []);
});

test("the measuring is right: black on white is 21:1, a colour on itself 1:1, and a mix lies between", () => {
  assert.equal(ratio(hex("#000000"), hex("#ffffff")).toFixed(1), "21.0");
  assert.equal(ratio(hex("#777777"), hex("#777777")), 1);
  assert.deepEqual(over(hex("#00000080"), hex("#ffffff")).map(Math.round), [127, 127, 127, 1]);
  assert.deepEqual(resolve("color-mix(in srgb, #ffffff 25%, #000000)", "dark")?.map(Math.round), [64, 64, 64, 1]);
  assert.deepEqual(resolve("var(--vscode-no-such, #123456)", "dark"), hex("#123456"));
});
