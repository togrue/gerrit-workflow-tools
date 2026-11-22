// Runs after `npm install` / `npm ci`: puts VS Code's icon font next to the webviews' own files, so
// *Local stack* and *Change* draw the same icons as the tree beside them. Webviews cannot reach the
// editor's own copy. With them go their licences: the font is CC BY 4.0 (LICENSE), the CSS is MIT
// (LICENSE-CODE), and both ask for the licence to travel with the copy. All four files are build
// output (media/.gitignore) and go into the .vsix.

const fs = require("node:fs");
const path = require("node:path");

const from = path.join(__dirname, "..", "node_modules", "@vscode", "codicons");
const to = path.join(__dirname, "..", "media");

const files = {
  "dist/codicon.css": "codicon.css",
  "dist/codicon.ttf": "codicon.ttf",
  LICENSE: "codicon-LICENSE.txt",
  "LICENSE-CODE": "codicon-LICENSE-CODE.txt",
};
for (const [source, target] of Object.entries(files)) {
  fs.copyFileSync(path.join(from, source), path.join(to, target));
}
