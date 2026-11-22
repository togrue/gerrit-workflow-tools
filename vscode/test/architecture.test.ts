// One owner per icon meaning. The comment bubble (`comment-unresolved`) means unresolved comments
// the Change view lists; twice a private severity-to-icon table drew it for any warning (A4), so
// a negative vote wore it. This keeps a third such mapping from coming back.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

const src = path.join(__dirname, "..", "..", "src");

// The only places that may name the comment icon:
// - format.ts: attentionIcon, the one mapping from attention reasons to an icon;
// - stackRows.ts: the local stack row's comment badge, drawn from the count of unresolved comments;
// - changeRows.ts: the Change view's thread rows, each an open comment chain it lists.
const ALLOWED = ["changeRows.ts", "format.ts", "stackRows.ts"];

test("only attentionIcon, the comment badge and the comment threads draw the comment icon", () => {
  const using = fs
    .readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((file) => file.endsWith(".ts"))
    .filter((file) => fs.readFileSync(path.join(src, file), "utf8").includes("comment-unresolved"))
    .map((file) => file.split(path.sep).join("/"))
    .sort();
  assert.deepEqual(using, ALLOWED, "derive the icon from attentionIcon (format.ts) instead of naming comment-unresolved");
});
