// The gallery: a static page that shows every scenario in the real pages of media/, each in an
// iframe per theme and side bar width. This writes it: the pages' own files copied as they are,
// one frame document per scenario, view and theme, a stylesheet per theme that stands for
// VS Code (theme.ts), and the page around them.

import * as fs from "node:fs";
import * as path from "node:path";

import { Scenario } from "./scenarios";
import { ThemeName, themeClass, themeCss } from "./theme";

export const THEMES: ThemeName[] = ["dark", "light"];
/** Side bar widths: the narrowest a side bar is used at, the usual one, and one dragged wider. */
export const WIDTHS = [200, 300, 450];
export const VIEWS = ["stack", "change"] as const;
export type View = (typeof VIEWS)[number];

const TITLES: Record<View, string> = { stack: "Local stack", change: "Change" };

/** The files a webview page loads (src/webview.ts), and the font codicon.css names. */
export const PAGE_FILES = ["codicon.css", "codicon.ttf", "views.css", "common.js", "stack.js", "change.js"];
/** The harness's own files, next to this module's source. */
const HARNESS_FILES = ["frame.js", "index.js", "index.css", "shot.html"];

const root = path.join(__dirname, "..", "..", "..");

const escapeHtml = (text: string) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c);

/** One frame document: a scenario's view in a theme. Its width is the frame's. */
export interface Frame {
  scenario: Scenario;
  view: View;
  theme: ThemeName;
  /** Names the frame's file, and with a width its picture: `<scenario>.<view>.<theme>`. */
  id: string;
  /** Relative to the gallery's folder. */
  file: string;
}

/** A file name for each scenario, from its name; a second one of the same name gets a number. */
export function slugs(scenarios: readonly Scenario[]): string[] {
  const taken = new Map<string, number>();
  return scenarios.map((scenario) => {
    const slug = scenario.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "scenario";
    const count = (taken.get(slug) ?? 0) + 1;
    taken.set(slug, count);
    return count > 1 ? `${slug}-${count}` : slug;
  });
}

/** Every frame of the gallery: per scenario, the views it has something for, in each theme. */
export function frames(scenarios: readonly Scenario[]): Frame[] {
  const names = slugs(scenarios);
  return scenarios.flatMap((scenario, index) =>
    VIEWS.filter((view) => scenario[view] !== null).flatMap((view) =>
      THEMES.map((theme) => {
        const id = `${names[index]}.${view}.${theme}`;
        return { scenario, view, theme, id, file: `frames/${id}.html` };
      }),
    ),
  );
}

/**
 * The document of one frame: the page of src/webview.ts (the same files, in the same order, the
 * same body), with VS Code's part in front of it: the theme's stylesheet before the page's, the
 * body's theme class, and frame.js before the page's scripts.
 */
export function frameHtml(frame: Frame): string {
  const { scenario, view, theme } = frame;
  // Inside a script element only `<` could end it early.
  const data = JSON.stringify({ vm: scenario[view], click: scenario.click ?? [] }).replace(/</g, "\\u003c");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>${escapeHtml(`${scenario.name} · ${TITLES[view]} · ${theme}`)}</title>
<link rel="stylesheet" href="../theme-${theme}.css">
<link rel="stylesheet" href="../media/codicon.css">
<link rel="stylesheet" href="../media/views.css">
</head>
<body class="${view} ${themeClass(theme)}" data-vscode-theme-kind="${themeClass(theme)}">
<div id="root"></div>
<script type="application/json" id="gallery">${data}</script>
<script src="../frame.js"></script>
<script src="../media/common.js"></script>
<script src="../media/${view}.js"></script>
</body>
</html>
`;
}

/** The page around the frames: per scenario, each of its views in every theme and width. */
export function indexHtml(scenarios: readonly Scenario[]): string {
  const all = frames(scenarios);
  const names = slugs(scenarios);
  const pane = (frame: Frame, width: number) => `<figure class="pane ${frame.theme}" style="width: ${width}px">
<figcaption><b>${TITLES[frame.view]}</b><span>${frame.theme} · ${width}px</span></figcaption>
<iframe src="${frame.file}" data-shot="${frame.id}.${width}" width="${width}" title="${escapeHtml(`${frame.scenario.name}: ${TITLES[frame.view]}, ${frame.theme}, ${width}px`)}"></iframe>
<ol class="log" aria-label="What the page posted to the extension"></ol>
</figure>`;
  const sections = scenarios.map((scenario, index) => {
    const rows = VIEWS.map((view) => all.filter((frame) => frame.scenario === scenario && frame.view === view))
      .filter((row) => row.length)
      .map((row) => `<div class="panes">\n${row.flatMap((frame) => WIDTHS.map((width) => pane(frame, width))).join("\n")}\n</div>`);
    return `<section id="${names[index]}">\n<h2>${escapeHtml(scenario.name)}</h2>\n${rows.join("\n")}\n</section>`;
  });
  const links = scenarios.map((scenario, index) => `<a href="#${names[index]}">${escapeHtml(scenario.name)}</a>`);
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ger for VS Code: webview gallery</title>
<link rel="stylesheet" href="index.css">
<script src="index.js"></script>
</head>
<body>
<h1>ger for VS Code: webview gallery</h1>
<p>The pages of <i>Local stack</i> and <i>Change</i> as the extension ships them, drawing the view model of each scenario in VS Code's Dark Modern and Light Modern colours, at two side bar widths. Under each frame: what its page posted to the extension (click, drag, right-click to see).</p>
<nav>${links.join("\n")}</nav>
${sections.join("\n")}
</body>
</html>
`;
}

/** Write the gallery of `scenarios` into `out` (emptied first) and return the path of its page. */
export function writeGallery(out: string, scenarios: readonly Scenario[]): string {
  const media = path.join(root, "media");
  const missing = PAGE_FILES.filter((file) => !fs.existsSync(path.join(media, file)));
  if (missing.length) {
    throw new Error(`media/ lacks ${missing.join(", ")}: run npm install, which copies the icon font there`);
  }
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(path.join(out, "media"), { recursive: true });
  fs.mkdirSync(path.join(out, "frames"));
  for (const file of PAGE_FILES) {
    fs.copyFileSync(path.join(media, file), path.join(out, "media", file));
  }
  for (const file of HARNESS_FILES) {
    fs.copyFileSync(path.join(root, "test", "gallery", file), path.join(out, file));
  }
  for (const theme of THEMES) {
    fs.writeFileSync(path.join(out, `theme-${theme}.css`), themeCss(theme));
  }
  for (const frame of frames(scenarios)) {
    fs.writeFileSync(path.join(out, frame.file), frameHtml(frame));
  }
  const index = path.join(out, "index.html");
  fs.writeFileSync(index, indexHtml(scenarios));
  return index;
}
