# Spec: docu/spec/ci-content.md § Finding extractors
"""The built-in ordered finders: one tool tag per finding, precise before generic, no double counts."""

from __future__ import annotations

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_findings import clean_log_lines, find_ci_findings


PYTEST_LONG = """\
+ python3 -m pytest
============================= test session starts ==============================
collected 3 items

tests/test_calc.py .F.                                                   [100%]

=================================== FAILURES ===================================
_________________________________ test_divide __________________________________

    def test_divide():
>       assert divide(1, 0) == 0

tests/test_calc.py:9:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def divide(a, b):
>       return a / b
E       ZeroDivisionError: division by zero

demo/calc.py:6: ZeroDivisionError
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_divide - ZeroDivisionError: division by zero
========================= 1 failed, 2 passed in 0.03s ==========================
Build step 'Execute shell' marked build as failure
Finished: FAILURE
"""


def _by_tool(findings: list[CiFinding]) -> dict[str, list[CiFinding]]:
    out: dict[str, list[CiFinding]] = {}
    for f in findings:
        out.setdefault(f.tool, []).append(f)
    return out


def test_pytest_failure_is_located_at_the_raising_frame() -> None:
    findings = _by_tool(find_ci_findings(PYTEST_LONG))
    [failure] = findings["pytest"]
    assert failure.kind == "failed"
    assert failure.message == "tests/test_calc.py::test_divide: ZeroDivisionError: division by zero"
    assert (failure.path, failure.line) == ("demo/calc.py", 6)
    assert failure.detail == "ZeroDivisionError: division by zero"


def test_generic_lines_outside_pytest_still_count_but_inside_do_not() -> None:
    findings = _by_tool(find_ci_findings(PYTEST_LONG))
    messages = [f.message for f in findings["generic"]]
    assert messages == ["Build step 'Execute shell' marked build as failure", "FAILURE"]
    assert set(findings) == {"pytest", "generic"}


def test_pytest_short_traceback_uses_the_test_frame() -> None:
    log = """\
=================================== FAILURES ===================================
_______________________________ TestCalc.test_add ______________________________
tests/test_calc.py:14: in test_add
    assert add(1, 1) == 3
E   assert 2 == 3
=========================== short test summary info ============================
FAILED tests/test_calc.py::TestCalc::test_add - assert 2 == 3
"""
    [failure] = find_ci_findings(log)
    assert (failure.tool, failure.path, failure.line) == ("pytest", "tests/test_calc.py", 14)
    assert failure.detail == "assert 2 == 3"


def test_pytest_collection_error() -> None:
    log = "ERROR tests/test_broken.py - ModuleNotFoundError: No module named 'nope'\n"
    [finding] = find_ci_findings(log)
    assert (finding.tool, finding.kind, finding.path) == ("pytest", "error", "tests/test_broken.py")


def test_ruff_and_mypy() -> None:
    log = """\
src/app.py:3:8: F401 [*] `os` imported but unused
Would reformat: src/other.py
src/app.py:12: error: Incompatible return value type (got "int", expected "str")  [return-value]
src/app.py:12: note: See https://mypy.rtfd.io
Found 1 error in 1 file (checked 2 source files)
"""
    findings = find_ci_findings(log)
    assert [(f.tool, f.kind, f.location) for f in findings] == [
        ("ruff", "lint", "src/app.py:3:8"),
        ("ruff", "format", "src/other.py"),
        ("mypy", "error", "src/app.py:12"),
    ]
    assert findings[0].message == "F401 `os` imported but unused"


def test_compiler_clang_format_and_linker() -> None:
    log = """\
src/main.c:10:5: error: implicit declaration of function 'foo' [-Werror=implicit-function-declaration]
src/util.h:3:1: warning: unused function 'bar' [-Wunused-function]
src/fmt.cc:7:12: error: code should be clang-formatted [-Wclang-format-violations]
/usr/bin/ld: main.o: in function `main':
main.c:(.text+0x15): undefined reference to `foo'
collect2: error: ld returned 1 exit status
make: *** [Makefile:12: app] Error 1
"""
    findings = find_ci_findings(log)
    assert [(f.tool, f.kind, f.location) for f in findings] == [
        ("cc", "error", "src/main.c:10:5"),
        ("cc", "warning", "src/util.h:3:1"),
        ("clang-format", "format", "src/fmt.cc:7:12"),
        ("ld", "error", "main.c"),
        ("ld", "error", ""),
        ("generic", "error", ""),
    ]
    assert findings[3].message == "undefined reference to `foo'"


def test_ansi_and_jenkins_timestamps_are_stripped() -> None:
    lines = clean_log_lines("[2026-11-09T10:00:00.123Z] \x1b[31msrc/a.py:1:1: E501 Line too long\x1b[0m\r\n")
    assert lines == ["src/a.py:1:1: E501 Line too long"]
    [finding] = find_ci_findings("12:00:01 src/a.py:1:1: E501 Line too long\n")
    assert finding.tool == "ruff"


def test_fingerprint_survives_a_line_shift_and_duplicates_collapse() -> None:
    a = find_ci_findings("src/a.py:3: error: Name 'x' is not defined  [name-defined]\n")
    b = find_ci_findings("src/a.py:30: error: Name 'x' is not defined  [name-defined]\n")
    assert a[0].fingerprint == b[0].fingerprint
    assert len(find_ci_findings("Error: boom\nError: boom\n")) == 1


def test_clean_log_has_no_findings() -> None:
    assert find_ci_findings("+ make\nall good\nFinished: SUCCESS\n") == []


def test_finding_round_trips_through_its_json_shape() -> None:
    [finding] = find_ci_findings(PYTEST_LONG.split("Build step")[0])
    assert CiFinding.from_dict(finding.to_dict()) == finding
