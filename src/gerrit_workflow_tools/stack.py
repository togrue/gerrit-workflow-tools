from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.config import current_branch
from gerrit_workflow_tools.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)


def upstream_tracking_tip_and_display(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str]:
    """
    Return ``(upstream_tip_sha, display_name)`` for the branch's **upstream** only.

    The local stack is ``<sha>..HEAD`` (same *sha* as the first element here).
    This does not consult ``branch.*.gerritTarget``; use :func:`resolve_local_base_ref`
    in ``config`` when you need the configured Gerrit push destination tip instead.
    """
    b = branch or current_branch(cwd)
    upstream_sym = f"{b}@{{upstream}}" if branch else "@{upstream}"
    upstream_name = git("rev-parse", "--abbrev-ref", upstream_sym, cwd=cwd, check=False)
    if upstream_name.returncode != 0:
        raise GitError(
            f"No upstream configured for branch {b!r}.\n"
            "Set an upstream, e.g.:\n"
            "  git branch --set-upstream-to=<remote>/<branch>\n"
            "Or infer the nearest remote-tracking branch and set upstream:\n"
            "  ger branch infer-upstream\n"
            "Optional per-branch Gerrit destination overrides: see `ger branch --help`."
        )
    display = upstream_name.stdout.strip()
    upstream_ref = git("rev-parse", "--verify", display, cwd=cwd, check=False)
    if upstream_ref.returncode != 0:
        raise GitError(
            f"Upstream {display!r} for branch {b!r} does not resolve to a ref. "
            f"Fetch from your remote so the tracking branch exists."
        )
    return (upstream_ref.stdout.strip(), display)


@dataclass(frozen=True)
class Commit:
    """One commit from a ``git log`` metadata line (subject + full message body)."""

    sha: str
    short_sha: str
    subject: str
    body: str
    change_id: str | None


@dataclass(frozen=True)
class StackSnapshot:
    """Upstream tracking tip + ``upstream_tip..HEAD`` commits (one ``git log``)."""

    upstream_tip: str
    upstream_display: str
    commits: tuple[Commit, ...]


def get_stack_snapshot(cwd: Path | str | None, branch: str | None = None) -> StackSnapshot:
    """Return the upstream tip SHA, display name, and oldest-first ``upstream_tip..HEAD`` commits."""
    upstream_tip, display = upstream_tracking_tip_and_display(cwd, branch)
    rows_list = commits_in_range(cwd, f"{upstream_tip}..HEAD")
    return StackSnapshot(
        upstream_tip=upstream_tip,
        upstream_display=display,
        commits=tuple(rows_list),
    )


CHANGE_ID_RE = re.compile(r"^Change-Id:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_change_id(message: str) -> str | None:
    """Extract ``Change-Id: …`` from a commit message body, or return None."""
    m = CHANGE_ID_RE.search(message)
    return m.group(1) if m else None


def merge_base_with_target(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str, str]:
    """
    Return ``(rebase_fork, upstream_display, upstream_tip_sha)``.

    *upstream_tip_sha* is ``git rev-parse`` of the branch's ``@{upstream}``; the default
    local stack is ``upstream_tip_sha..HEAD``.

    *rebase_fork* is ``merge-base(HEAD, upstream_tip_sha)`` — the onto point for
    ``git rebase -i <fork>`` (not the same commit as *upstream_tip_sha* when histories diverge).
    """
    upstream_tip, display = upstream_tracking_tip_and_display(cwd, branch)
    rebase_fork = git_out("merge-base", "HEAD", upstream_tip, cwd=cwd)
    logger.debug(
        "merge_base_with_target: rebase_fork=%s upstream_display=%r upstream_tip=%r",
        rebase_fork[:8],
        display,
        upstream_tip[:8],
    )
    return rebase_fork, display, upstream_tip


def rev_spec_stack_base_to_end(cwd: Path | str | None, input_arg: str, branch: str | None = None) -> str:
    """``upstream_tip..END`` where END is *input_arg* or the right side of ``left..right``."""
    _fork, _display, upstream_tip = merge_base_with_target(cwd, branch)
    if ".." not in input_arg:
        logger.debug("rev_spec_stack_base_to_end rev-parse %r (end ref)", input_arg)
        end = git_out("rev-parse", input_arg, cwd=cwd)
        return f"{upstream_tip}..{end}"
    idx = input_arg.index("..")
    right = input_arg[idx + 2 :].strip() or "HEAD"
    logger.debug("rev_spec_stack_base_to_end rev-parse %r (range right)", right)
    end = git_out("rev-parse", right, cwd=cwd)
    return f"{upstream_tip}..{end}"


def rev_spec_target_tip_to_end(cwd: Path | str | None, input_arg: str) -> str:
    """Backward-compatible alias for :func:`rev_spec_stack_base_to_end` (upstream-based stack base)."""
    return rev_spec_stack_base_to_end(cwd, input_arg)


def list_stack_commits(
    cwd: Path | str | None,
    start_exclusive: str,
    *,
    head: str = "HEAD",
) -> list[str]:
    """Oldest-first SHAs in ``start_exclusive..head``."""
    return rev_list_reverse(cwd, start_exclusive, head)


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
# Git expands %x1e in --format; same RS style as :func:`commits_in_range`.
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


def _parse_rs_metadata_records(stdout: str) -> list[Commit]:
    """Parse git log output from :func:`commits_in_range` format into :class:`Commit` rows."""
    parts = stdout.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    out: list[Commit] = []
    for i in range(0, len(parts), 4):
        if i + 3 >= len(parts):
            break
        sha, short_s, subj, body = (
            parts[i].strip(),
            parts[i + 1].strip(),
            parts[i + 2].strip(),
            parts[i + 3].strip(),
        )
        out.append(
            Commit(
                sha=sha,
                short_sha=short_s,
                subject=subj,
                body=body,
                change_id=parse_change_id(body),
            )
        )
    return out


def commits_in_range(
    cwd: Path | str | None,
    rev_range: str,
) -> list[Commit]:
    """
    Oldest-first commits in *rev_range* (e.g. ``upstream_tip..HEAD``).

    One ``git log`` call.
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
        raise GitError("git log failed", stderr=p.stderr, returncode=p.returncode)
    return _parse_rs_metadata_records(p.stdout)


def commit_subject_and_body(cwd: Path | str | None, sha: str) -> tuple[str, str]:
    """Return ``(first_line_subject, full_message_body)`` for *sha*."""
    raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    lines = raw.splitlines()
    sub = lines[0] if lines else ""
    return sub, raw


def is_ancestor(cwd: Path | str | None, maybe_desc: str, maybe_anc: str) -> bool:
    """Return True if *maybe_anc* is an ancestor of *maybe_desc*."""
    p = git("merge-base", "--is-ancestor", maybe_anc, maybe_desc, cwd=cwd, check=False)
    return p.returncode == 0


def resolve_stack_commit(
    cwd: Path | str | None,
    ref: str,
    *,
    branch: str | None = None,
    _snap: StackSnapshot | None = None,
) -> str:
    """Resolve *ref* to a full SHA, or map a Change-Id to the unique commit on the current stack."""
    s = ref.strip()
    if CHANGE_ID_VALUE_RE.match(s):
        snap = _snap or get_stack_snapshot(cwd, branch)
        want = s.lower()
        matches: list[tuple[str, str]] = []
        for c in snap.commits:
            cid = c.change_id
            if cid and cid.lower() == want:
                matches.append((c.sha, c.short_sha))
        if not matches:
            raise GitError(f"no commit in current stack with Change-Id {s}")
        if len(matches) > 1:
            shorts = [m[1] for m in matches]
            raise GitError(f"ambiguous Change-Id {s} in stack ({', '.join(shorts)})")
        logger.debug("resolve_stack_commit: Change-Id %s -> %s", s, matches[0][0][:8])
        return matches[0][0]
    full = git_out("rev-parse", s, cwd=cwd)
    logger.debug("resolve_stack_commit: ref %r -> %s", s, full[:8])
    return full


def commit_in_stack(
    cwd: Path | str | None,
    commit: str,
    *,
    branch: str | None = None,
) -> bool:
    """True if commit is in the default ``upstream_tip..HEAD`` stack."""
    try:
        snap = get_stack_snapshot(cwd, branch)
    except GitError:
        return False
    c = resolve_stack_commit(cwd, commit, branch=branch, _snap=snap)
    stack_shas = [x.sha for x in snap.commits]
    return c in stack_shas


def current_stack_shas(cwd: Path | str | None, branch: str | None = None) -> list[str]:
    """Full SHAs in the local stack (``@{upstream}..HEAD``), oldest first."""
    return [c.sha for c in get_stack_snapshot(cwd, branch).commits]
