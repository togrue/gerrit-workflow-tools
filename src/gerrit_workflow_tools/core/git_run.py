"""Utilities for invoking git commands with consistent error handling."""

from __future__ import annotations

import codecs
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.call_trace import record_call
from gerrit_workflow_tools.core.changeish import unresolved_rev_message


logger = logging.getLogger(__name__)

_UNKNOWN_REVISION_RE = re.compile(r"unknown revision|bad revision|no such branch|Needed a single revision|bad object")

_NAMED_REVISION_RE = re.compile(r"(?:ambiguous argument|bad revision|no such branch:) '([^']+)'")


class GitError(RuntimeError):
    """Git command failed."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int = -1) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode

    def __str__(self) -> str:
        detail = self.stderr.strip()
        if not detail or detail.splitlines()[0] in str(self.args[0]):
            return str(self.args[0])
        return f"{self.args[0]}\n{detail}"


class UnknownRevisionError(GitError):
    """Git could not resolve a revision the user named."""


def is_unknown_revision(stderr: str) -> bool:
    """Whether *stderr* from a revision-reading git command says the revision does not exist."""
    return isinstance(stderr, str) and _UNKNOWN_REVISION_RE.search(stderr) is not None


def unknown_revision_error(cwd: Path | str | None, stderr: str, returncode: int) -> UnknownRevisionError:
    """An error naming the side of a range git could not resolve, in the words every command uses."""
    match = _NAMED_REVISION_RE.search(stderr)
    name = match.group(1) if match else ""
    missing = [part for part in re.split(r"\.{2,3}", name) if part and commit_sha_or_none(cwd, part) is None]
    rev = (missing[0] if missing else name).split("@{")[0] or name
    return UnknownRevisionError(unresolved_rev_message(rev), returncode=returncode)


def abs_cwd(cwd: Path | str | None) -> str:
    """Absolute form of *cwd*, the process cwd when ``None``."""
    if cwd is not None:
        return os.path.abspath(os.path.expanduser(str(cwd)))
    return os.getcwd()


def _run_git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    capture: bool = True,
    on_stderr: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *args]
    cwd_str = str(cwd) if cwd is not None else None
    logger.debug("run: %s (cwd=%s)", " ".join(cmd), cwd_str or ".")
    merged = {**os.environ, **env} if env else None
    t0 = time.perf_counter()
    result: subprocess.CompletedProcess[str]
    if on_stderr is not None:
        result = _stream_stderr(cmd, cwd=cwd, env=merged, on_line=on_stderr)
    elif input_text is None:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=merged,
            # Git writes UTF-8 whatever the console code page says; text=True alone uses the locale.
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            check=False,
        )
    else:
        # Bytes in: a text-mode pipe turns every LF of the input into CR LF on Windows, and git
        # would store that — in a commit message given to `commit-tree`, in a diff given to `patch-id`.
        raw = subprocess.run(
            cmd, cwd=cwd, env=merged, input=input_text.encode("utf-8"), capture_output=capture, check=False
        )
        result = subprocess.CompletedProcess(raw.args, raw.returncode, _as_text(raw.stdout), _as_text(raw.stderr))
    record_call("git", " ".join(cmd), time.perf_counter() - t0)
    return result


_LINE_END = re.compile(r"\r\n|\r|\n")
_DRAIN_SECONDS = 1.0
"""How long stderr may stay open once git exited: a process git started and left running holds it."""


def _stream_stderr(
    cmd: list[str], *, cwd: Path | str | None, env: dict[str, str] | None, on_line: Callable[[str], None]
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* talking to the terminal, except for stderr: each line of it goes to *on_line* as it comes.

    A line ends at a line feed or a carriage return (git's progress, ``Rebasing (1/2)`` and a
    carriage return), so nothing waits for a newline git never writes.
    """
    read_end, write_end = os.pipe()
    reader = threading.Thread(target=_read_lines, args=(read_end, on_line), daemon=True)
    reader.start()
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, stderr=write_end, check=False)
    finally:
        os.close(write_end)
        reader.join(_DRAIN_SECONDS)
    return subprocess.CompletedProcess(cmd, result.returncode, None, None)


def _read_lines(fd: int, on_line: Callable[[str], None]) -> None:
    decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
    pending = ""
    with os.fdopen(fd, "rb", buffering=0) as stream:
        while chunk := stream.read(4096):
            *lines, pending = _LINE_END.split(pending + decode(chunk))
            for line in lines:
                on_line(line)
    if rest := pending + decode(b"", final=True):
        on_line(rest)


def _as_text(output: bytes | None) -> str:
    """Git's output as text mode reads it: UTF-8, with every line ending a line feed."""
    if output is None:
        return None  # type: ignore[return-value]  # not captured, as in text mode
    return output.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _raise_if_failed(
    args: tuple[str, ...],
    p: subprocess.CompletedProcess[str],
    *,
    check: bool,
) -> None:
    if check and p.returncode != 0:
        unknown = args[0] in ("rev-parse", "log") and is_unknown_revision(p.stderr)
        raise (UnknownRevisionError if unknown else GitError)(
            f"git {' '.join(args)} failed: {(p.stderr.strip() or p.stdout.strip()).partition(chr(10))[0]}",
            stderr=p.stderr,
            returncode=p.returncode,
        )


def git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    input: str | None = None,
    capture: bool = True,
    on_stderr: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git with given args; cwd defaults to current directory.

    Every call runs git: nothing here remembers an answer, so nothing has to guess when one
    went stale. What a run reads once it keeps itself (``core/repository.py``).

    Pass *input* to feed stdin. Pass ``capture=False`` for a git that talks to the terminal
    itself, such as ``rebase -i``; with *on_stderr* as well, its stderr goes there line by line.
    """
    p = _run_git(*args, cwd=cwd, env=env, input_text=input, capture=capture, on_stderr=on_stderr)
    _raise_if_failed(args, p, check=check)
    return p


def git_out(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Return stripped stdout from git."""
    return git(*args, cwd=cwd, env=env).stdout.strip()


def commit_sha_or_none(cwd: Path | str | None, rev: str) -> str | None:
    """Full SHA for *rev* when the repository can resolve it to a commit, else ``None``."""
    p = git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None
