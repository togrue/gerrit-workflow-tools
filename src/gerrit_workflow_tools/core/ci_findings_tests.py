"""Finders for test runners: a summary of what failed, located by the failure's own section.

Shape: the runner names each failed test once, and prints a section per failure that holds
the traceback. The finding is the test; its location and ``detail`` come from the section.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_log import DETAIL_LINES, PATH


# --- pytest ------------------------------------------------------------------------

_PYTEST_SUMMARY = re.compile(r"^(?P<what>FAILED|ERROR) (?P<node>\S+\.py(?:::.+?)?)(?: - (?P<msg>.*))?$")
_PYTEST_BANNER = re.compile(r"^={3,} (?P<title>.*?) ={3,}$")
_PYTEST_SECTION = re.compile(r"^_{3,} (?P<name>.+?) _{3,}$")
_PYTEST_RAISE_AT = re.compile(rf"^(?P<path>{PATH}\.py):(?P<line>\d+): (?P<exc>[A-Za-z_][\w.]*)$")
_PYTEST_FRAME = re.compile(rf"^(?P<path>{PATH}\.py):(?P<line>\d+): in \S+")
_PYTEST_E = re.compile(r"^E\s+(?P<text>.*)$")


def _pytest_sections(lines: Sequence[str], claimed: set[int]) -> dict[str, tuple[str | None, int | None, list[str]]]:
    """Failure sections under ``== FAILURES ==`` / ``== ERRORS ==``: name -> (path, line, E-lines)."""
    sections: dict[str, tuple[str | None, int | None, list[str]]] = {}
    inside = False
    name: str | None = None
    for i, line in enumerate(lines):
        banner = _PYTEST_BANNER.match(line)
        if banner:
            inside = banner.group("title") in ("FAILURES", "ERRORS")
            name = None
            if inside:
                claimed.add(i)
            continue
        if not inside:
            continue
        claimed.add(i)
        header = _PYTEST_SECTION.match(line)
        if header:
            name = header.group("name")
            sections[name] = (None, None, [])
            continue
        if name is None:
            continue
        path, lineno, e_lines = sections[name]
        if m := _PYTEST_E.match(line):
            e_lines.append(m.group("text"))
        elif (m := _PYTEST_RAISE_AT.match(line)) or ((m := _PYTEST_FRAME.match(line)) and path is None):
            # The raising frame wins; a ``path:line: in fn`` frame only fills a gap (--tb=short).
            path, lineno = m.group("path"), int(m.group("line"))
        sections[name] = (path, lineno, e_lines)
    return sections


def _section_name(node: str) -> str:
    """pytest's section header for a node id: ``a.py::Cls::test_x`` -> ``Cls.test_x``."""
    parts = node.split("::")[1:]
    return ".".join(parts) if parts else f"ERROR collecting {node}"


def find_pytest(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """Failed and erroring tests: the summary line, located by the failure section's traceback."""
    sections = _pytest_sections(lines, claimed)
    findings: list[CiFinding] = []
    used: set[str] = set()
    for i, line in enumerate(lines):
        if i in claimed or not (m := _PYTEST_SUMMARY.match(line)):
            continue
        claimed.add(i)
        node = m.group("node")
        name = _section_name(node)
        path, lineno, e_lines = sections.get(name, (None, None, []))
        used.add(name)
        message = m.group("msg") or (e_lines[0] if e_lines else "")
        findings.append(
            CiFinding(
                tool="pytest",
                kind="failed" if m.group("what") == "FAILED" else "error",
                message=f"{node}: {message}" if message else node,
                path=path or node.split("::")[0],
                line=lineno,
                detail="\n".join(e_lines[:DETAIL_LINES]) or None,
            )
        )
    for name, (path, lineno, e_lines) in sections.items():
        if name in used:
            continue
        findings.append(
            CiFinding(
                tool="pytest",
                kind="error" if name.startswith("ERROR ") else "failed",
                message=f"{name}: {e_lines[0]}" if e_lines else name,
                path=path,
                line=lineno,
                detail="\n".join(e_lines[:DETAIL_LINES]) or None,
            )
        )
    return findings


# --- cargo test --------------------------------------------------------------------

_CARGO_FAILED = re.compile(r"^test (?P<name>\S+)(?: - should panic)? \.\.\. FAILED$")
_CARGO_SECTION = re.compile(r"^---- (?P<name>\S+) stdout ----$")
_CARGO_PANIC = re.compile(r"^thread '[^']*'(?: \(\d+\))? panicked at (?P<path>.+?):(?P<line>\d+):(?P<col>\d+):$")
_CARGO_PANIC_OLD = re.compile(
    r"^thread '[^']*' panicked at '(?P<msg>.*)', (?P<path>[^,']+):(?P<line>\d+):(?P<col>\d+)$"
)
_CARGO_NOISE = re.compile(r"^(?:test result: FAILED\..*|error: test failed, to rerun pass .*)$")
_CARGO_LISTED = re.compile(r"^    \S+$")


def _cargo_panics(lines: Sequence[str], claimed: set[int]) -> dict[str, tuple[str, int, int, list[str]]]:
    """Per test, from its ``---- name stdout ----`` section: where it panicked and what it said."""
    panics: dict[str, tuple[str, int, int, list[str]]] = {}
    name: str | None = None
    listing = False  # under a "failures:" heading, where the failed tests are named again
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if section := _CARGO_SECTION.match(line):
            name = section.group("name")
        elif line.startswith("failures:"):
            name, listing = None, True
        elif name is not None and (m := _CARGO_PANIC.match(line)):
            said: list[str] = []
            for row in lines[i + 1 :]:
                if not row.strip() or row.startswith(("note: ", "---- ")):
                    break
                said.append(row.rstrip())
            panics.setdefault(name, (m.group("path"), int(m.group("line")), int(m.group("col")), said))
        elif name is not None and (m := _CARGO_PANIC_OLD.match(line)):
            panics.setdefault(name, (m.group("path"), int(m.group("line")), int(m.group("col")), [m.group("msg")]))
        elif name is None and listing and _CARGO_LISTED.match(line):
            pass
        elif name is None and not (_CARGO_FAILED.match(line) or _CARGO_NOISE.match(line)):
            listing = listing and not line.strip()
            continue
        claimed.add(i)
    return panics


def find_cargo_test(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``test name ... FAILED``, located by the panic in the test's ``---- name stdout ----`` section."""
    failed = [m.group("name") for i, line in enumerate(lines) if i not in claimed and (m := _CARGO_FAILED.match(line))]
    if not failed:
        return []
    panics = _cargo_panics(lines, claimed)
    findings: list[CiFinding] = []
    for test in failed:
        path, lineno, col, said = panics.get(test, (None, None, None, []))
        findings.append(
            CiFinding(
                tool="cargo-test",
                kind="failed",
                message=f"{test}: {said[0].strip()}" if said else test,
                path=path,
                line=lineno,
                column=col,
                detail="\n".join(said[:DETAIL_LINES]) or None,
            )
        )
    return findings


# --- GoogleTest / CTest ------------------------------------------------------------

_GTEST_RUN = re.compile(r"^\[\s*RUN\s*\] (?P<name>\S+)$")
_GTEST_FAILURE = re.compile(rf"^(?P<path>{PATH}):(?P<line>\d+): Failure$")
_GTEST_FAILED = re.compile(r"^\[\s*FAILED\s*\] (?P<name>[\w/.$]+)(?:, where .*?)?(?: \(\d+ ms\))?$")
_GTEST_TOTAL = re.compile(r"^(?:\[\s*FAILED\s*\] \d+ tests?, listed below:| *\d+ FAILED TESTS?)$")
_CTEST_ROW = re.compile(r"^\s+\d+ - (?P<name>\S+) \((?P<why>[^)]+)\)\s*$")


def find_gtest(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``[  FAILED  ] Suite.Test``, located by the first ``file:line: Failure`` of its run."""
    findings: list[CiFinding] = []
    seen: set[str] = set()
    name: str | None = None
    where: tuple[str, int] | None = None
    said: list[str] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if run := _GTEST_RUN.match(line):
            name, where, said = run.group("name"), None, []
        elif m := _GTEST_FAILURE.match(line):
            if name is None:
                # A failure outside any test's run (a fixture, or output cut to the failures alone).
                told = lines[i + 1].strip() if i + 1 < len(lines) else ""
                at = (m.group("path"), int(m.group("line")))
                findings.append(
                    CiFinding(tool="gtest", kind="failed", message=told or "Failure", path=at[0], line=at[1])
                )
            where = where or (m.group("path"), int(m.group("line")))
        elif m := _GTEST_FAILED.match(line):
            test = m.group("name")
            ours = test == name
            if test not in seen:
                seen.add(test)
                findings.append(
                    CiFinding(
                        tool="gtest",
                        kind="failed",
                        message=f"{test}: {said[0].strip()}" if ours and said else test,
                        path=where[0] if ours and where else None,
                        line=where[1] if ours and where else None,
                        detail=("\n".join(said[:DETAIL_LINES]) or None) if ours else None,
                    )
                )
            name = None
        elif name is not None and where is not None:
            said.append(line.rstrip())
        elif not _GTEST_TOTAL.match(line):
            continue
        claimed.add(i)
    return findings


def find_ctest(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """The rows under CTest's ``The following tests FAILED:``."""
    findings: list[CiFinding] = []
    listing = False
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if line.startswith("The following tests FAILED:"):
            listing = True
        elif listing and (m := _CTEST_ROW.match(line)):
            findings.append(CiFinding(tool="ctest", kind="failed", message=f"{m.group('name')} ({m.group('why')})"))
        elif line.startswith("Errors while running CTest"):
            listing = False
        else:
            listing = False
            continue
        claimed.add(i)
    return findings


# --- node --test -------------------------------------------------------------------

_NODE_AT = re.compile(r"^test at (?P<path>.+?):(?P<line>\d+):(?P<col>\d+)$")
_NODE_FAILED = re.compile(r"^\s*✖ (?P<name>.+?) \([\d.]+ms\)$")


def find_node_test(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """The entries under ``✖ failing tests:``: ``test at file:line:col``, its name, its error."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if _NODE_FAILED.match(line) or line == "✖ failing tests:":
            claimed.add(i)  # the run's own progress line; the entry below is the finding
            continue
        at = _NODE_AT.match(line)
        named = _NODE_FAILED.match(lines[i + 1]) if at and i + 1 < len(lines) else None
        if at is None or named is None:
            continue
        end = i + 2
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith(" ")):
            end += 1
        claimed.update(range(i, end))
        said = [row.strip() for row in lines[i + 2 : end] if row.strip()]
        findings.append(
            CiFinding(
                tool="node-test",
                kind="failed",
                message=f"{named.group('name')}: {said[0]}" if said else named.group("name"),
                path=at.group("path"),
                line=int(at.group("line")),
                column=int(at.group("col")),
                detail="\n".join(said[:DETAIL_LINES]) or None,
            )
        )
    return findings
