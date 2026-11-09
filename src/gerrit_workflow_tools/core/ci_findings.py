"""Ordered, content-driven finders that turn a CI log into **CI findings**.

Pipeline output varies within one log, so every finder runs over the whole text, in order:
pytest, then ruff / mypy, then gcc / clang-style compilers (with clang-format and linker
lines), then a generic fallback. A line one finder claims is skipped by the later ones, so
precise finders win and nothing is counted twice.

Reusable by custom **CI content providers**: :func:`find_ci_findings` takes plain text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding


Finder = Callable[[Sequence[str], set[int]], list[CiFinding]]
"""``(lines, claimed) -> findings``; a finder adds the indices it used to *claimed*."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\[?(?:\d{4}-\d\d-\d\d[T ])?\d\d:\d\d:\d\d(?:[.,]\d+)?Z?\]?\s+")
_PATH = r"(?:[A-Za-z]:)?[^\s:]+"


def clean_log_lines(text: str) -> list[str]:
    """Log lines without ANSI escapes, carriage returns or Jenkins timestamper prefixes."""
    return [_TIMESTAMP.sub("", _ANSI.sub("", line.rstrip("\r"))) for line in text.splitlines()]


# --- pytest ------------------------------------------------------------------------

_PYTEST_SUMMARY = re.compile(r"^(?P<what>FAILED|ERROR) (?P<node>\S+\.py(?:::\S+)?)(?: - (?P<msg>.*))?$")
_PYTEST_BANNER = re.compile(r"^={3,} (?P<title>.*?) ={3,}$")
_PYTEST_SECTION = re.compile(r"^_{3,} (?P<name>.+?) _{3,}$")
_PYTEST_RAISE_AT = re.compile(rf"^(?P<path>{_PATH}\.py):(?P<line>\d+): (?P<exc>[A-Za-z_][\w.]*)$")
_PYTEST_FRAME = re.compile(rf"^(?P<path>{_PATH}\.py):(?P<line>\d+): in \S+")
_PYTEST_E = re.compile(r"^E\s+(?P<text>.*)$")
_DETAIL_LINES = 8


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
                detail="\n".join(e_lines[:_DETAIL_LINES]) or None,
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
                detail="\n".join(e_lines[:_DETAIL_LINES]) or None,
            )
        )
    return findings


# --- ruff / mypy -------------------------------------------------------------------

_RUFF = re.compile(
    rf"^(?P<path>{_PATH}\.pyi?):(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]{{1,4}}\d{{2,4}}) (?:\[\*\] )?(?P<msg>.*)$"
)
_RUFF_FORMAT = re.compile(rf"^Would reformat: (?P<path>{_PATH})$")
_MYPY = re.compile(rf"^(?P<path>{_PATH}\.pyi?):(?P<line>\d+)(?::(?P<col>\d+))?: (?P<sev>error|warning): (?P<msg>.*)$")


def find_python_linters(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """ruff (``path:line:col: CODE msg``, ``Would reformat:``) and mypy (``path:line: error: msg``)."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if m := _RUFF.match(line):
            finding = CiFinding(
                tool="ruff",
                kind="lint",
                message=f"{m.group('code')} {m.group('msg')}",
                path=m.group("path"),
                line=int(m.group("line")),
                column=int(m.group("col")),
            )
        elif m := _RUFF_FORMAT.match(line):
            finding = CiFinding(tool="ruff", kind="format", message="would reformat", path=m.group("path"))
        elif m := _MYPY.match(line):
            finding = CiFinding(
                tool="mypy",
                kind=m.group("sev"),
                message=m.group("msg"),
                path=m.group("path"),
                line=int(m.group("line")),
                column=int(m.group("col")) if m.group("col") else None,
            )
        else:
            continue
        claimed.add(i)
        findings.append(finding)
    return findings


# --- gcc / clang, clang-format, linker ---------------------------------------------

_CC = re.compile(
    rf"^(?P<path>{_PATH}\.(?:c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|ipp|inl|m|mm|cu)):(?P<line>\d+):(?:(?P<col>\d+):)?"
    r" (?P<sev>fatal error|error|warning): (?P<msg>.*)$"
)
_UNDEFINED_REF = re.compile(
    rf"^(?P<path>{_PATH}?)(?::(?P<line>\d+))?:(?:\([^)]*\):)? (?P<msg>undefined reference to .*)$"
)
_LINKER = re.compile(r"^(?:\S*/)?(?:ld(?:\.\w+)?|collect2|lld): (?:error: )?(?P<msg>.*)$")


def find_compilers(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``file:line:col: error: msg`` (gcc/clang; clang-format violations), and linker errors."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if m := _CC.match(line):
            msg = m.group("msg")
            clang_format = "-Wclang-format-violations" in msg
            finding = CiFinding(
                tool="clang-format" if clang_format else "cc",
                kind="format" if clang_format else ("warning" if m.group("sev") == "warning" else "error"),
                message=msg,
                path=m.group("path"),
                line=int(m.group("line")),
                column=int(m.group("col")) if m.group("col") else None,
            )
        elif m := _UNDEFINED_REF.match(line):
            finding = CiFinding(
                tool="ld",
                kind="error",
                message=m.group("msg"),
                path=m.group("path") or None,
                line=int(m.group("line")) if m.group("line") else None,
            )
        elif m := _LINKER.match(line):
            if " in function " in f" {m.group('msg')}":
                claimed.add(i)  # context for the next line's undefined reference, not a finding
                continue
            finding = CiFinding(tool="ld", kind="error", message=m.group("msg"))
        else:
            continue
        claimed.add(i)
        findings.append(finding)
    return findings


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
