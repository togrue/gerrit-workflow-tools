// The change a review step goes to, asked once per patchset: ahead while you read (Next), or at the
// press (Previous, a neighbour clicked in the task header). src/reviewAhead.ts.

import assert from "node:assert/strict";
import test from "node:test";

import type { GerClient, Member, ShownCommit } from "../src/api";
import { ReviewAhead } from "../src/reviewAhead";

const member = (number: number, sha: string) => ({ number, sha }) as unknown as Member;

test("a change is asked once per patchset, and a new patchset is asked anew", async () => {
  const asked: string[] = [];
  const client = {
    show: async (revs: string[]) => {
      asked.push(revs[0]);
      return [{ sha: revs[0] === "change:2" ? "b2" : "c3", files: { files: [] } } as unknown as ShownCommit];
    },
  } as unknown as GerClient;
  const ahead = new ReviewAhead(async () => client);
  const below = member(2, "b2");
  assert.equal(ahead.has(below), false, "Previous: not asked ahead, so asked at the press");
  ahead.fetch(below);
  ahead.fetch(below);
  assert.equal(ahead.has(below), true);
  assert.equal((await ahead.take(below))?.shown.sha, "b2");
  assert.deepEqual(asked, ["change:2"], "one request");
  assert.equal(ahead.has(member(2, "b2-new")), false, "another patchset is not what was asked");
  assert.equal(await ahead.take(member(2, "b2-new")), undefined);
  ahead.forget();
  assert.equal(ahead.has(below), false);
});
