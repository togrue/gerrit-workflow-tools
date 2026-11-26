// What VS Code gives a webview that a plain browser does not: the `--vscode-*` variables of the
// colour theme, and the default styles of a webview's page. Two themes, VS Code's defaults:
// Dark Modern and Light Modern. test/gallery.test.ts fails when a page in media/ uses a variable
// that is not listed here.

export type ThemeName = "dark" | "light";

// The fonts are the workbench's and the editor's, not the theme's: as on Windows, the other platforms' following.
const UI_FONT = `"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, "Ubuntu", "Droid Sans", sans-serif`;
const EDITOR_FONT = `Consolas, "Courier New", Menlo, Monaco, "Droid Sans Mono", monospace`;

/** Per variable (without `--vscode-`): its value in [dark, light]; null where the theme leaves it unset and the page's fallback applies, as in VS Code. */
const VARIABLES: Record<string, [string | null, string | null]> = {
  "font-family": [UI_FONT, UI_FONT],
  "font-size": ["13px", "13px"],
  "font-weight": ["normal", "normal"],
  "editor-font-family": [EDITOR_FONT, EDITOR_FONT],
  "editor-font-size": ["14px", "14px"],
  "editor-font-weight": ["normal", "normal"],

  foreground: ["#cccccc", "#3b3b3b"],
  disabledForeground: ["#cccccc80", "#61616180"],
  descriptionForeground: ["#9d9d9d", "#3b3b3b"],
  focusBorder: ["#0078d4", "#005fb8"],
  "widget-border": ["#313131", "#e5e5e5"],
  "widget-shadow": ["#0000005c", "#00000029"],
  "editorHoverWidget-background": ["#202020", "#f8f8f8"],
  "editorHoverWidget-foreground": ["#cccccc", "#3b3b3b"],
  "editorHoverWidget-border": ["#454545", "#c8c8c8"],
  "textLink-foreground": ["#4daafc", "#005fb8"],
  "textLink-activeForeground": ["#4daafc", "#005fb8"],
  "textPreformat-foreground": ["#d0d0d0", "#3b3b3b"],

  "editor-background": ["#1f1f1f", "#ffffff"],
  "editor-foreground": ["#cccccc", "#3b3b3b"],
  "sideBar-background": ["#181818", "#f8f8f8"],
  "sideBarSectionHeader-border": ["#2b2b2b", "#e5e5e5"],

  "list-hoverBackground": ["#2a2d2e", "#f2f2f2"],
  "list-inactiveSelectionBackground": ["#37373d", "#e4e6f1"],
  "list-inactiveSelectionForeground": [null, null],
  "list-activeSelectionBackground": ["#04395e", "#e8e8e8"],
  "list-activeSelectionForeground": ["#ffffff", "#000000"],
  "list-focusOutline": ["#0078d4", "#005fb8"],
  "list-errorForeground": ["#f88070", "#b01011"],
  "list-warningForeground": ["#cca700", "#855f00"],
  "toolbar-hoverBackground": ["#5a5d5e50", "#b8b8b850"],

  "input-background": ["#313131", "#ffffff"],
  "input-foreground": ["#cccccc", "#3b3b3b"],
  "input-border": ["#3c3c3c", "#cecece"],
  "dropdown-background": ["#313131", "#ffffff"],
  "dropdown-foreground": ["#cccccc", "#3b3b3b"],
  "dropdown-border": ["#3c3c3c", "#cecece"],
  "checkbox-selectBackground": ["#202020", "#f8f8f8"],

  "button-background": ["#0078d4", "#005fb8"],
  "button-foreground": ["#ffffff", "#ffffff"],
  "button-hoverBackground": ["#026ec1", "#0258a8"],
  "button-border": ["#ffffff12", "#0000001a"],
  "button-secondaryBackground": ["#313131", "#e5e5e5"],
  "button-secondaryForeground": ["#cccccc", "#3b3b3b"],
  "button-secondaryHoverBackground": ["#3c3c3c", "#cccccc"],

  "charts-red": ["#f14c4c", "#e51400"],
  "charts-yellow": ["#cca700", "#bf8803"],
  "charts-green": ["#89d185", "#388a34"],
  "charts-blue": ["#3794ff", "#1a85ff"],
  "testing-iconPassed": ["#73c991", "#73c991"],
  "gitDecoration-addedResourceForeground": ["#81b88b", "#587c0c"],
  "gitDecoration-deletedResourceForeground": ["#c74e39", "#ad0707"],

  "scrollbarSlider-background": ["#79797966", "#64646466"],
  "scrollbarSlider-hoverBackground": ["#646464b3", "#646464b3"],
  "scrollbarSlider-activeBackground": ["#bfbfbf66", "#00000099"],
};

/** Every variable the gallery knows of, set or deliberately left unset. */
export function themedVariables(): string[] {
  return Object.keys(VARIABLES).map((name) => `--vscode-${name}`);
}

/** The variables `theme` sets, as `--vscode-…` → value. */
export function themeVariables(theme: ThemeName): Record<string, string> {
  const set: Record<string, string> = {};
  for (const name of Object.keys(VARIABLES)) {
    const is = VARIABLES[name][theme === "dark" ? 0 : 1];
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
  return `:root {\n  color-scheme: ${theme};\n${variables.join("\n")}\n}\n${DEFAULT_STYLES}`;
}

/** The classes VS Code puts on a webview's body for the theme kind. */
export function themeClass(theme: ThemeName): string {
  return theme === "dark" ? "vscode-dark" : "vscode-light";
}
