from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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


def commit_subject_and_body(cwd: Path | str | None, sha: str) -> tuple[str, str]:
    sub = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
    body = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    return sub, body


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
    shas = list_stack_commits(cwd, mb)
    subjects: list[str] = []
    bodies: list[str] = []
    for sha in shas:
        sub, body = commit_subject_and_body(cwd, sha)
        subjects.append(sub)
        bodies.append(body)
    labels = (
        _ready_labels_for_stack(subjects, pats)
        if with_ready_state
        else ["ready"] * len(shas)
    )
    commits: list[StackCommit] = []
    for i, sha in enumerate(shas, start=1):
        sub = subjects[i - 1]
        body = bodies[i - 1]
        cid = parse_change_id(body)
        st = labels[i - 1]
        commits.append(
            StackCommit(
                index=i,
                sha=sha,
                short_sha=git_out("rev-parse", "--short", sha, cwd=cwd),
                subject=sub,
                change_id=cid,
                ready_state=st,
            )
        )
    return mb, target_display, target_display, commits


def is_ancestor(cwd: Path | str | None, maybe_desc: str, maybe_anc: str) -> bool:
    p = git("merge-base", "--is-ancestor", maybe_anc, maybe_desc, cwd=cwd, check=False)
    return p.returncode == 0


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
    c = git_out("rev-parse", commit, cwd=cwd)
    stack = list_stack_commits(cwd, mb)
    return c in stack
