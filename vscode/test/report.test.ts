// The report of an issue (src/report.ts): what is in it besides the anonymized views, that it is a
// snapshot whatever state the extension is in, and that the link to a new issue carries no report.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { changeVm } from "../src/changeRows";
import { ABOUT, ReportInput, issueUrl, reportText } from "../src/report";
import { readSnapshot } from "../src/snapshot";
import { stackVm } from "../src/stackRows";
import { commit, never, stack, state } from "./fixtures";

const versions = { extension: "1.2.3", editor: "1.95.0", os: "linux 6.8.0" };
const input = (extra: Partial<ReportInput> = {}): ReportInput => ({ ...versions, state: "ready", events: "live", outOfDate: false, stack: null, change: null, chains: null, ...extra });

test("a report says what it is first, then the versions, the state and the views: and nothing else", () => {
  const report = JSON.parse(reportText(input({ stack: stackVm(stack([commit("One")]), undefined, never), change: changeVm(state()), chains: [] })));
  assert.deepEqual(Object.keys(report), ["about", "format", "name", "extension", "vscode", "os", "state", "events", "outOfDate", "chains", "stack", "change"]);
  assert.deepEqual(
    { ...report, stack: null, change: null },
    { about: ABOUT, format: 1, name: "Report", extension: "1.2.3", vscode: "1.95.0", os: "linux 6.8.0", state: "ready", events: "live", outOfDate: false, chains: [], stack: null, change: null },
  );
  assert.equal(report.stack.rows.length, 1);
  assert.notEqual(report.stack.rows[0].summary, "One");
});

test("a report of an extension that did not start is a snapshot without views", () => {
  const text = reportText(input({ state: "no-repo", events: null }));
  assert.deepEqual(readSnapshot(text), { format: 1, name: "Report", stack: null, change: null });
  assert.equal(JSON.parse(text).state, "no-repo");
});

test("the link to a new issue has the label and the versions, and says to paste the report: it never carries one", () => {
  const url = new URL(issueUrl("https://github.com/togrue/gerrit-workflow-tools.git/", versions));
  assert.equal(`${url.origin}${url.pathname}`, "https://github.com/togrue/gerrit-workflow-tools/issues/new");
  assert.deepEqual([...url.searchParams.keys()], ["labels", "body"]);
  assert.equal(url.searchParams.get("labels"), "vscode");
  const body = url.searchParams.get("body") ?? "";
  assert.match(body, /ger extension 1\.2\.3 · VS Code 1\.95\.0 · linux 6\.8\.0$/);
  assert.match(body, /PASTE THE REPORT HERE: it is on your clipboard/);
  // The function is not even given a report: what it takes is the repository and the versions.
  assert.equal(issueUrl.length, 2);
});

test("the repository a report goes to is the one package.json names", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "package.json"), "utf8"));
  assert.match(issueUrl(manifest.repository.url, versions), /^https:\/\/github\.com\/togrue\/gerrit-workflow-tools\/issues\/new\?/);
});
