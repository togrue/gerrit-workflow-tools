// What VS Code gives a webview that a plain browser does not: the `--vscode-*` variables of the
// colour theme, and the default styles of a webview's page. VS Code's default themes: Dark Modern
// and Light Modern, which the gallery draws, and the two high-contrast ones (Dark High Contrast,
// Light High Contrast: VS Code 1.148's colour registry and the git extension's colours), which
// test/contrast.test.ts measures too. test/gallery.test.ts fails when a page in media/ uses a variable
// that is not listed here.

export type ThemeName = "dark" | "light" | "hcDark" | "hcLight";

// The fonts are the workbench's and the editor's, not the theme's: as on Windows, the other platforms' following.
const UI_FONT = `"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, "Ubuntu", "Droid Sans", sans-serif`;
const EDITOR_FONT = `Consolas, "Courier New", Menlo, Monaco, "Droid Sans Mono", monospace`;

/** Per variable (without `--vscode-`): its value in [dark, light, hcDark, hcLight]; null where the theme leaves it unset and the page's fallback applies, as in VS Code. */
const VARIABLES: Record<string, [string | null, string | null, string | null, string | null]> = {
  "font-family": [UI_FONT, UI_FONT, UI_FONT, UI_FONT],
  "font-size": ["13px", "13px", "13px", "13px"],
  "font-weight": ["normal", "normal", "normal", "normal"],
  "editor-font-family": [EDITOR_FONT, EDITOR_FONT, EDITOR_FONT, EDITOR_FONT],
  "editor-font-size": ["14px", "14px", "14px", "14px"],
  "editor-font-weight": ["normal", "normal", "normal", "normal"],

  foreground: ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  disabledForeground: ["#cccccc80", "#61616180", "#a5a5a5", "#7f7f7f"],
  descriptionForeground: ["#9d9d9d", "#3b3b3b", "#ffffffb3", "#292929b3"],
  focusBorder: ["#0078d4", "#005fb8", "#f38518", "#006bbd"],
  "widget-border": ["#313131", "#e5e5e5", "#6fc3df", "#0f4a85"],
  "widget-shadow": ["#0000005c", "#00000029", null, null],
  "editorHoverWidget-background": ["#202020", "#f8f8f8", "#0c141f", "#ffffff"],
  "editorHoverWidget-foreground": ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  "editorHoverWidget-border": ["#454545", "#c8c8c8", "#6fc3df", "#0f4a85"],
  "textLink-foreground": ["#4daafc", "#005fb8", "#21a6ff", "#0f4a85"],
  "textLink-activeForeground": ["#4daafc", "#005fb8", "#21a6ff", "#0f4a85"],
  "textPreformat-foreground": ["#d0d0d0", "#3b3b3b", "#ffffff", "#292929"],

  "editor-background": ["#1f1f1f", "#ffffff", "#000000", "#ffffff"],
  "editor-foreground": ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  "sideBar-background": ["#181818", "#f8f8f8", "#000000", "#ffffff"],
  "sideBarSectionHeader-border": ["#2b2b2b", "#e5e5e5", "#6fc3df", "#0f4a85"],

  "list-hoverBackground": ["#2a2d2e", "#f2f2f2", "#ffffff1a", "#0f4a851a"],
  "list-inactiveSelectionBackground": ["#37373d", "#e4e6f1", null, "#0f4a851a"],
  "list-inactiveSelectionForeground": [null, null, null, null],
  "list-activeSelectionBackground": ["#04395e", "#e8e8e8", null, "#0f4a851a"],
  "list-activeSelectionForeground": ["#ffffff", "#000000", null, null],
  "list-focusAndSelectionOutline": [null, "#005fb8", null, null],
  contrastActiveBorder: [null, null, "#f38518", "#006bbd"],
  "list-focusOutline": ["#0078d4", "#005fb8", "#f38518", "#006bbd"],
  "list-errorForeground": ["#f88070", "#b01011", null, null],
  "list-warningForeground": ["#cca700", "#855f00", null, null],
  "toolbar-hoverBackground": ["#5a5d5e50", "#b8b8b850", null, null],

  "input-background": ["#313131", "#ffffff", "#000000", "#ffffff"],
  "input-foreground": ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  "input-border": ["#3c3c3c", "#cecece", "#6fc3df", "#0f4a85"],
  "dropdown-background": ["#313131", "#ffffff", "#000000", "#ffffff"],
  "dropdown-foreground": ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  "dropdown-border": ["#3c3c3c", "#cecece", "#6fc3df", "#0f4a85"],
  "inputOption-activeBorder": ["#2488db", "#005fb8", "#6fc3df", "#0f4a85"],
  "inputOption-activeBackground": ["#2489db82", "#bed6ed", "#00000000", "#00000000"],
  "inputOption-activeForeground": ["#ffffff", "#000000", "#ffffff", "#292929"],
  "checkbox-selectBackground": ["#202020", "#f8f8f8", "#0c141f", "#ffffff"],

  "button-background": ["#0078d4", "#005fb8", "#000000", "#0f4a85"],
  "button-foreground": ["#ffffff", "#ffffff", "#ffffff", "#ffffff"],
  "button-hoverBackground": ["#026ec1", "#0258a8", "#000000", "#0f4a85"],
  "button-border": ["#ffffff12", "#0000001a", "#6fc3df", "#0f4a85"],
  "button-secondaryBackground": ["#313131", "#e5e5e5", null, "#ffffff"],
  "button-secondaryForeground": ["#cccccc", "#3b3b3b", "#ffffff", "#292929"],
  "button-secondaryHoverBackground": ["#3c3c3c", "#cccccc", null, null],

  "charts-red": ["#f14c4c", "#e51400", "#f48771", "#b5200d"],
  "charts-yellow": ["#cca700", "#bf8803", "#ffd370", "#895503"],
  "charts-green": ["#89d185", "#388a34", "#89d185", "#374e06"],
  "charts-blue": ["#3794ff", "#1a85ff", "#59a4f9", "#0063d3"],
  "testing-iconPassed": ["#73c991", "#73c991", "#73c991", "#007100"],
  "gitDecoration-addedResourceForeground": ["#81b88b", "#587c0c", "#a1e3ad", "#374e06"],
  "gitDecoration-deletedResourceForeground": ["#c74e39", "#ad0707", "#c74e39", "#ad0707"],

  "scrollbarSlider-background": ["#79797966", "#64646466", "#6fc3df99", "#0f4a8566"],
  "scrollbarSlider-hoverBackground": ["#646464b3", "#646464b3", "#6fc3dfcc", "#0f4a85cc"],
  "scrollbarSlider-activeBackground": ["#bfbfbf66", "#00000099", "#6fc3df", "#0f4a85"],
};

const THEME_COLUMN: Record<ThemeName, number> = { dark: 0, light: 1, hcDark: 2, hcLight: 3 };

/** Every variable the gallery knows of, set or deliberately left unset. */
export function themedVariables(): string[] {
  return Object.keys(VARIABLES).map((name) => `--vscode-${name}`);
}

/** The variables `theme` sets, as `--vscode-…` → value. */
export function themeVariables(theme: ThemeName): Record<string, string> {
  const set: Record<string, string> = {};
  for (const name of Object.keys(VARIABLES)) {
    const is = VARIABLES[name][THEME_COLUMN[theme]];
    if (is !== null) {
      set[`--vscode-${name}`] = is;
    }
  }
  return set;
}

/**
 * The page's default styles, as VS Code injects them into every webview (its `_defaultStyles`),
 * for what the two pages can hit. The body is transparent there and the view's container shows
 * through: here the `html` element stands for it, in the side bar's colour.
 */
const DEFAULT_STYLES = `
html {
  background: var(--vscode-sideBar-background);
  scrollbar-color: var(--vscode-scrollbarSlider-background) transparent;
}
body {
  background-color: transparent;
  color: var(--vscode-editor-foreground);
  font-family: var(--vscode-font-family);
  font-weight: var(--vscode-font-weight);
  font-size: var(--vscode-font-size);
  margin: 0;
  padding: 0 20px;
}
img, video { max-width: 100%; max-height: 100%; }
a, a code { color: var(--vscode-textLink-foreground); }
a:hover { color: var(--vscode-textLink-activeForeground); }
a:focus, input:focus, select:focus, textarea:focus { outline: 1px solid -webkit-focus-ring-color; outline-offset: -1px; }
code { color: var(--vscode-textPreformat-foreground); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-corner { background-color: var(--vscode-editor-background); }
::-webkit-scrollbar-thumb { background-color: var(--vscode-scrollbarSlider-background); }
::-webkit-scrollbar-thumb:hover { background-color: var(--vscode-scrollbarSlider-hoverBackground); }
::-webkit-scrollbar-thumb:active { background-color: var(--vscode-scrollbarSlider-activeBackground); }
`;

/** The stylesheet that stands for VS Code in `theme`: its variables on the root, then the default styles. */
export function themeCss(theme: ThemeName): string {
  const variables = Object.entries(themeVariables(theme)).map(([name, is]) => `  ${name}: ${is};`);
  return `:root {\n  color-scheme: ${theme === "dark" || theme === "hcDark" ? "dark" : "light"};\n${variables.join("\n")}\n}\n${DEFAULT_STYLES}`;
}

/** The classes VS Code puts on a webview's body for the theme kind. */
export function themeClass(theme: ThemeName): string {
  return { dark: "vscode-dark", light: "vscode-light", hcDark: "vscode-high-contrast", hcLight: "vscode-high-contrast-light" }[theme];
}
