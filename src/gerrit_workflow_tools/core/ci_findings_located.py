"""Finders for one-line diagnostics that name their place: ``path:line[:col]: severity: msg``.

Shape: everything the finding needs is on one line. Linters and compilers share it; what
tells them apart is the file extension and what follows the position.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_log import PATH


# --- ruff / mypy -------------------------------------------------------------------

_RUFF = re.compile(
    rf"^(?P<path>{PATH}\.pyi?):(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]{{1,4}}\d{{2,4}}) (?:\[\*\] )?(?P<msg>.*)$"
)
_RUFF_FORMAT = re.compile(rf"^Would reformat: (?P<path>{PATH})$")
_MYPY = re.compile(rf"^(?P<path>{PATH}\.pyi?):(?P<line>\d+)(?::(?P<col>\d+))?: (?P<sev>error|warning): (?P<msg>.*)$")


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
    rf"^(?P<path>{PATH}\.(?:c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|ipp|inl|m|mm|cu)):(?P<line>\d+):(?:(?P<col>\d+):)?"
    r" (?P<sev>fatal error|error|warning): (?P<msg>.*)$"
)
_UNDEFINED_REF = re.compile(
    rf"^(?P<path>{PATH}?)(?::(?P<line>\d+))?:(?:\([^)]*\):)? (?P<msg>undefined reference to .*)$"
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
