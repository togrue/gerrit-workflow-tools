"""Ordered, content-driven finders that turn a CI log into **CI findings**.

Pipeline output varies within one log, so every finder runs over the whole text, in order.
A line one finder claims is skipped by the later ones, so precise finders win and nothing
is counted twice.

The finders are grouped by the shape of what they read, one module per shape, most precise
first:

1. test runners — a failed test, located by its failure section
   (:mod:`~gerrit_workflow_tools.core.ci_findings_tests`,
   :mod:`~gerrit_workflow_tools.core.ci_findings_tests_jvm`);
2. blocks — a header line and its place on a line nearby
   (:mod:`~gerrit_workflow_tools.core.ci_findings_blocks`);
3. one-line located diagnostics — ``path:line: severity: msg``
   (:mod:`~gerrit_workflow_tools.core.ci_findings_located`);
4. build verdicts — a build driver saying *that* a step failed, and the generic fallback
   (here).

Reusable by custom **CI content providers**: :func:`find_ci_findings` takes plain text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_findings_blocks import (
    find_arrow_blocks,
    find_cmake,
    find_eslint,
    find_npm,
    find_tracebacks,
)
from gerrit_workflow_tools.core.ci_findings_located import find_located
from gerrit_workflow_tools.core.ci_findings_tests import (
    find_cargo_test,
    find_ctest,
    find_gtest,
    find_node_test,
    find_pytest,
)
from gerrit_workflow_tools.core.ci_findings_tests_jvm import (
    find_gradle_test,
    find_jest,
    find_scalatest,
    find_surefire,
)
from gerrit_workflow_tools.core.ci_log import Finder, clean_log_lines, workspace_relative, workspace_roots


# --- build verdicts and the generic fallback ---------------------------------------

_VERDICTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ninja", re.compile(r"^(?P<msg>FAILED: \S.*?)\s*$")),
    ("cmake", re.compile(r"^-- (?P<msg>Configuring incomplete, errors occurred!)")),
    ("cargo", re.compile(r"^error: (?P<msg>could not compile `.+)$")),
    ("maven", re.compile(r"^\[ERROR\] (?P<msg>Failed to execute goal .+?)(?: -> \[Help \d+\])?$")),
    ("gradle", re.compile(r"^> (?P<msg>Task \S+ FAILED)$")),
    ("sbt", re.compile(r"^\[error\] (?P<msg>\([^)]+\) .+)$")),
    ("generic", re.compile(r"^(?:ERROR|Error|error|FATAL|Fatal|fatal):\s+(?P<msg>.+)$")),
    ("generic", re.compile(r"^(?P<msg>make(?:\[\d+\])?: \*\*\* .*)$")),
    ("generic", re.compile(r"(?P<msg>script returned exit code [1-9]\d*)")),
    (
        "generic",
        re.compile(r"(?P<msg>(?:returned non-zero exit status|exited with (?:exit )?code|exit status) [1-9]\d*)"),
    ),
    ("generic", re.compile(r"(?P<msg>Build step '.*' marked build as failure)")),
    ("generic", re.compile(r"^Finished: (?P<msg>FAILURE|UNSTABLE|ABORTED)$")),
)
_RESTATED = re.compile(
    r"^(?:ninja: build stopped: .*|\[INFO\] BUILD FAILURE"
    r"|FAILURE: Build failed with an exception\.|BUILD FAILED in .*)$"
)
"""A second line for a verdict already found: claimed, so the fallback does not repeat it."""


def find_generic(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """A build driver's verdict (ninja, cmake, cargo, maven, gradle, sbt, make) and the generic fallback.

    ``Error:`` lines, non-zero exit statuses and the build's final verdict belong to the fallback.
    """
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if _RESTATED.match(line):
            claimed.add(i)
            continue
        for tool, rx in _VERDICTS:
            if m := rx.search(line):
                claimed.add(i)
                findings.append(CiFinding(tool=tool, kind="error", message=m.group("msg").strip()))
                break
    return findings


FINDERS: tuple[Finder, ...] = (
    find_pytest,
    find_cargo_test,
    find_gtest,
    find_ctest,
    find_node_test,
    find_surefire,
    find_gradle_test,
    find_scalatest,
    find_jest,
    find_arrow_blocks,
    find_tracebacks,
    find_cmake,
    find_eslint,
    find_npm,
    find_located,
    find_generic,
)
"""Built-in finders, most precise first."""


def find_ci_findings(text: str, *, finders: Sequence[Finder] = FINDERS) -> list[CiFinding]:
    """Run *finders* in order over *text*; drop exact duplicates, keep first-seen order.

    A path under a workspace directory the log names is made relative to it, so the same
    file reads the same from any build agent.
    """
    lines = clean_log_lines(text)
    roots = workspace_roots(lines)
    claimed: set[int] = set()
    seen: set[tuple[object, ...]] = set()
    findings: list[CiFinding] = []
    for finder in finders:
        for found in finder(lines, claimed):
            path = workspace_relative(found.path, roots) if found.path else found.path
            finding = found if path == found.path else replace(found, path=path, fingerprint="")
            key = (finding.fingerprint, finding.line, finding.column)
            if key not in seen:
                seen.add(key)
                findings.append(finding)
    return findings
