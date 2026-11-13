"""Ordered, content-driven finders that turn a CI log into **CI findings**.

Pipeline output varies within one log, so every finder runs over the whole text, in order:
test runners, then one-line located diagnostics (linters, compilers, the linker), then a
generic fallback. A line one finder claims is skipped by the later ones, so precise finders
win and nothing is counted twice.

The finders are grouped by the shape of what they read, one module per shape:
:mod:`~gerrit_workflow_tools.core.ci_findings_tests` and
:mod:`~gerrit_workflow_tools.core.ci_findings_located`; the generic fallback lives here.

Reusable by custom **CI content providers**: :func:`find_ci_findings` takes plain text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_findings_located import find_compilers, find_python_linters
from gerrit_workflow_tools.core.ci_findings_tests import find_pytest
from gerrit_workflow_tools.core.ci_log import Finder, clean_log_lines


# --- generic fallback --------------------------------------------------------------

_GENERIC = (
    re.compile(r"^(?:ERROR|Error|error|FATAL|Fatal|fatal):\s+(?P<msg>.+)$"),
    re.compile(r"^(?P<msg>make(?:\[\d+\])?: \*\*\* .*)$"),
    re.compile(r"(?P<msg>script returned exit code [1-9]\d*)"),
    re.compile(r"(?P<msg>(?:returned non-zero exit status|exited with (?:exit )?code|exit status) [1-9]\d*)"),
    re.compile(r"(?P<msg>Build step '.*' marked build as failure)"),
    re.compile(r"^Finished: (?P<msg>FAILURE|UNSTABLE|ABORTED)$"),
)


def find_generic(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``Error:`` lines, ``make: ***``, non-zero exit statuses and the build's final verdict."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        for rx in _GENERIC:
            if m := rx.search(line):
                claimed.add(i)
                findings.append(CiFinding(tool="generic", kind="error", message=m.group("msg").strip()))
                break
    return findings


FINDERS: tuple[Finder, ...] = (find_pytest, find_python_linters, find_compilers, find_generic)
"""Built-in finders, most precise first."""


def find_ci_findings(text: str, *, finders: Sequence[Finder] = FINDERS) -> list[CiFinding]:
    """Run *finders* in order over *text*; drop exact duplicates, keep first-seen order."""
    lines = clean_log_lines(text)
    claimed: set[int] = set()
    seen: set[tuple[object, ...]] = set()
    findings: list[CiFinding] = []
    for finder in finders:
        for finding in finder(lines, claimed):
            key = (finding.fingerprint, finding.line, finding.column)
            if key not in seen:
                seen.add(key)
                findings.append(finding)
    return findings
