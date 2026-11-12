// Runs after `npm install` / `npm ci`: puts VS Code's icon font next to the webviews' own files, so
// *Local stack* and *Change* draw the same icons as the tree beside them. Webviews cannot reach the
// editor's own copy. The two files are build output (media/.gitignore) and go into the .vsix.

const fs = require("node:fs");
const path = require("node:path");

const from = path.join(__dirname, "..", "node_modules", "@vscode", "codicons", "dist");
const to = path.join(__dirname, "..", "media");

for (const file of ["codicon.css", "codicon.ttf"]) {
  fs.copyFileSync(path.join(from, file), path.join(to, file));
}
