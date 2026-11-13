"""Finders for test runners on the JVM and in JavaScript: the failed test, located by its report.

The same shape as :mod:`~gerrit_workflow_tools.core.ci_findings_tests`, for Surefire (Maven),
Gradle, ScalaTest (sbt) and Jest. The JVM runners name a source file without its directory;
resolving it against the commit's files is left to path resolution.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_log import DETAIL_LINES


# --- Maven Surefire ----------------------------------------------------------------

_SUREFIRE_TAG = r"(?:\[ERROR\]|Error:)?\s*"
_SUREFIRE_TEST = (
    # Surefire 3: ``com.x.FooTest.testBar -- Time elapsed: 0.01 s <<< FAILURE!``
    re.compile(rf"^{_SUREFIRE_TAG}(?P<cls>[\w.$]+)\.(?P<test>[\w$]+) -- Time elapsed: .*<<< (?P<what>FAILURE|ERROR)!$"),
    # Surefire 2: ``testBar(com.x.FooTest)  Time elapsed: 0.01 s  <<< FAILURE!``
    re.compile(
        rf"^{_SUREFIRE_TAG}(?P<test>[\w$]+)\((?P<cls>[\w.$]+)\)\s+Time elapsed: .*<<< (?P<what>FAILURE|ERROR)!$"
    ),
)
_SUREFIRE_CLASS = re.compile(r"^(?:\[\w+\]|Error:)?\s*Tests run: \d+, Failures: \d+, Errors: \d+.*$")
_SUREFIRE_HEAD = re.compile(r"^\[ERROR\] (?P<what>Failures|Errors):\s*$")
_SUREFIRE_ROW = re.compile(r"^\[ERROR\]\s{2,}(?P<cls>[\w.$]+)\.(?P<test>[\w$]+)(?::(?P<line>\d+))?(?: (?P<msg>.*))?$")
_JAVA_FRAME = re.compile(r"^\s+at (?P<cls>[\w.$]+)\.[\w$<>]+\((?P<file>[\w$]+\.\w+):(?P<line>\d+)\)$")


def _simple(cls: str) -> str:
    """``com.x.Outer$Inner`` -> ``Outer``: the class a source file is named after."""
    return cls.rsplit(".", 1)[-1].split("$")[0]


def _surefire_trace(
    lines: Sequence[str], start: int, cls: str, claimed: set[int]
) -> tuple[str, str | None, int | None]:
    """Under a failed test's line: what was thrown, and the first frame in the test's own class."""
    said = lines[start].strip() if start < len(lines) and not _JAVA_FRAME.match(lines[start]) else ""
    file: str | None = None
    lineno: int | None = None
    end = start + (1 if said else 0)
    while end < len(lines) and (frame := _JAVA_FRAME.match(lines[end])):
        if file is None and _simple(frame.group("cls")) == _simple(cls):
            file, lineno = frame.group("file"), int(frame.group("line"))
        end += 1
    claimed.update(range(start, end))
    return said, file, lineno


def find_surefire(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """A test's ``<<< FAILURE!`` line and the stack trace under it; else its row in the closing summary."""
    findings: list[CiFinding] = []
    seen: set[str] = set()
    what: str | None = None
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        failed = next((m for rx in _SUREFIRE_TEST if (m := rx.match(line))), None)
        if failed is not None:
            cls, test = failed.group("cls"), failed.group("test")
            said, file, lineno = _surefire_trace(lines, i + 1, cls, claimed)
            name = f"{_simple(cls)}.{test}"
            seen.add(name)
            findings.append(
                CiFinding(
                    tool="junit",
                    kind="failed" if failed.group("what") == "FAILURE" else "error",
                    message=f"{name}: {said}" if said else name,
                    path=file or f"{_simple(cls)}.java",
                    line=lineno,
                )
            )
        elif head := _SUREFIRE_HEAD.match(line):
            what = head.group("what")
        elif what is not None and (m := _SUREFIRE_ROW.match(line)):
            name = f"{_simple(m.group('cls'))}.{m.group('test')}"
            said = (m.group("msg") or "").strip()
            if name not in seen:
                seen.add(name)
                findings.append(
                    CiFinding(
                        tool="junit",
                        kind="failed" if what == "Failures" else "error",
                        message=f"{name}: {said}" if said else name,
                        path=f"{_simple(m.group('cls'))}.java",
                        line=int(m.group("line")) if m.group("line") else None,
                    )
                )
        elif _SUREFIRE_CLASS.match(line):
            what = None
        else:
            what = None
            continue
        claimed.add(i)
    return findings


# --- Gradle ------------------------------------------------------------------------

_GRADLE_FAILED = re.compile(r"^(?P<cls>[\w.$]+) > (?P<test>.+?) FAILED$")
_GRADLE_AT = re.compile(r"^\s+(?P<exc>[\w.$]+)(?:: (?P<msg>.*?))? at (?P<file>[\w$]+\.\w+):(?P<line>\d+)$")


def find_gradle_test(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``Class > test FAILED`` and the ``Exception at File.java:42`` line under it."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        m = _GRADLE_FAILED.match(line)
        if i in claimed or m is None:
            continue
        claimed.add(i)
        at = _GRADLE_AT.match(lines[i + 1]) if i + 1 < len(lines) else None
        if at:
            claimed.add(i + 1)
        test = f"{m.group('cls')}.{m.group('test')}"
        said = ": ".join(part for part in (at.group("exc"), at.group("msg")) if part) if at else ""
        findings.append(
            CiFinding(
                tool="junit",
                kind="failed",
                message=f"{test}: {said}" if said else test,
                path=at.group("file") if at else None,
                line=int(at.group("line")) if at else None,
            )
        )
    return findings


# --- ScalaTest through sbt ---------------------------------------------------------

_SCALATEST_FAILED = re.compile(r"^\[info\] - (?P<name>.+?) \*\*\* FAILED \*\*\*(?: \(.*\))?$")
_SCALATEST_AT = re.compile(r"^\[info\]\s+(?P<msg>.*?) \((?P<file>[\w$]+\.\w+):(?P<line>\d+)\)$")
_SCALATEST_NOISE = re.compile(r"^\[error\] (?:Failed tests:|\t\S+|Failed: Total .*)$")


def find_scalatest(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``- test name *** FAILED ***`` and the ``message (File.scala:12)`` line under it."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if _SCALATEST_NOISE.match(line):
            claimed.add(i)
            continue
        m = _SCALATEST_FAILED.match(line)
        if m is None:
            continue
        claimed.add(i)
        at = _SCALATEST_AT.match(lines[i + 1]) if i + 1 < len(lines) else None
        if at:
            claimed.add(i + 1)
        said = at.group("msg").strip() if at else ""
        findings.append(
            CiFinding(
                tool="scalatest",
                kind="failed",
                message=f"{m.group('name')}: {said}" if said else m.group("name"),
                path=at.group("file") if at else None,
                line=int(at.group("line")) if at else None,
            )
        )
    return findings


# --- Jest --------------------------------------------------------------------------

_JEST_FILE = re.compile(r"^\s*(?P<what>FAIL|PASS)\s+(?P<path>\S+)(?: \(.*\))?$")
_JEST_TEST = re.compile(r"^\s+● (?P<name>.+)$")
_JEST_AT = re.compile(r"^\s+at .*?\(?(?P<path>[^()\s]+):(?P<line>\d+):(?P<col>\d+)\)?$")


def find_jest(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """A ``●`` test heading under a ``FAIL file``, located by the first stack frame in project code."""
    findings: list[CiFinding] = []
    file: str | None = None
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if named := _JEST_FILE.match(line):
            file = named.group("path") if named.group("what") == "FAIL" else None
            continue
        m = _JEST_TEST.match(line)
        if file is None or m is None or m.group("name").startswith("Console"):
            continue
        end = i + 1
        while end < len(lines) and not _JEST_TEST.match(lines[end]) and not _JEST_FILE.match(lines[end]):
            end += 1
        claimed.update(range(i, end))
        body = lines[i + 1 : end]
        frames = [at for row in body if (at := _JEST_AT.match(row)) and "node_modules" not in at.group("path")]
        said = [row.strip() for row in body if row.strip() and not _JEST_AT.match(row)]
        findings.append(
            CiFinding(
                tool="jest",
                kind="failed",
                message=f"{m.group('name')}: {said[0]}" if said else m.group("name"),
                path=frames[0].group("path") if frames else file,
                line=int(frames[0].group("line")) if frames else None,
                column=int(frames[0].group("col")) if frames else None,
                detail="\n".join(said[:DETAIL_LINES]) or None,
            )
        )
    return findings
