"""Finder for one-line diagnostics that name their place: ``path:line[:col]: severity: msg``.

Shape: everything the finding needs is on one line. Linters and compilers share it; what
tells them apart is the file extension and what follows the position. Each tool is one
rule in :data:`_RULES`: a pattern, and how its match becomes a finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace

from gerrit_workflow_tools.core.ci_content import CiFinding, Severity
from gerrit_workflow_tools.core.ci_log import DETAIL_LINES, LEVEL, PATH, level_severity


Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], CiFinding | None]]
"""A pattern and its reading; ``None`` claims the line without a finding (context, noise)."""


def _located(m: re.Match[str], *, tool: str, kind: str, message: str, severity: Severity = "error") -> CiFinding:
    groups = m.groupdict()
    return CiFinding(
        tool=tool,
        kind=kind,
        message=message,
        path=groups.get("path") or None,
        line=int(groups["line"]) if groups.get("line") else None,
        column=int(groups["col"]) if groups.get("col") else None,
        severity=severity,
    )


def _by_severity(m: re.Match[str], tool: str, message: str | None = None) -> CiFinding:
    """A finding whose kind and severity are the tool's own word: ``error`` / ``warning``."""
    severity = level_severity(m.group("sev"))
    return _located(
        m, tool=tool, kind=severity, message=m.group("msg") if message is None else message, severity=severity
    )


# --- Python: ruff (concise), pylint, mypy ------------------------------------------

_PY = rf"(?P<path>{PATH}\.pyi?)"
_RUFF = re.compile(
    rf"^{_PY}:(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]+\d+|[a-z]+(?:-[a-z]+)+:) (?:\[\*\] )?(?P<msg>.*)$"
)
_RUFF_FORMAT = re.compile(rf"^Would reformat: (?P<path>{PATH})$")
_PYLINT = re.compile(rf"^{_PY}:(?P<line>\d+):(?P<col>\d+): (?P<code>[CRWEFI]\d{{4}}): (?P<msg>.*)$")
_MYPY = re.compile(rf"^{_PY}:(?P<line>\d+)(?::(?P<col>\d+))?(?::\d+:\d+)?: (?P<sev>error|warning):\s*(?P<msg>.*)$")
_PYLINT_KINDS = {"C": "convention", "R": "refactor", "W": "warning", "E": "error", "F": "fatal", "I": "info"}


def _ruff(m: re.Match[str]) -> CiFinding:
    syntax = m.group("code").endswith(":")
    return _located(m, tool="ruff", kind="syntax" if syntax else "lint", message=f"{m.group('code')} {m.group('msg')}")


def _pylint(m: re.Match[str]) -> CiFinding:
    letter = m.group("code")[0]
    return _located(
        m,
        tool="pylint",
        kind=_PYLINT_KINDS[letter],
        message=f"{m.group('code')} {m.group('msg')}",
        severity="error" if letter in "EF" else "warning",
    )


# --- C / C++: gcc, clang, clang-format, the linker ---------------------------------

_CC = re.compile(
    rf"^(?P<path>{PATH}\.(?:c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|ipp|inl|m|mm|cu)):(?P<line>\d+):(?:(?P<col>\d+):)?"
    r" (?P<sev>fatal error|error|warning): (?P<msg>.*)$"
)
_CC_DRIVER = re.compile(
    r"^(?:\S*[/\\])?(?:clang(?:\+\+)?|gcc|g\+\+|cc|c\+\+|cc1(?:plus)?)(?:-\d+)?(?:\.exe)?: "
    r"(?:fatal )?error: (?P<msg>.*)$"
)
_LD = r"(?:\S*[/\\])?(?:ld(?:\.\w+)?|collect2(?:\.exe)?|lld)"
_LINK_MSG = r"(?P<msg>(?:undefined reference to|multiple definition of) .*)"
_LD_LOCATED = re.compile(rf"^{_LD}: (?:\S+\.o(?:bj)?:)?(?P<path>[^\s:()]+):\([^)]*\): {_LINK_MSG}$")
_UNDEFINED_REF = re.compile(rf"^(?P<path>{PATH}?)(?::(?P<line>\d+))?:(?:\([^)]*\):)? {_LINK_MSG}$")
_LINKER = re.compile(rf"^{_LD}: (?:error: )?(?P<msg>.*)$")


def _cc(m: re.Match[str]) -> CiFinding:
    if "-Wclang-format-violations" in m.group("msg"):
        return _located(m, tool="clang-format", kind="format", message=m.group("msg"))
    return _by_severity(m, "cc")


def _cc_driver(m: re.Match[str]) -> CiFinding | None:
    # "all warnings being treated as errors" restates -Werror; the diagnostics themselves are findings.
    if "warnings being treated as errors" in m.group("msg"):
        return None
    return CiFinding(tool="cc", kind="error", message=m.group("msg"))


def _linker(m: re.Match[str]) -> CiFinding | None:
    if " in function " in f" {m.group('msg')}":
        return None  # context for the next line's undefined reference, not a finding
    return CiFinding(tool="ld", kind="error", message=m.group("msg"))


# --- JVM: javac (plain and through Maven), scalac through sbt ----------------------

_JAVAC = re.compile(rf"^(?P<path>{PATH}\.java):(?P<line>\d+): (?P<sev>error|warning): (?P<msg>.*)$")
_MAVEN = re.compile(rf"^{LEVEL} (?P<path>{PATH}\.(?P<ext>java|scala|kt)):\[(?P<line>\d+),(?P<col>\d+)\] (?P<msg>.*)$")
_SBT = re.compile(rf"^{LEVEL} (?P<path>{PATH}\.(?P<ext>scala|sc|java)):(?P<line>\d+):(?P<col>\d+): (?P<msg>.*)$")
_SCALA3 = re.compile(
    rf"^{LEVEL} -- (?:\[(?P<code>E\d+)\] )?(?P<title>[^:]*): "
    rf"(?P<path>{PATH}\.(?:scala|sc)):(?P<line>\d+):(?P<col>\d+)[\s-]*$"
)
_JVM_TOOLS = {"java": "javac", "scala": "scalac", "sc": "scalac", "kt": "kotlinc"}


def _jvm(m: re.Match[str]) -> CiFinding:
    severity = level_severity(m.group("level"))
    return _located(m, tool=_JVM_TOOLS[m.group("ext")], kind=severity, message=m.group("msg"), severity=severity)


def _scala3(m: re.Match[str]) -> CiFinding:
    severity = level_severity(m.group("level"))
    title = m.group("title").strip()
    message = f"{m.group('code')} {title}" if m.group("code") else title
    return _located(m, tool="scalac", kind=severity, message=message, severity=severity)


# --- TypeScript, rustfmt -----------------------------------------------------------

_TS = rf"(?P<path>{PATH}\.(?:[cm]?[jt]s|[jt]sx|vue))"
_TSC = re.compile(rf"^{_TS}\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) (?P<code>TS\d+): (?P<msg>.*)$")
_TSC_PRETTY = re.compile(rf"^{_TS}:(?P<line>\d+):(?P<col>\d+) - (?P<sev>error|warning) (?P<code>TS\d+): (?P<msg>.*)$")
_RUSTFMT = re.compile(r"^Diff in (?P<path>.+?\.rs)(?::| at line )(?P<line>\d+):$")


def _tsc(m: re.Match[str]) -> CiFinding:
    return _by_severity(m, "tsc", f"{m.group('code')} {m.group('msg')}")


def _rustfmt(m: re.Match[str]) -> CiFinding:
    # rustfmt prints one "Diff in" per hunk; the file is the finding, so the line stays out.
    return CiFinding(tool="rustfmt", kind="format", message="would reformat", path=m.group("path"))


_RULES: tuple[Rule, ...] = (
    (_PYLINT, _pylint),
    (_RUFF, _ruff),
    (_RUFF_FORMAT, lambda m: _located(m, tool="ruff", kind="format", message="would reformat")),
    (_MYPY, lambda m: _by_severity(m, "mypy")),
    (_CC, _cc),
    (_JAVAC, lambda m: _by_severity(m, "javac")),
    (_MAVEN, _jvm),
    (_SCALA3, _scala3),
    (_SBT, _jvm),
    (_TSC, _tsc),
    (_TSC_PRETTY, _tsc),
    (_RUSTFMT, _rustfmt),
    (_LD_LOCATED, lambda m: _located(m, tool="ld", kind="error", message=m.group("msg"))),
    (_UNDEFINED_REF, lambda m: _located(m, tool="ld", kind="error", message=m.group("msg"))),
    (_LINKER, _linker),
    (_CC_DRIVER, _cc_driver),
)

_LEVEL_LINE = re.compile(rf"^{LEVEL} ?(?P<text>.*)$")


def _scala3_excerpt(lines: Sequence[str], start: int, claimed: set[int]) -> str | None:
    """The framed source excerpt under a Scala 3 header: where its actual message is."""
    excerpt: list[str] = []
    for j in range(start + 1, len(lines)):
        m = _LEVEL_LINE.match(lines[j])
        if m is None or m.group("text").startswith("-- ") or "|" not in m.group("text"):
            break
        claimed.add(j)
        excerpt.append(m.group("text").rstrip())
    return "\n".join(excerpt[:DETAIL_LINES]) or None


def find_located(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """Every one-line located diagnostic, in log order."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        for pattern, read in _RULES:
            m = pattern.match(line)
            if m is None:
                continue
            claimed.add(i)
            finding = read(m)
            if finding is None:
                break
            if pattern is _MYPY and not finding.message and i + 1 < len(lines):
                # mypy --pretty wraps: the message is on the line below its position.
                claimed.add(i + 1)
                finding = replace(finding, message=lines[i + 1].strip(), fingerprint="")
            elif pattern is _SCALA3:
                finding = replace(finding, detail=_scala3_excerpt(lines, i, claimed))
            findings.append(finding)
            break
    return findings
