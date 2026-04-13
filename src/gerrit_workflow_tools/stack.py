from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.config import resolve_local_base_ref, stop_patterns
from gerrit_workflow_tools.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)


def _cwd_key(cwd: Path | str | None) -> str:
    p = Path.cwd() if cwd is None else Path(cwd)
    return str(p.resolve())


@dataclass(frozen=True)
class StackSnapshot:
    """Merge-base + ``merge_base..HEAD`` commits from one ``git log`` range."""

    merge_base: str
    target_display: str
    base_ref: str
    rows: tuple[tuple[str, str, str, str], ...]


def clear_stack_snapshot_cache() -> None:
    """Drop memoized stack snapshots (e.g. between tests or after mutating the repo)."""
    _cached_stack_snapshot.cache_clear()


def _merge_base_with_target_impl(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str, str]:
    base_ref, display = resolve_local_base_ref(cwd, branch)
    mb = git_out("merge-base", "HEAD", base_ref, cwd=cwd)
    return mb, display, base_ref


@lru_cache(maxsize=64)
def _cached_stack_snapshot(cwd_key: str, branch: str) -> StackSnapshot:
    cwd = Path(cwd_key)
    mb, display, base_ref = _merge_base_with_target_impl(cwd, branch or None)
    rows_list = stack_commits_metadata_one_log(cwd, f"{mb}..HEAD")
    rows_t = tuple(tuple(r) for r in rows_list)
    return StackSnapshot(mb, display, base_ref, rows_t)


def get_stack_snapshot(cwd: Path | str | None, branch: str | None = None) -> StackSnapshot:
    """Return merge-base and oldest-first commits for ``merge_base..HEAD`` (cached per cwd/branch)."""
    return _cached_stack_snapshot(_cwd_key(cwd), branch or "")


@dataclass
class StackCommit:
    index: int
    sha: str
    short_sha: str
    subject: str
    change_id: str | None
    ready_state: str  # "ready" | "blocked(...)" | "after-blocked"


CHANGE_ID_RE = re.compile(r"^Change-Id:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_change_id(message: str) -> str | None:
    """Extract ``Change-Id: …`` from a commit message body, or return None."""
    m = CHANGE_ID_RE.search(message)
    return m.group(1) if m else None


def merge_base_with_target(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str, str]:
    """Return ``(merge_base_sha, target_display_name, base_ref_commit)`` from the memoized stack snapshot."""
    snap = get_stack_snapshot(cwd, branch)
    return snap.merge_base, snap.target_display, snap.base_ref


def rev_spec_merge_base_to_end(cwd: Path | str | None, input_arg: str) -> str:
    """``merge_base..END`` where END is *input_arg* or the right side of ``left..right``."""
    mb, _, _ = merge_base_with_target(cwd)
    if ".." not in input_arg:
        logger.debug("rev_spec_merge_base_to_end rev-parse %r (end ref)", input_arg)
        end = git_out("rev-parse", input_arg, cwd=cwd)
        return f"{mb}..{end}"
    idx = input_arg.index("..")
    right = input_arg[idx + 2 :].strip() or "HEAD"
    logger.debug("rev_spec_merge_base_to_end rev-parse %r (range right)", right)
    end = git_out("rev-parse", right, cwd=cwd)
    return f"{mb}..{end}"


def list_stack_commits(
    cwd: Path | str | None,
    merge_base: str,
    *,
    head: str = "HEAD",
) -> list[str]:
    """Oldest-first SHAs from merge_base..head (exclusive..exclusive range)."""
    return rev_list_reverse(cwd, merge_base, head)


def rev_list_reverse(cwd: Path | str | None, start_exclusive: str, end_inclusive: str) -> list[str]:
    """Commits reachable from end_inclusive but not start_exclusive, oldest first."""
    p = git(
        "rev-list",
        "--reverse",
        f"{start_exclusive}..{end_inclusive}",
        cwd=cwd,
        check=False,
    )
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


# Field separator in `git log --format` (ASCII RS). Avoids NUL in argv (Windows
# subprocess rejects embedded nulls); %x1e is expanded by git, not by the shell.
_RS = "\x1e"
# Git expands %x1e in --format; same RS style as :func:`stack_commits_metadata_one_log`.
_LOG_SHA_BODY_FMT = "%H%x1e%B%x1e"


def parse_git_log_sha_body_rs(raw: str) -> list[tuple[str, str]]:
    """Parse RS-delimited ``git log`` output with format ``%H%x1e%B%x1e`` (see :func:`git_log_sha_body`)."""
    parts = raw.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        if i + 1 >= len(parts):
            break
        sha, msg = parts[i].strip(), parts[i + 1]
        out.append((sha, msg))
    return out


def git_log_sha_body(
    cwd: Path | str | None,
    rev_spec: str,
    *,
    single: bool,
) -> str:
    """One ``git log``; stdout is RS-delimited full SHA and raw body per commit."""
    args: list[str] = ["log", f"--format={_LOG_SHA_BODY_FMT}"]
    if single:
        args.extend(["-1", rev_spec])
    else:
        args.append(rev_spec)
    logger.debug("git_log_sha_body rev_spec=%r single=%s", rev_spec, single)
    return git_out(*args, cwd=cwd)


def _parse_rs_metadata_records(stdout: str) -> list[tuple[str, str, str, str]]:
    """Parse git log output from stack_commits_metadata_one_log format."""
    parts = stdout.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    out: list[tuple[str, str, str, str]] = []
    for i in range(0, len(parts), 4):
        if i + 3 >= len(parts):
            break
        a, b, c, d = (
            parts[i].strip(),
            parts[i + 1].strip(),
            parts[i + 2].strip(),
            parts[i + 3].strip(),
        )
        out.append((a, b, c, d))
    return out


def stack_commits_metadata_one_log(
    cwd: Path | str | None,
    rev_range: str,
) -> list[tuple[str, str, str, str]]:
    """
    Oldest-first commits in rev_range (e.g. 'merge_base..HEAD').

    One ``git log`` call. Each tuple is (full_sha, short_sha, subject, raw_message)
    where raw_message is the same as ``git log -1 --format=%B`` for that commit.
    """
    # Git expands %x1e to ASCII RS; keeps argv free of NUL (required on Windows).
    fmt = "%H%x1e%h%x1e%s%x1e%B%x1e"
    p = git(
        "log",
        "--reverse",
        rev_range,
        f"--format={fmt}",
        cwd=cwd,
        check=False,
    )
    if p.returncode != 0:
        return []
    return _parse_rs_metadata_records(p.stdout)


def stack_shas_and_subjects_one_log(
    cwd: Path | str | None,
    merge_base: str,
    *,
    head: str = "HEAD",
    branch: str | None = None,
) -> tuple[list[str], list[str]]:
    """
    Oldest-first SHAs and subject lines for merge_base..head using one git log.
    When ``head`` is ``HEAD``, reuses :func:`get_stack_snapshot` when merge_base matches.
    """
    if head == "HEAD":
        snap = get_stack_snapshot(cwd, branch)
        if snap.merge_base == merge_base:
            shas = [r[0] for r in snap.rows]
            subjects = [r[2] for r in snap.rows]
            return shas, subjects
    rows = stack_commits_metadata_one_log(cwd, f"{merge_base}..{head}")
    shas = [r[0] for r in rows]
    subjects = [r[2] for r in rows]
    return shas, subjects


def commit_subject_and_body(cwd: Path | str | None, sha: str) -> tuple[str, str]:
    """Return ``(first_line_subject, full_message_body)`` for *sha*."""
    raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    lines = raw.splitlines()
    sub = lines[0] if lines else ""
    return sub, raw


def _first_blocking_pattern(subject: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        try:
            if re.search(pat, subject):
                return pat
        except re.error:
            continue
    return None


def _ready_labels_for_stack(
    subjects: list[str],
    patterns: list[str],
) -> list[str]:
    """Bottom-up: first subject matching any pattern blocks that commit; later are after-blocked."""
    first_idx: int | None = None
    matched_pat: str | None = None
    for i, sub in enumerate(subjects):
        mp = _first_blocking_pattern(sub, patterns)
        if mp is not None:
            first_idx = i
            matched_pat = mp
            break
    out: list[str] = []
    for i, _sub in enumerate(subjects):
        if first_idx is None:
            out.append("ready")
        elif i < first_idx:
            out.append("ready")
        elif i == first_idx:
            out.append(f"blocked({matched_pat})")
        else:
            out.append("after-blocked")
    return out


def build_stack(
    cwd: Path | str | None,
    *,
    branch: str | None = None,
    with_ready_state: bool = False,
    patterns: list[str] | None = None,
) -> tuple[str, str, str, list[StackCommit]]:
    """Return merge-base, target label, and :class:`StackCommit` rows (optional per-commit ready/blocked labels)."""
    snap = get_stack_snapshot(cwd, branch)
    mb = snap.merge_base
    target_display = snap.target_display
    pats = patterns if patterns is not None else stop_patterns(cwd)
    rows = list(snap.rows)
    subjects = [r[2] for r in rows]
    labels = _ready_labels_for_stack(subjects, pats) if with_ready_state else ["ready"] * len(rows)
    commits: list[StackCommit] = []
    for i, (sha, short, sub, raw) in enumerate(rows, start=1):
        cid = parse_change_id(raw)
        st = labels[i - 1]
        commits.append(
            StackCommit(
                index=i,
                sha=sha,
                short_sha=short,
                subject=sub,
                change_id=cid,
                ready_state=st,
            )
        )
    return mb, target_display, target_display, commits


def is_ancestor(cwd: Path | str | None, maybe_desc: str, maybe_anc: str) -> bool:
    """Return True if *maybe_anc* is an ancestor of *maybe_desc*."""
    p = git("merge-base", "--is-ancestor", maybe_anc, maybe_desc, cwd=cwd, check=False)
    return p.returncode == 0


def resolve_stack_commit(
    cwd: Path | str | None,
    ref: str,
    *,
    branch: str | None = None,
) -> str:
    """Resolve *ref* to a full SHA, or map a Change-Id to the unique commit on the current stack."""
    s = ref.strip()
    if CHANGE_ID_VALUE_RE.match(s):
        snap = get_stack_snapshot(cwd, branch)
        want = s.lower()
        matches: list[tuple[str, str]] = []
        for sha, short, _sub, raw in snap.rows:
            cid = parse_change_id(raw)
            if cid and cid.lower() == want:
                matches.append((sha, short))
        if not matches:
            raise GitError(f"no commit in current stack with Change-Id {s}")
        if len(matches) > 1:
            shorts = [m[1] for m in matches]
            raise GitError(f"ambiguous Change-Id {s} in stack ({', '.join(shorts)})")
        return matches[0][0]
    return git_out("rev-parse", s, cwd=cwd)


def commit_in_stack(
    cwd: Path | str | None,
    commit: str,
    *,
    branch: str | None = None,
) -> bool:
    """True if commit is in merge_base..HEAD stack."""
    try:
        snap = get_stack_snapshot(cwd, branch)
    except GitError:
        return False
    c = resolve_stack_commit(cwd, commit, branch=branch)
    stack_shas = [r[0] for r in snap.rows]
    return c in stack_shas
