# Spec: docu/spec/ci-content.md § Finding extractors
"""One log per ecosystem through the built-in finders: every tool is found once, with its place.

The C/C++, Rust, Python, npm, tsc and ``node --test`` logs are real tool output, trimmed. The
JVM, eslint, Jest and GoogleTest logs follow those tools' formats as published; the section at
the end pins shapes that only showed up in real build logs.
"""

from __future__ import annotations

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_findings import find_ci_findings
from gerrit_workflow_tools.core.ci_log import clean_log_lines


def _rows(log: str) -> list[tuple[str, str, str, str]]:
    return [(f.tool, f.severity, f.location, f.message) for f in find_ci_findings(log)]


def _one(log: str, tool: str) -> CiFinding:
    [finding] = [f for f in find_ci_findings(log) if f.tool == tool]
    return finding


# --- Python ------------------------------------------------------------------------

RUFF_FULL = """\
F401 [*] `os` imported but unused
 --> pkg/a.py:1:8
  |
1 | import os
  |        ^^
2 | import asyncio
  |
help: Remove unused import: `os`

D100 Missing docstring in public module
--> pkg/a.py:1:1

invalid-syntax: Expected a parameter or the end of the parameter list
 --> pkg/bad.py:1:7
  |
1 | def f(:
  |       ^
  |

Found 3 errors.
"""


def test_ruff_default_output_is_a_block_with_an_arrow_line() -> None:
    assert _rows(RUFF_FULL) == [
        ("ruff", "error", "pkg/a.py:1:8", "F401 `os` imported but unused"),
        ("ruff", "error", "pkg/a.py:1:1", "D100 Missing docstring in public module"),
        ("ruff", "error", "pkg/bad.py:1:7", "invalid-syntax: Expected a parameter or the end of the parameter list"),
    ]
    assert _one(RUFF_FULL.split("D100")[0], "ruff").detail == "import os\n       ^^\nimport asyncio"


def test_ruff_block_and_concise_output_are_the_same_finding() -> None:
    concise = find_ci_findings("pkg/a.py:1:8: F401 [*] `os` imported but unused\n")
    assert concise[0].fingerprint == find_ci_findings(RUFF_FULL)[0].fingerprint
    [long_code] = find_ci_findings("pkg/a.py:5:5: ASYNC230 Async functions should not open files\n")
    assert (long_code.tool, long_code.kind) == ("ruff", "lint")


def test_pylint_ranks_conventions_and_warnings_below_errors() -> None:
    log = """\
************* Module pkg.mod
pkg/mod.py:45:0: R0903: Too few public methods (1/2) (too-few-public-methods)
pkg/mod.py:12:4: W0611: Unused import os (unused-import)
pkg/mod.py:20:8: E0602: Undefined variable 'nope' (undefined-variable)
"""
    assert [(f.tool, f.kind, f.severity, f.location) for f in find_ci_findings(log)] == [
        ("pylint", "refactor", "warning", "pkg/mod.py:45:0"),
        ("pylint", "warning", "warning", "pkg/mod.py:12:4"),
        ("pylint", "error", "error", "pkg/mod.py:20:8"),
    ]


def test_mypy_end_positions_and_pretty_wrapping() -> None:
    log = """\
pkg/m.py:2:12:2:14: error: Incompatible return value type (got "str", expected "int")  [return-value]
pkg/n.py:2: error:
Incompatible return value type (got "str", expected "int")  [return-value]
        return "a"
               ^~~
"""
    first, second = find_ci_findings(log)
    assert (first.location, second.location) == ("pkg/m.py:2:12", "pkg/n.py:2")
    assert first.message == second.message


def test_python_traceback_is_located_in_project_code() -> None:
    log = """\
+ python tools/gen.py
Traceback (most recent call last):
  File "/ws/tools/gen.py", line 3, in <module>
    h()
    ~^^
  File "/ws/tools/gen.py", line 2, in h
    raise json.loads("x")
  File "/usr/lib/python3.12/json/__init__.py", line 346, in loads
    return _default_decoder.decode(s)
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
"""
    assert _rows(log) == [
        (
            "python",
            "error",
            "/ws/tools/gen.py:2",
            "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
        )
    ]


def test_pytest_parametrized_id_with_a_space() -> None:
    [finding] = find_ci_findings("FAILED tests/test_a.py::test_x[a b] - assert 1 == 2\n")
    assert finding.message == "tests/test_a.py::test_x[a b]: assert 1 == 2"


# --- C / C++ / CMake / Ninja -------------------------------------------------------

NINJA_GCC = """\
[1/2] Building C object CMakeFiles/app.dir/main.c.obj
FAILED: CMakeFiles/app.dir/main.c.obj
/usr/bin/gcc -MD -MT CMakeFiles/app.dir/main.c.obj -o CMakeFiles/app.dir/main.c.obj -c /ws/cpp/main.c
/ws/cpp/main.c: In function 'main':
/ws/cpp/main.c:2:45: error: 'undefined_var' undeclared (first use in this function)
    2 | int main(void){ int unused; return foo(1) + undefined_var; }
      |                                             ^~~~~~~~~~~~~
/ws/cpp/main.c:2:45: note: each undeclared identifier is reported only once for each function it appears in
/ws/cpp/main.c:2:21: warning: unused variable 'unused' [-Wunused-variable]
cc1: all warnings being treated as errors
ninja: build stopped: subcommand failed.
"""


def test_gcc_under_ninja_names_the_error_then_the_failed_target() -> None:
    assert _rows(NINJA_GCC) == [
        ("cc", "error", "/ws/cpp/main.c:2:45", "'undefined_var' undeclared (first use in this function)"),
        ("cc", "warning", "/ws/cpp/main.c:2:21", "unused variable 'unused' [-Wunused-variable]"),
        ("ninja", "error", "", "FAILED: CMakeFiles/app.dir/main.c.obj"),
    ]


def test_linker_error_through_a_prefixed_ld_is_located() -> None:
    log = """\
FAILED: app.exe
D:/msys64/mingw64/bin/ld.exe: CMakeFiles/app.dir/main.c.obj:main.c:(.text+0x13): undefined reference to `foo'
collect2.exe: error: ld returned 1 exit status
clang: error: linker command failed with exit code 1 (use -v to see invocation)
"""
    assert _rows(log) == [
        ("ld", "error", "main.c", "undefined reference to `foo'"),
        ("ld", "error", "", "ld returned 1 exit status"),
        ("cc", "error", "", "linker command failed with exit code 1 (use -v to see invocation)"),
        ("ninja", "error", "", "FAILED: app.exe"),
    ]


def test_cmake_error_block() -> None:
    log = """\
-- Detecting C compile features - done
CMake Error at CMakeLists.txt:3 (find_package):
  By not providing "FindNoSuchThing.cmake" in CMAKE_MODULE_PATH this project
  has asked CMake to find a package configuration file provided by
  "NoSuchThing", but CMake did not find one.

  Could not find a package configuration file provided by "NoSuchThing" with
  any of the following names:

    NoSuchThingConfig.cmake


-- Configuring incomplete, errors occurred!
"""
    located, verdict = find_ci_findings(log)
    assert (located.tool, located.location) == ("cmake", "CMakeLists.txt:3")
    assert located.message.startswith('By not providing "FindNoSuchThing.cmake"')
    assert located.message.endswith("but CMake did not find one.")
    assert (verdict.tool, verdict.message) == ("cmake", "Configuring incomplete, errors occurred!")


def test_googletest_and_ctest() -> None:
    log = """\
[ RUN      ] CalcTest.Adds
/ws/tests/calc_test.cc:12: Failure
Expected equality of these values:
  add(1, 1)
    Which is: 2
  3
[  FAILED  ] CalcTest.Adds (0 ms)
[  PASSED  ] 3 tests.
[  FAILED  ] 1 test, listed below:
[  FAILED  ] CalcTest.Adds

 1 FAILED TEST
The following tests FAILED:
\t  2 - calc_test (Failed)
Errors while running CTest
"""
    assert _rows(log) == [
        ("gtest", "error", "/ws/tests/calc_test.cc:12", "CalcTest.Adds: Expected equality of these values:"),
        ("ctest", "error", "", "calc_test (Failed)"),
    ]


# --- Rust --------------------------------------------------------------------------

CARGO_BUILD = """\
   Compiling p v0.1.0 (/ws/rs)
error[E0308]: mismatched types
 --> src/lib.rs:2:23
  |
2 | pub fn bad() -> i32 { "x" }
  |                 ---   ^^^ expected `i32`, found `&str`
  |                 |
  |                 expected `i32` because of return type

warning: unused variable: `unused`
 --> src/lib.rs:1:41
  |
1 | pub fn add(a: i32, b: i32) -> i32 { let unused = 1; a + b }
  |                                         ^^^^^^ help: if this is intentional, prefix it with an underscore: `_unused`
  |
  = note: `#[warn(unused_variables)]` (part of `#[warn(unused)]`) on by default

For more information about this error, try `rustc --explain E0308`.
warning: `p` (lib) generated 1 warning
error: could not compile `p` (lib) due to 1 previous error; 1 warning emitted
"""


def test_rustc_blocks_and_the_cargo_verdict() -> None:
    assert _rows(CARGO_BUILD) == [
        ("rustc", "error", "src/lib.rs:2:23", "E0308 mismatched types"),
        ("rustc", "warning", "src/lib.rs:1:41", "unused variable: `unused`"),
        ("cargo", "error", "", "could not compile `p` (lib) due to 1 previous error; 1 warning emitted"),
    ]


def test_clippy_is_told_from_rustc_by_its_lint_name() -> None:
    log = """\
error: unneeded `return` statement
 --> src/lib.rs:1:40
  |
1 | pub fn f(x: i32) -> bool { if x == 1 { return true; } else { return false; } }
  |                                        ^^^^^^^^^^^
  |
  = note: `-D clippy::needless-return` implied by `-D warnings`
"""
    assert _rows(log) == [("clippy", "error", "src/lib.rs:1:40", "unneeded `return` statement")]


def test_cargo_test_failures_are_located_at_the_panic() -> None:
    log = """\
running 2 tests
test tests::adds ... FAILED
test tests::boom ... FAILED

failures:

---- tests::adds stdout ----

thread 'tests::adds' (34328) panicked at src/lib.rs:9:9:
assertion `left == right` failed
  left: -1
 right: 3
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

---- tests::boom stdout ----

thread 'tests::boom' (26320) panicked at src/lib.rs:12:52:
index out of bounds: the len is 0 but the index is 3


failures:
    tests::adds
    tests::boom

test result: FAILED. 0 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
"""
    assert _rows(log) == [
        ("cargo-test", "error", "src/lib.rs:9:9", "tests::adds: assertion `left == right` failed"),
        (
            "cargo-test",
            "error",
            "src/lib.rs:12:52",
            "tests::boom: index out of bounds: the len is 0 but the index is 3",
        ),
    ]


def test_rustfmt_is_one_finding_per_file() -> None:
    log = "Diff in /ws/rs/src/lib.rs:9:\n     }\nDiff in /ws/rs/src/lib.rs:20:\n     }\n"
    assert _rows(log) == [("rustfmt", "error", "/ws/rs/src/lib.rs", "would reformat")]


# --- JavaScript --------------------------------------------------------------------


def test_tsc_plain_and_pretty() -> None:
    log = """\
src/a.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'.
src/b.tsx:2:44 - error TS2339: Property 'nope' does not exist on type 'string'.
"""
    assert _rows(log) == [
        ("tsc", "error", "src/a.ts:1:7", "TS2322 Type 'string' is not assignable to type 'number'."),
        ("tsc", "error", "src/b.tsx:2:44", "TS2339 Property 'nope' does not exist on type 'string'."),
    ]


def test_eslint_rows_belong_to_the_file_named_above_them() -> None:
    log = """\
/ws/src/a.js
  12:5   error    'x' is defined but never used  no-unused-vars
  14:1   warning  Unexpected console statement   no-console

/ws/src/b.js
  3:10  error  Missing semicolon  semi

✖ 3 problems (2 errors, 1 warning)
"""
    assert _rows(log) == [
        ("eslint", "error", "/ws/src/a.js:12:5", "'x' is defined but never used (no-unused-vars)"),
        ("eslint", "warning", "/ws/src/a.js:14:1", "Unexpected console statement (no-console)"),
        ("eslint", "error", "/ws/src/b.js:3:10", "Missing semicolon (semi)"),
    ]


def test_npm_error_report_is_one_finding() -> None:
    new = """\
npm error Missing script: "nope"
npm error
npm error To see a list of scripts, run:
npm error   npm run
npm error A complete log of this run can be found in: /home/ci/.npm/_logs/2026-11-13T01_36_28_242Z-debug-0.log
"""
    old = """\
npm ERR! code ELIFECYCLE
npm ERR! errno 1
npm ERR! p@1.0.0 test: `jest`
npm ERR! Exit status 1
"""
    assert _rows(new) == [("npm", "error", "", 'Missing script: "nope"')]
    assert _rows(old) == [("npm", "error", "", "ELIFECYCLE: p@1.0.0 test: `jest`")]


def test_node_test_runner() -> None:
    log = """\
✖ adds (1.032ms)
✔ ok (0.1225ms)

✖ failing tests:

test at a.test.js:2:1
✖ adds (1.032ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:

  2 !== 3

      at TestContext.<anonymous> (/ws/js/a.test.js:2:29)
"""
    finding = _one(log, "node-test")
    assert finding.location == "a.test.js:2:1"
    assert finding.message == "adds: AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:"
    assert len(find_ci_findings(log)) == 1


def test_jest_failure_is_located_at_the_first_project_frame() -> None:
    log = """\
FAIL src/calc.test.js
  ● calc \u203a adds

    expect(received).toBe(expected) // Object.is equality

    Expected: 3
    Received: 2

      at Object.<anonymous> (node_modules/jest-circus/build/run.js:10:3)
      at Object.<anonymous> (src/calc.test.js:5:23)

PASS src/other.test.js
"""
    assert _rows(log) == [
        (
            "jest",
            "error",
            "src/calc.test.js:5:23",
            "calc \u203a adds: expect(received).toBe(expected) // Object.is equality",
        )
    ]


# --- Java / Scala ------------------------------------------------------------------


def test_javac_plain_and_through_maven() -> None:
    log = """\
src/main/java/com/x/Foo.java:12: error: cannot find symbol
[ERROR] /ws/src/main/java/com/x/Bar.java:[7,5] incompatible types: String cannot be converted to int
[WARNING] /ws/src/main/java/com/x/Bar.java:[9,1] deprecated API
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.11.0:compile (default-compile) \
on project x: Compilation failure -> [Help 1]
[INFO] BUILD FAILURE
"""
    rows = _rows(log)
    assert rows[:3] == [
        ("javac", "error", "src/main/java/com/x/Foo.java:12", "cannot find symbol"),
        (
            "javac",
            "error",
            "/ws/src/main/java/com/x/Bar.java:7:5",
            "incompatible types: String cannot be converted to int",
        ),
        ("javac", "warning", "/ws/src/main/java/com/x/Bar.java:9:1", "deprecated API"),
    ]
    assert [(tool, message[:22]) for tool, _, _, message in rows[3:]] == [("maven", "Failed to execute goal")]


def test_surefire_and_gradle_test_failures() -> None:
    log = """\
[ERROR] Failures:
[ERROR]   FooTest.testBar:42 expected: <1> but was: <2>
[ERROR] Errors:
[ERROR]   FooTest.testNull:50 » NullPointer
[ERROR] Tests run: 3, Failures: 1, Errors: 1, Skipped: 0
com.x.CalcTest > adds() FAILED
    org.opentest4j.AssertionFailedError at CalcTest.java:17
> Task :test FAILED
"""
    assert _rows(log) == [
        ("junit", "error", "FooTest.java:42", "FooTest.testBar: expected: <1> but was: <2>"),
        ("junit", "error", "FooTest.java:50", "FooTest.testNull: » NullPointer"),
        ("junit", "error", "CalcTest.java:17", "com.x.CalcTest.adds(): org.opentest4j.AssertionFailedError"),
        ("gradle", "error", "", "Task :test FAILED"),
    ]


def test_scalac_and_scalatest_through_sbt() -> None:
    log = """\
[error] /ws/src/main/scala/Foo.scala:12:5: type mismatch;
[error]  found   : String("a")
[warn] /ws/src/main/scala/Foo.scala:3:1: imported `x` is unused
[error] -- [E007] Type Mismatch Error: /ws/src/main/scala/Bar.scala:3:15 -----------
[error] 3 |  val x: Int = "a"
[error]   |               ^^^
[error]   |               Found:    ("a" : String)
[error]   |               Required: Int
[info] - should add *** FAILED ***
[info]   2 did not equal 3 (CalcSpec.scala:9)
[error] Failed tests:
[error] \tcom.x.CalcSpec
[error] (Compile / compileIncremental) Compilation failed
"""
    assert _rows(log) == [
        ("scalatest", "error", "CalcSpec.scala:9", "should add: 2 did not equal 3"),
        ("scalac", "error", "/ws/src/main/scala/Foo.scala:12:5", "type mismatch;"),
        ("scalac", "warning", "/ws/src/main/scala/Foo.scala:3:1", "imported `x` is unused"),
        ("scalac", "error", "/ws/src/main/scala/Bar.scala:3:15", "E007 Type Mismatch Error"),
        ("sbt", "error", "", "(Compile / compileIncremental) Compilation failed"),
    ]
    assert "Required: Int" in (find_ci_findings(log)[3].detail or "")


# --- what a CI system puts in front of a line --------------------------------------


def test_parallel_stage_tags_and_buildkit_prefixes_are_stripped() -> None:
    log = """\
[Pipeline] { (Branch: unit tests)
[unit tests] src/a.py:1:1: E501 Line too long
[unit tests] [2026-11-09T10:00:00.123Z] src/b.py:2:1: E501 Line too long
#12 3.456 src/main.c:10:5: error: implicit declaration of function 'foo'
"""
    assert clean_log_lines(log)[1:] == [
        "src/a.py:1:1: E501 Line too long",
        "src/b.py:2:1: E501 Line too long",
        "src/main.c:10:5: error: implicit declaration of function 'foo'",
    ]
    assert [f.tool for f in find_ci_findings(log)] == ["ruff", "ruff", "cc"]


def test_paths_under_the_announced_workspace_become_relative() -> None:
    log = """\
Building remotely on agent-7 (linux) in workspace /home/jenkins/workspace/widget@2
/home/jenkins/workspace/widget@2/src/main.c:10:5: error: implicit declaration of function 'foo'
/usr/include/stdio.h:3:1: warning: something
"""
    other = log.replace("widget@2", "widget")
    assert [f.path for f in find_ci_findings(log)] == ["src/main.c", "/usr/include/stdio.h"]
    assert find_ci_findings(log)[0].fingerprint == find_ci_findings(other)[0].fingerprint


def test_windows_workspace_and_backslashes() -> None:
    log = "Running on win-1 in C:\\jenkins\\ws\\widget\nc:\\jenkins\\ws\\widget\\src\\lib.rs:3:1: error: x\n"
    lines = clean_log_lines(log)
    assert lines[0].startswith("Running on")
    [finding] = find_ci_findings("Running on win-1 in C:\\ws\\widget\nDiff in \\\\?\\C:\\ws\\widget\\src\\lib.rs:9:\n")
    assert finding.path == "src/lib.rs"


# --- formats seen in real build logs -----------------------------------------------
# Shapes taken from the public sample logs of the Jenkins warnings parser
# (jenkinsci/analysis-model test resources) and from published Gradle and ScalaTest runs.


def test_maven_paths_with_spaces_and_text_before_the_level_tag() -> None:
    log = """\
[WARNING] /home/hudson/data/jobs/Hudson main/workspace/src/test/java/a/StreamTest.java:[3,54] sun.misc is internal
[ERROR] \\Jenkins\\workspace\\VIGO - Compile\\src\\at\\Details.java:[350,48] cannot find symbol
[ParallelStep] [WARNING] C:\\Build\\workspace\\ui\\src\\main\\java\\Dialog.java:[12,39] [deprecation] x is deprecated
08:52:34[WARNING] /ws/src/main/java/my/Filing.java:[86,76] Test6
"""
    assert _rows(log) == [
        (
            "javac",
            "warning",
            "/home/hudson/data/jobs/Hudson main/workspace/src/test/java/a/StreamTest.java:3:54",
            "sun.misc is internal",
        ),
        ("javac", "error", "/Jenkins/workspace/VIGO - Compile/src/at/Details.java:350:48", "cannot find symbol"),
        ("javac", "warning", "C:/Build/workspace/ui/src/main/java/Dialog.java:12:39", "[deprecation] x is deprecated"),
        ("javac", "warning", "/ws/src/main/java/my/Filing.java:86:76", "Test6"),
    ]


def test_javac_and_scalac_lines_behind_a_wrapper_tag_keep_the_compilers_own_severity() -> None:
    log = """\
[java] /var/lib/jenkins/workspace/webhooks/src/main/java/File.java:59: error: ';' expected
[WARNING] /ws/src/main/java/edu/Ensure.java:57: warning: @param "value" has already been specified
[ERROR] /ws/yet/another/path/SomeFile.scala:59: warning: method error in object Predef is deprecated
"""
    assert _rows(log) == [
        ("javac", "error", "/var/lib/jenkins/workspace/webhooks/src/main/java/File.java:59", "';' expected"),
        ("javac", "warning", "/ws/src/main/java/edu/Ensure.java:57", '@param "value" has already been specified'),
        ("scalac", "warning", "/ws/yet/another/path/SomeFile.scala:59", "method error in object Predef is deprecated"),
    ]


def test_sbt_scalac_without_a_column() -> None:
    log = """\
[warn] /ws/path/SomeFile.scala:111: method stop in class Thread is deprecated: see corresponding Javadoc
[error] /ws/another/path/SomeFile.scala:9: ';' expected but identifier found.
[error] \tat com.x.Foo.bar(Foo.scala:12)
[error] Total time: 127 s, completed Oct 7, 2019 9:11:30 AM
"""
    assert _rows(log) == [
        (
            "scalac",
            "warning",
            "/ws/path/SomeFile.scala:111",
            "method stop in class Thread is deprecated: see corresponding Javadoc",
        ),
        ("scalac", "error", "/ws/another/path/SomeFile.scala:9", "';' expected but identifier found."),
    ]


def test_bracketed_codes_are_pylint_parseable_or_flake8() -> None:
    log = """\
trunk/src/python/tv.py:28: [C0103, Show.__init__] Invalid name "seasonCount"
trunk/src/python/tv.py:39: [E0213, Episode.__init__] Method should have "self" as first argument
trunk/src/python/tv.py:1: [F0001(fatal), ] No module named nope
myproject/__init__.py:7: [F401] 'db' imported but unused
"""
    assert [(f.tool, f.kind, f.severity, f.location) for f in find_ci_findings(log)] == [
        ("pylint", "convention", "warning", "trunk/src/python/tv.py:28"),
        ("pylint", "error", "error", "trunk/src/python/tv.py:39"),
        ("pylint", "fatal", "error", "trunk/src/python/tv.py:1"),
        ("flake8", "lint", "error", "myproject/__init__.py:7"),
    ]


def test_gradle_names_a_kotlin_test_with_spaces() -> None:
    log = """\
> Task :test FAILED

com.example.CalculatorSpec > adds 2 to 2 FAILED
    org.opentest4j.AssertionFailedError at CalculatorSpec.kt:11

3 tests completed, 1 failed

FAILURE: Build failed with an exception.
BUILD FAILED in 2s
"""
    assert _rows(log) == [
        (
            "junit",
            "error",
            "CalculatorSpec.kt:11",
            "com.example.CalculatorSpec.adds 2 to 2: org.opentest4j.AssertionFailedError",
        ),
        ("gradle", "error", "", "Task :test FAILED"),
    ]


def test_scalatest_failure_with_a_duration() -> None:
    log = """\
[info] - should send the markup unindented out the door *** FAILED *** (52 milliseconds)
[info]   "[]" did not equal "[one]" (OldDocSpec.scala:64)
"""
    assert _rows(log) == [
        (
            "scalatest",
            "error",
            "OldDocSpec.scala:64",
            'should send the markup unindented out the door: "[]" did not equal "[one]"',
        )
    ]


def test_googletest_failure_outside_a_run_and_looser_tag_spacing() -> None:
    log = """\
path/to/foo_test.cc:11: Failure
Value of: Bar(n)
Expected: 1
  Actual: 2
[ RUN      ] BarTest.ReturnsTrueOnSuccess
[   FAILED ] BarTest.ReturnsTrueOnSuccess
"""
    assert _rows(log) == [
        ("gtest", "error", "path/to/foo_test.cc:11", "Value of: Bar(n)"),
        ("gtest", "error", "", "BarTest.ReturnsTrueOnSuccess"),
    ]


def test_eslint_file_path_with_a_space() -> None:
    log = """\
/var/lib/jenkins/workspace/eslint Release/eslint/fullOfProblems.js
  1:10  error    'addOne' is defined but never used  no-unused-vars
  3:20  warning  Missing semicolon                   semi
"""
    assert [(f.severity, f.location) for f in find_ci_findings(log)] == [
        ("error", "/var/lib/jenkins/workspace/eslint Release/eslint/fullOfProblems.js:1:10"),
        ("warning", "/var/lib/jenkins/workspace/eslint Release/eslint/fullOfProblems.js:3:20"),
    ]


def test_surefire_failed_test_is_located_by_its_stack_trace_and_listed_once() -> None:
    log = """\
[ERROR] Tests run: 2, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.016 s <<< FAILURE! -- in org.x.FailTest
[ERROR] org.x.FailTest.adds -- Time elapsed: 0.011 s <<< FAILURE!
org.opentest4j.AssertionFailedError: expected: <3> but was: <2>
\tat org.junit.jupiter.api.AssertionUtils.fail(AssertionUtils.java:37)
\tat org.x.FailTest.adds(FailTest.java:11)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)
Error:  testResponse(org.x.handlers.ResponseIT)  Time elapsed: 5.01 s  <<< ERROR!
java.lang.IllegalStateException: no server
\tat org.x.handlers.ResponseIT.setUp(ResponseIT.java:86)
[ERROR] Failures:
[ERROR]   FailTest.adds:11 expected: <3> but was: <2>
[ERROR] Tests run: 3, Failures: 1, Errors: 1, Skipped: 0
"""
    assert _rows(log) == [
        (
            "junit",
            "error",
            "FailTest.java:11",
            "FailTest.adds: org.opentest4j.AssertionFailedError: expected: <3> but was: <2>",
        ),
        ("junit", "error", "ResponseIT.java:86", "ResponseIT.testResponse: java.lang.IllegalStateException: no server"),
    ]


def test_scala3_error_printed_by_the_compiler_itself() -> None:
    log = """\
-- [E007] Type Mismatch Error: example.scala:1:16 ------------------------------
1 |val x: String = 42
  |                ^^
  |                Found:    (42 : Int)
  |                Required: String
  |-----------------------------------------------------------------------------
  | Explanation (enabled by `-explain`)
"""
    [finding] = find_ci_findings(log)
    assert (finding.tool, finding.severity, finding.location) == ("scalac", "error", "example.scala:1:16")
    assert (finding.detail or "").splitlines()[-1].strip() == "|                Required: String"
