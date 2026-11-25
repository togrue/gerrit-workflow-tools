// Suggested edits: how a thread shows them, and when Apply writes one into your file.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FixReplacement, SuggestedEdit } from "../src/api";
import { APPLY_WORDS, applyPlan, latestSuggestion, suggestionMarkdown } from "../src/suggestions";
import { confirmVote } from "../src/voteText";
import { comment } from "./fixtures";

const whole = { start_line: 11, start_character: 0, end_line: 12, end_character: 0 };

function block(replacement: Partial<FixReplacement> = {}): SuggestedEdit {
  return { fix_id: null, description: "", in_message: true, replacements: [{ path: "calc/stats.py", range: whole, replacement: "    values = sorted(values)\n", location: "calc/stats.py:11", ...replacement }] };
}

const message = "Sort a copy, not the caller's list.\n\n```suggestion\n    values = sorted(values)\n```";

test("a suggestion block shows as old → new in its place, labelled", () => {
  const shown = suggestionMarkdown(comment(message, { suggested_edits: [block()] }), () => "    values.sort()\n");
  assert.equal(shown, "Sort a copy, not the caller's list.\n\n**Suggested edit**\n```diff\n-    values.sort()\n+    values = sorted(values)\n```");
  // What it replaces not known (not your commit): the new lines alone.
  assert.equal(suggestionMarkdown(comment(message, { suggested_edits: [block()] }), () => null), "Sort a copy, not the caller's list.\n\n**Suggested edit**\n```diff\n+    values = sorted(values)\n```");
});

test("a Gerrit fix suggestion shows below the text, where it applies named", () => {
  const fix: SuggestedEdit = { ...block(), fix_id: "f1", description: "Sort a copy", in_message: false };
  const shown = suggestionMarkdown(comment("Sort a copy.", { suggested_edits: [fix], markdown: "core's own" }), () => "    values.sort()\n");
  assert.equal(shown, "Sort a copy.\n\n**Suggested edit**: Sort a copy\n`calc/stats.py:11`\n```diff\n-    values.sort()\n+    values = sorted(values)\n```");
  assert.equal(suggestionMarkdown(comment("Plain.", { markdown: "Plain, as core writes it." }), () => null), "Plain, as core writes it.");
});

test("Apply goes where core says the lines are now, once they still say what it replaces", () => {
  const now = { start_line: 13, start_character: 0, end_line: 14, end_character: 0 };
  const placed = block({ worktree: now, original: "    values.sort()\n" });
  assert.deepEqual(applyPlan(placed, () => "    values.sort()\n"), {
    places: [{ path: "calc/stats.py", start: { line: 12, character: 0 }, end: { line: 13, character: 0 }, replacement: "    values = sorted(values)\n" }],
  });
  assert.deepEqual(applyPlan(placed, () => "    values.sort(reverse=True)\n"), { refused: APPLY_WORDS.differs("calc/stats.py:13") }, "edited since: unsaved edits count");
  assert.deepEqual(applyPlan(block({ worktree: null, original: "    values.sort()\n" }), () => ""), { refused: APPLY_WORDS.moved("calc/stats.py:11") });
});

test("Apply takes the newest comment that suggests an edit", () => {
  const older = comment("first", { suggested_edits: [block({ replacement: "a\n" })] });
  const newer = comment("second", { suggested_edits: [block({ replacement: "b\n" })] });
  assert.equal(latestSuggestion([older, newer, comment("Thanks")])?.comment, newer);
  assert.equal(latestSuggestion([comment("none")]), undefined);
});

test("the vote dialog says which draft carries a suggested edit", () => {
  const draft = { path: "a.py", line: 3, location: "a.py:3", text: "Sort a copy.", transition: null, suggested_edit: true };
  const plan = { changes: [{ number: 7, subject: "Add median", own: false, drafts: [draft] }] } as unknown as Parameters<typeof confirmVote>[0];
  assert.match(confirmVote(plan, undefined, "").detail, /a\.py:3 {2}"Sort a copy\." \(with a suggested edit\)/);
});
