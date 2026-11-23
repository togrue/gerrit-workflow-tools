// Whether the extension can use the ger serve that answered (compat.ts): the API rule, and package versions.

import assert from "node:assert/strict";
import { test } from "node:test";

import { IncompatibleServe, apiProblem, packageSkew } from "../src/compat";
import { failureState, waitingFor, waitsForYou } from "../src/state";

test("a served API with the same major and a minor at least the one needed is accepted; the patch never matters", () => {
  assert.equal(apiProblem("1.0.0", "1.0.0"), undefined, "equal");
  assert.equal(apiProblem("1.3.0", "1.2.0"), undefined, "minor higher");
  assert.equal(apiProblem("1.2.0", "1.2.7"), undefined, "patch lower");
  assert.equal(apiProblem("1.2.9", "1.2.0"), undefined, "patch higher");
  assert.equal(apiProblem(1, "1.0.0"), undefined, "a bare 1 from an older ger is 1.0.0");
});

test("another major, or a lower minor, is refused, and says which side to update", () => {
  const lowerMinor = apiProblem("1.1.0", "1.2.0");
  assert.ok(lowerMinor instanceof IncompatibleServe);
  assert.equal(lowerMinor.gerOlder, true);
  assert.equal(lowerMinor.message, "ger serve speaks API 1.1, the extension needs 1.2: update ger.");

  const olderMajor = apiProblem("1.9.0", "2.0.0");
  assert.equal(olderMajor?.gerOlder, true);

  const newerMajor = apiProblem("2.0.0", "1.4.0");
  assert.equal(newerMajor?.gerOlder, false);
  assert.equal(newerMajor?.message, "ger serve speaks API 2.0, the extension needs 1.4: update the extension, or install the ger release that matches it.");

  assert.equal(apiProblem(undefined, "1.0.0")?.message, "ger serve speaks API none, the extension needs 1.0: update ger.");
});

test("a refusal is its own state: the views name it and offer the fix, and polling waits for you", () => {
  const older = apiProblem("1.0.0", "1.1.0");
  const newer = apiProblem("2.0.0", "1.1.0");
  assert.equal(failureState(older), "incompatible");
  assert.ok(waitsForYou("incompatible"));
  assert.deepEqual(waitingFor("incompatible", "Your local stack", older), {
    text: "ger serve speaks API 1.0, the extension needs 1.1: update ger.",
    link: { label: "Update ger", command: "ger.installCli" },
  });
  assert.equal(waitingFor("incompatible", "A change", newer)?.link?.label, "Install Matching ger");
});

test("package versions compare as releases; a dev or local build is not compared", () => {
  assert.equal(packageSkew("0.1.0", "0.1.0"), "same");
  assert.equal(packageSkew("0.1.0", "0.2.0"), "ger-older");
  assert.equal(packageSkew("0.10.0", "0.9.3"), "ger-newer", "numbers, not text");
  assert.equal(packageSkew("1.0.0", "0.9.9"), "ger-newer");
  assert.equal(packageSkew("0.2.0.dev3", "0.1.0"), "unknown", "PEP 440 dev");
  assert.equal(packageSkew("0.2.0+g1234", "0.1.0"), "unknown", "local version");
  assert.equal(packageSkew("0.1.0", "0.2.0-dev"), "unknown", "extension pre-release");
});
