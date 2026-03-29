from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.config import resolve_local_base_ref, stop_patterns
from gerrit_workflow_tools.git_run import GitError, git, git_out


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
    m = CHANGE_ID_RE.search(message)
    return m.group(1) if m else None


def merge_base_with_target(
    cwd: Path | str | None, branch: str | None = None
) -> tuple[str, str, str]:
    """
    Returns (merge_base_sha, target_display_name, base_ref_commit).
    base_ref_commit is the resolved ref used with merge-base (second argument).
    """
    base_ref, display = resolve_local_base_ref(cwd, branch)
    mb = git_out("merge-base", "HEAD", base_ref, cwd=cwd)
    return mb, display, base_ref


def list_stack_commits(
    cwd: Path | str | None,
    merge_base: str,
    *,
    head: str = "HEAD",
) -> list[str]:
    """Oldest-first SHAs from merge_base..head (exclusive..exclusive range)."""
    return rev_list_reverse(cwd, merge_base, head)


def rev_list_reverse(
    cwd: Path | str | None, start_exclusive: str, end_inclusive: str
) -> list[str]:
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


def _parse_rs_metadata_records(stdout: str) -> list[tuple[str, str, str, str]]:
    """Parse git log output from stack_commits_metadata_one_log format."""
    parts = stdout.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    out: list[tuple[str, str, str, str]] = []
    for i in range(0, len(parts), 4):
        if i + 3 >= len(parts):
            break
        a, b, c, d = (parts[i].strip(), parts[i + 1].strip(), parts[i + 2].strip(), parts[i + 3].strip())
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
) -> tuple[list[str], list[str]]:
    """
    Oldest-first SHAs and subject lines for merge_base..head using one git log.
    """
    rows = stack_commits_metadata_one_log(cwd, f"{merge_base}..{head}")
    shas = [r[0] for r in rows]
    subjects = [r[2] for r in rows]
    return shas, subjects


def commit_subject_and_body(cwd: Path | str | None, sha: str) -> tuple[str, str]:
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
    """
    Returns (merge_base_sha, target_display_name, target_display_name, commits).
    """
    mb, target_display, _base_ref = merge_base_with_target(cwd, branch)
    pats = patterns if patterns is not None else stop_patterns(cwd)
    rows = stack_commits_metadata_one_log(cwd, f"{mb}..HEAD")
    subjects = [r[2] for r in rows]
    labels = (
        _ready_labels_for_stack(subjects, pats)
        if with_ready_state
        else ["ready"] * len(rows)
    )
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
    p = git("merge-base", "--is-ancestor", maybe_anc, maybe_desc, cwd=cwd, check=False)
    return p.returncode == 0


def resolve_stack_commit(
    cwd: Path | str | None,
    ref: str,
    *,
    branch: str | None = None,
) -> str:
    """
    Resolve ref to a full SHA. If ref is a Gerrit Change-Id (I + 40 hex), find the
    unique commit in merge_base..HEAD whose message contains that Change-Id.
    """
    s = ref.strip()
    if CHANGE_ID_VALUE_RE.match(s):
        mb, _, _ = merge_base_with_target(cwd, branch)
        want = s.lower()
        matches: list[tuple[str, str]] = []
        for sha, short, _sub, raw in stack_commits_metadata_one_log(
            cwd, f"{mb}..HEAD"
        ):
            cid = parse_change_id(raw)
            if cid and cid.lower() == want:
                matches.append((sha, short))
        if not matches:
            raise GitError(f"no commit in current stack with Change-Id {s}")
        if len(matches) > 1:
            shorts = [m[1] for m in matches]
            raise GitError(
                f"ambiguous Change-Id {s} in stack ({', '.join(shorts)})"
            )
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
        mb, _, _ = merge_base_with_target(cwd, branch)
    except GitError:
        return False
    c = resolve_stack_commit(cwd, commit, branch=branch)
    stack = list_stack_commits(cwd, mb)
    return c in stack
