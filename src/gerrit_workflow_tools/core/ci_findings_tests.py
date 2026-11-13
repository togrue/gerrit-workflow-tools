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

_PYTEST_SUMMARY = re.compile(r"^(?P<what>FAILED|ERROR) (?P<node>\S+\.py(?:::\S+)?)(?: - (?P<msg>.*))?$")
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
