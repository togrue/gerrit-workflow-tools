// The extension makes the terminal taller when ger push says it waits for room (state 19): the two
// sides name the outcome with the same words.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { PUSH_WAITS } from "../src/stackRows";

test("ger push's outcome while its terminal is too low is the one the extension waits for", () => {
  const prompt = fs.readFileSync(path.join(__dirname, "..", "..", "..", "src", "gerrit_workflow_tools", "push_input_prompt.py"), "utf8");
  assert.ok(prompt.includes(`PUSH_WAITS = "${PUSH_WAITS}"`));
});
