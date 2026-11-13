"""Finders for diagnostics printed as a block: a header line, and its place on a line nearby.

Shape: the message and the position are on different lines of one block, so a finder reads
the block as a whole and claims all of it. rustc, clippy and ruff share one layout (a header,
then ``--> path:line:col``); a Python traceback, a CMake error, eslint's per-file listing and
npm's own error report each have theirs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_log import DETAIL_LINES, PATH, level_severity


def _block_end(lines: Sequence[str], start: int) -> int:
    """Index after the block that starts at *start*: it runs to the next blank line."""
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return end


# --- rustc / clippy / ruff: header, then ``--> path:line:col`` ----------------------

_ARROW = re.compile(r"^\s*--> (?P<path>.+?):(?P<line>\d+):(?P<col>\d+)$")
_RUST_HEAD = re.compile(r"^(?P<sev>error|warning)(?:\[(?P<code>[A-Z]\d+)\])?: (?P<msg>.+)$")
_RUFF_HEAD = re.compile(r"^(?P<code>[A-Z]+\d+|[a-z]+(?:-[a-z]+)+:) (?:\[\*\] )?(?P<msg>.+)$")
_RUST_NOISE = re.compile(
    r"^(?:error: aborting due to |warning: `.+` \(.+\) generated \d+ warning|For more information about this error)"
)
_GUTTER = re.compile(r"^\s*\d*\s*\|\s?(?P<text>.*)$")


def _excerpt(block: Sequence[str]) -> str | None:
    """The source lines and their annotations from a block's gutter, without the empty rows."""
    rows = [m.group("text").rstrip() for line in block if (m := _GUTTER.match(line))]
    return "\n".join([row for row in rows if row.strip()][:DETAIL_LINES]) or None


def find_arrow_blocks(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """rustc, clippy and ruff diagnostics: the header names the problem, the arrow line the place."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        if _RUST_NOISE.match(line):
            claimed.add(i)
            continue
        arrow = _ARROW.match(lines[i + 1]) if i + 1 < len(lines) else None
        if arrow is None:
            continue
        end = _block_end(lines, i)
        block = lines[i + 2 : end]
        if m := _RUST_HEAD.match(line):
            severity = level_severity(m.group("sev"))
            tool = "clippy" if any("clippy::" in row for row in block) else "rustc"
            message = f"{m.group('code')} {m.group('msg')}" if m.group("code") else m.group("msg")
            kind: str = severity
        elif m := _RUFF_HEAD.match(line):
            severity, tool = "error", "ruff"
            message = f"{m.group('code')} {m.group('msg')}"
            kind = "syntax" if m.group("code").endswith(":") else "lint"
        else:
            continue
        claimed.update(range(i, end))
        findings.append(
            CiFinding(
                tool=tool,
                kind=kind,
                message=message,
                path=arrow.group("path"),
                line=int(arrow.group("line")),
                column=int(arrow.group("col")),
                detail=_excerpt(block),
                severity=severity,
            )
        )
    return findings


# --- Python traceback --------------------------------------------------------------

_TRACEBACK = re.compile(r"^(?P<indent>\s*)Traceback \(most recent call last\):$")
_FRAME = re.compile(r'^\s+File "(?P<path>.+)", line (?P<line>\d+)(?:, in (?P<fn>.+))?$')
_NOT_OURS = ("site-packages", "dist-packages", "/lib/python", "\\Lib\\", "<frozen ", "<string>")


def find_tracebacks(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """An uncaught Python exception: its last line, located at the deepest frame in project code."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        start = _TRACEBACK.match(line)
        if i in claimed or start is None:
            continue
        indent = len(start.group("indent"))
        frames: list[tuple[str, int]] = []
        end = i + 1
        while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
            if m := _FRAME.match(lines[end]):
                frames.append((m.group("path"), int(m.group("line"))))
            end += 1
        if not frames:
            continue
        raised = lines[end].strip() if end < len(lines) else ""
        claimed.update(range(i, min(end + 1, len(lines))))
        ours = [frame for frame in frames if not any(mark in frame[0] for mark in _NOT_OURS)]
        path, lineno = (ours or frames)[-1]
        findings.append(
            CiFinding(tool="python", kind="error", message=raised or "uncaught exception", path=path, line=lineno)
        )
    return findings


# --- CMake -------------------------------------------------------------------------

_CMAKE = re.compile(
    rf"^CMake (?:Deprecation )?(?P<sev>Error|Warning)(?: \(dev\))?"
    rf"(?: at (?P<path>{PATH}):(?P<line>\d+) \((?P<command>[^)]*)\))?:\s*(?P<rest>.*)$"
)


def find_cmake(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """``CMake Error at CMakeLists.txt:3 (find_package):`` and the indented message under it."""
    findings: list[CiFinding] = []
    for i, line in enumerate(lines):
        m = _CMAKE.match(line)
        if i in claimed or m is None:
            continue
        end = i + 1
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith(" ")):
            end += 1
        claimed.update(range(i, end))
        text = [row.strip() for row in lines[i + 1 : end]]
        while text and not text[0]:
            text.pop(0)
        first = text[: text.index("")] if "" in text else text
        rest = [row for row in text[len(first) :] if row]
        severity = level_severity(m.group("sev"))
        findings.append(
            CiFinding(
                tool="cmake",
                kind=severity,
                message=m.group("rest").strip() or " ".join(first) or "configuration failed",
                path=m.group("path"),
                line=int(m.group("line")) if m.group("line") else None,
                detail="\n".join(rest[:DETAIL_LINES]) or None,
                severity=severity,
            )
        )
    return findings


# --- eslint (stylish) --------------------------------------------------------------

_ESLINT_ROW = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning)\s+(?P<msg>.+?)(?:\s{2,}(?P<rule>\S+))?$"
)
_ESLINT_FILE = re.compile(r"^(?P<path>\S.*\.\w+)$")
_ESLINT_TOTAL = re.compile(r"^\S+ \d+ problems? \(\d+ errors?, \d+ warnings?\)$")


def find_eslint(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """eslint's default report: a file path on its own line, then ``line:col  severity  msg  rule`` rows."""
    findings: list[CiFinding] = []
    path: str | None = None
    for i, line in enumerate(lines):
        row = None if i in claimed else _ESLINT_ROW.match(line)
        if row is None:
            # Anything but a row ends the file's listing; a path line right above rows starts one.
            path = None
            if i in claimed:
                continue
            named = _ESLINT_FILE.match(line)
            if named and i + 1 < len(lines) and _ESLINT_ROW.match(lines[i + 1]):
                path = named.group("path")
            if path or _ESLINT_TOTAL.match(line):
                claimed.add(i)
            continue
        if path is None:
            continue
        claimed.add(i)
        severity = level_severity(row.group("sev"))
        rule = row.group("rule")
        findings.append(
            CiFinding(
                tool="eslint",
                kind="lint",
                message=f"{row.group('msg')} ({rule})" if rule else row.group("msg"),
                path=path,
                line=int(row.group("line")),
                column=int(row.group("col")),
                severity=severity,
            )
        )
    return findings


# --- npm's own errors --------------------------------------------------------------

_NPM = re.compile(r"^npm (?:ERR!|error)(?: (?P<text>.*))?$")
_NPM_CODE = re.compile(r"^code (?P<code>\S+)$")
_NPM_SKIP = re.compile(
    r"^(?:(?:errno|syscall|path|dest|command)\b|A complete log of this run"
    r"|To see a list of scripts|npm run$|Exit status|$)"
)


def find_npm(lines: Sequence[str], claimed: set[int]) -> list[CiFinding]:
    """One finding per ``npm error`` / ``npm ERR!`` report: its code and its first telling line."""
    findings: list[CiFinding] = []
    i = 0
    while i < len(lines):
        if i in claimed or not _NPM.match(lines[i]):
            i += 1
            continue
        code: str | None = None
        text: list[str] = []
        while i < len(lines) and (m := _NPM.match(lines[i])):
            claimed.add(i)
            row = (m.group("text") or "").strip()
            if found := _NPM_CODE.match(row):
                code = code or found.group("code")
            elif not _NPM_SKIP.match(row):
                text.append(row)
            i += 1
        first = text[0].removeprefix(f"{code} ") if text and code else (text[0] if text else "")
        message = ": ".join(part for part in (code, first) if part) or "npm failed"
        findings.append(
            CiFinding(tool="npm", kind="error", message=message, detail="\n".join(text[1:DETAIL_LINES]) or None)
        )
    return findings
