// CI findings as the editor's own problems (state 13 of the outside-the-happy-path run: a finding
// opened its file at its line, but nothing marked the line, Problems was empty, and the row cut
// the compiler's suggested fix and the JUnit message). The findings are those core parses out of
// the real logs in tests/ci_logs/ (tests/test_ci_findings_compiled.py): C++, Java, Rust; and a
// Python one. Nothing assumes a language: each is a path, a line and words.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CiContent, CiFinding } from "../src/api";
import { CI_SOURCE, findingDiagnostics } from "../src/ciDiagnostics";

const found = (extra: Partial<CiFinding> & Pick<CiFinding, "tool" | "message">): CiFinding => ({ kind: "error", path: null, line: null, column: null, detail: null, fingerprint: `${extra.tool}:${extra.message}`, severity: "error", ...extra });

const build = (name: string, findings: CiFinding[], extra: Partial<CiContent> = {}): CiContent => ({
  label: "Verified",
  url: `https://ci.example.org/job/${name}/console`,
  provenance: { url: `https://ci.example.org/job/${name}/console`, provider: "jenkins", build: name, fetched_at: null, truncated: false, cached: false, error: null },
  findings,
  collapse_warnings: false,
  ...extra,
});

// As core parses tests/ci_logs/cpp-cmake-make.txt, java-maven-surefire.txt and rust-cargo-test.txt.
const CPP = found({ tool: "cc", path: "src/csv.cpp", line: 28, column: 19, message: "‘parse_amount’ was not declared in this scope; did you mean ‘parse_money’?" });
const JUNIT = found({
  tool: "junit",
  path: "ledger-core/src/test/java/org/example/ledger/CsvTest.java",
  line: 13,
  message: "CsvTest.toCsvWritesOneLinePerRow: org.opentest4j.AssertionFailedError: expected: <account,kind,balance",
  detail: "cash,asset,100.50\n> but was: <account,kind,balance\ncash,ASSET,100.50\n>",
});
const MAVEN = found({ tool: "maven", message: "Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.5.2:test (default-test) on project ledger-core: There are test failures." });
const RUST = found({ tool: "cargo-test", path: "crates/ledger/src/csv.rs", line: 45, column: 45, message: "csv::tests::round_trip_of_every_row: called `Result::unwrap()` on an `Err` value: ParseError(\"not an amount: 'expense,800.00'\")" });
const PYTHON = found({ tool: "flake8", path: "calc/ops.py", line: 3, column: 1, severity: "warning", kind: "lint", message: "E302 expected 2 blank lines, found 1" });

test("a finding with a place becomes a diagnostic at its line and column, with its whole message, for any language", () => {
  const all = findingDiagnostics([build("ger-playground-cpp #7154", [CPP]), build("ger-playground-java #3219", [JUNIT, MAVEN]), build("ger-playground-rust #88", [RUST, PYTHON])], null);
  assert.deepEqual(
    all.map((d) => [d.path, d.line, d.column, d.severity, d.build]),
    [
      ["src/csv.cpp", 27, 18, "error", "ger-playground-cpp #7154"],
      ["ledger-core/src/test/java/org/example/ledger/CsvTest.java", 12, 0, "error", "ger-playground-java #3219"],
      ["crates/ledger/src/csv.rs", 44, 44, "error", "ger-playground-rust #88"],
      ["calc/ops.py", 2, 0, "warning", "ger-playground-rust #88"],
    ],
    "the Maven step has no file: it stays a row of the CI section",
  );
  // The compiler's suggested fix and the JUnit assertion whole: what the row cut (finding 16).
  assert.match(all[0].message, /did you mean ‘parse_money’\?$/);
  assert.equal(all[1].message, `${JUNIT.message}\n${JUNIT.detail}`);
  assert.equal(all[1].url, "https://ci.example.org/job/ger-playground-java #3219/console");
  assert.equal(CI_SOURCE, "ger CI");
});

test("where a finding came from is said in its message: inherited from the parent, or seen on an older patchset", () => {
  const moved = { ...CPP, line: 30, patchset_line: 28, line_changed: true };
  const [inherited] = findingDiagnostics([build("b #1", [moved], { patch_set: 3 })], new Set());
  assert.equal(inherited.line, 29, "where the line is in your commit now");
  assert.match(inherited.message, /\nCI saw it at line 28 of patchset 3, a line changed since\. It also fails on the parent: this change did not bring it in\.$/);
  const [introduced] = findingDiagnostics([build("b #1", [CPP])], new Set([CPP.fingerprint]));
  assert.equal(introduced.message, CPP.message, "introduced here: nothing more to say");
});

test("a finding a log repeats is one diagnostic", () => {
  assert.equal(findingDiagnostics([build("b #1", [CPP, CPP]), build("b #2", [])], null).length, 1);
  assert.deepEqual(findingDiagnostics([], null), []);
});
