# Spec: docu/spec/ci-content.md § Finding extractors
"""Real console logs of failed compiled-language builds.

The logs in ``ci_logs/`` are unedited Jenkins output of the ger language playgrounds. They pin what
each build tool wraps: one finding for the failure, at its place, and its echoes left out.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_findings import find_ci_findings


def _findings(name: str) -> list[CiFinding]:
    return find_ci_findings((Path(__file__).parent / "ci_logs" / name).read_text(encoding="utf-8"))


# --- C++: gcc under CMake and make ---------------------------------------------------


def test_gcc_error_is_the_only_finding_of_a_make_build() -> None:
    [finding] = _findings("cpp-cmake-make.txt")
    assert (finding.tool, finding.location) == ("cc", "src/csv.cpp:28:19")
    assert finding.message.startswith("‘parse_amount’ was not declared in this scope")


def test_make_alone_is_one_finding_the_innermost_failed_target() -> None:
    log = """\
+ make -j4
make[2]: *** [src/Makefile:149: src/csv.o] Error 1
make[2]: *** Waiting for unfinished jobs....
make[1]: *** [Makefile:164: src/all] Error 2
make: *** [Makefile:101: all] Error 2
Build step 'Execute shell' marked build as failure
Finished: FAILURE
"""
    [finding] = find_ci_findings(log)
    assert finding.message == "make[2]: *** [src/Makefile:149: src/csv.o] Error 1"


def test_make_errors_that_are_not_echoes_stay_findings() -> None:
    log = "make: *** No rule to make target 'all.o', needed by 'all'.  Stop.\nmake: *** [all] Error 2\n"
    assert [f.message for f in find_ci_findings(log)] == [
        "make: *** No rule to make target 'all.o', needed by 'all'.  Stop."
    ]


# --- Rust: cargo test ----------------------------------------------------------------


def test_cargo_test_panic_is_told_once() -> None:
    [finding] = _findings("rust-cargo-test.txt")
    assert (finding.tool, finding.location) == ("cargo-test", "crates/ledger/src/csv.rs:45:45")
    said = "called `Result::unwrap()` on an `Err` value: ParseError(\"not an amount: 'expense,800.00'\")"
    assert finding.message == f"csv::tests::round_trip_of_every_row: {said}"
    assert finding.detail is None


def test_cargo_test_panic_of_several_lines_keeps_the_rest_as_detail() -> None:
    log = """\
test tests::adds ... FAILED

failures:

---- tests::adds stdout ----

thread 'tests::adds' (34328) panicked at src/lib.rs:9:9:
assertion `left == right` failed
  left: -1
 right: 3
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
"""
    [finding] = find_ci_findings(log)
    assert finding.message == "tests::adds: assertion `left == right` failed"
    assert finding.detail == "  left: -1\n right: 3"


# --- Java: Maven, Surefire, javac ----------------------------------------------------


def test_junit_assertion_over_several_lines_has_its_line_and_whole_message() -> None:
    [junit, maven] = _findings("java-maven-surefire.txt")
    assert (junit.tool, junit.kind, junit.location) == ("junit", "failed", "CsvTest.java:13")
    assert junit.message == (
        "CsvTest.toCsvWritesOneLinePerRow: org.opentest4j.AssertionFailedError: expected: <account,kind,balance"
    )
    assert junit.detail == "cash,asset,100.50\n> but was: <account,kind,balance\ncash,ASSET,100.50\n>"
    assert maven.tool == "maven"


def test_junit_summary_row_keeps_the_lines_its_message_goes_on_with() -> None:
    log = """\
[ERROR] Failures:
[ERROR]   CsvTest.writes:13 expected: <a
b
> but was: <a
c
>
[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0
"""
    [finding] = find_ci_findings(log)
    assert (finding.location, finding.message) == ("CsvTest.java:13", "CsvTest.writes: expected: <a")
    assert finding.detail == "b\n> but was: <a\nc\n>"


def test_junit_failure_without_a_trace_does_not_swallow_the_next_test() -> None:
    log = """\
[ERROR] org.x.FooTest.a -- Time elapsed: 0.01 s <<< FAILURE!
[ERROR] org.x.FooTest.b -- Time elapsed: 0.01 s <<< FAILURE!
org.opentest4j.AssertionFailedError: boom
\tat org.x.FooTest.b(FooTest.java:9)
"""
    assert [(f.message, f.location) for f in find_ci_findings(log)] == [
        ("FooTest.a", "FooTest.java"),
        ("FooTest.b: org.opentest4j.AssertionFailedError: boom", "FooTest.java:9"),
    ]
