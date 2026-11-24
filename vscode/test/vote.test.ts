// Vote and Publish's words: the votes as core offers them, what the picker opens on, and the one
// confirmation, which must list everything that goes out.

import assert from "node:assert/strict";
import { test } from "node:test";

import { VoteChange, VotePlan } from "../src/api";
import { confirmVote, defaultVoteItem, sentVote, voteItems, voteTitle } from "../src/voteText";

const change = (number: number, drafts: VoteChange["drafts"] = [], own = false): VoteChange => ({
  number,
  change: `p~main~I${number}`,
  subject: `Change ${number}`,
  patch_set: 1,
  url: null,
  owner: own ? "me" : "Bob",
  own,
  permitted: { "Code-Review": [-1, 0, 1] },
  ruled_out: [],
  drafts,
});

const nit = { path: "a.py", line: 3, location: "a.py:3", text: "nit: a name", transition: "resolves" as const };

const plan = (changes: VoteChange[]): VotePlan => ({
  changes,
  votes: {},
  drafts: changes.reduce((sum, c) => sum + c.drafts.length, 0),
  options: [
    { label: "Code-Review", value: 2, meaning: "Looks good to me, approved", takes: [changes[changes.length - 1].number] },
    { label: "Code-Review", value: 1, meaning: "Looks good to me, but someone else must approve", takes: changes.map((c) => c.number) },
    { label: "Code-Review", value: 0, meaning: "No score", takes: changes.map((c) => c.number) },
    { label: "Code-Review", value: -1, meaning: "", takes: changes.map((c) => c.number) },
  ],
});

test("the picker offers core's votes with what each means, then the drafts without a vote, and opens on +1", () => {
  const shown = plan([change(900, [nit])]);
  const items = voteItems(shown);
  assert.deepEqual(
    items.map((item) => [item.label, item.description]),
    [
      ["$(thumbsup) Code-Review +2", "Looks good to me, approved"],
      ["$(thumbsup) Code-Review +1", "Looks good to me, but someone else must approve"],
      ["$(circle-large-outline) Code-Review 0", "No score"],
      ["$(thumbsdown) Code-Review -1", undefined],
      ["$(comment) Publish drafts only, no vote", "1 draft"],
    ],
  );
  assert.equal(defaultVoteItem(shown, items)?.label, "$(thumbsup) Code-Review +1");
  assert.equal(voteTitle(shown), 'Vote on 900 "Change 900"');
});

test("on your own change the picker opens on your drafts without a vote; with no drafts that is not offered", () => {
  const own = plan([change(101, [nit], true)]);
  assert.equal(defaultVoteItem(own, voteItems(own))?.option, undefined);
  const bare = plan([change(900)]);
  assert.ok(voteItems(bare).every((item) => item.option), "nothing to publish without a vote");
});

test("a chain vote says which changes a vote skips, and the confirmation lists each draft on each change", () => {
  const chain = plan([change(100, [nit]), change(101), change(102)]);
  const items = voteItems(chain);
  assert.equal(items[0].detail, "only on change 102: the other changes do not take it");
  assert.equal(items[1].detail, undefined);
  const plusOne = items[1].option;
  const asked = confirmVote(chain, plusOne, "lgtm");
  assert.equal(asked.text, "Publish on 100, 101, 102?");
  assert.deepEqual(asked.detail.split("\n"), [
    'Code-Review +1 · 1 draft · message "lgtm"',
    "",
    "100 Change 100",
    '  a.py:3  "nit: a name" (resolves)',
    "",
    "101 Change 101: no drafts",
    "",
    "102 Change 102: no drafts",
    "",
    "Everyone on the change sees it at once; it cannot be taken back.",
  ]);
  const plusTwo = confirmVote(chain, items[0].option, "");
  assert.equal(plusTwo.text, "Publish on change 102?");
  assert.ok(plusTwo.detail.includes("Skipped (it does not take Code-Review +2): 100, 101"));
});

test("once sent, one line says what went out where", () => {
  const sent = {
    changes: [{ change: "k", number: 900, drafts: 2, labels: { "Code-Review": 1 }, message: null, status: "published" }],
    skipped: [{ change: "j", number: 899, ruled_out: ["Code-Review+2 (you may give -1, 0, +1)"] }],
  };
  assert.equal(sentVote(sent, { label: "Code-Review", value: 1, meaning: "", takes: [900] }), "Published Code-Review +1 and 2 drafts on change 900. Skipped change 899.");
  assert.equal(sentVote({ ...sent, skipped: [] }, undefined), "Published 2 drafts on change 900.");
});
