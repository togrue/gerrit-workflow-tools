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
