"""Stack inspection helpers for commit ranges and ancestry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.change_id import parse_change_id_footer
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.git_state import current_branch

logger = logging.getLogger(__name__)


def upstream_tracking_tip_and_display(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str]:
    """
    Return ``(upstream_tip_sha, display_name)`` for the branch's **upstream** only.

    The local stack is ``<sha>..HEAD`` (same *sha* as the first element here).
    """
    b = branch or current_branch(cwd)
    upstream_sym = f"{b}@{{upstream}}" if branch else "@{upstream}"
    upstream_name = git("rev-parse", "--abbrev-ref", upstream_sym, cwd=cwd, check=False)
    if upstream_name.returncode != 0:
        raise GitError(
            f"No upstream configured for branch {b!r}.\n"
            "Set an upstream, e.g.:\n"
            "  git branch --set-upstream-to=<remote>/<branch>\n"
            "Fetch from your Gerrit remote first if the tracking branch is missing."
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
    """The footer value exactly as written — not necessarily a valid Gerrit id.

    Kept raw so Change-Id validation can report a malformed footer as malformed rather
    than as missing. Anything about to send this to Gerrit should validate first (see
    :func:`~gerrit_workflow_tools.core.change_id.extract_valid_change_id`)."""


@dataclass(frozen=True)
class StackSnapshot:
    """Upstream tracking tip + ``upstream_tip..HEAD`` commits (one ``git log``)."""

    upstream_tip: str
    commits: tuple[Commit, ...]


def get_stack_snapshot(cwd: Path | str | None, branch: str | None = None) -> StackSnapshot:
    """Return the upstream tip SHA and oldest-first ``upstream_tip..HEAD`` commits."""
    upstream_tip, _display = upstream_tracking_tip_and_display(cwd, branch)
    rows_list = commits_in_range(cwd, f"{upstream_tip}..HEAD")
    return StackSnapshot(
        upstream_tip=upstream_tip,
        commits=tuple(rows_list),
    )


def merge_base_with_target(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    head: str = "HEAD",
) -> tuple[str, str, str]:
    """
    Return ``(rebase_fork, upstream_display, upstream_tip_sha)``.

    *upstream_tip_sha* is ``git rev-parse`` of the branch's ``@{upstream}``; the default
    local stack is ``upstream_tip_sha..head`` (``HEAD`` when *head* is omitted).

    *rebase_fork* is ``merge-base(head, upstream_tip_sha)`` — the onto point for
    ``git rebase -i <fork>`` (not the same commit as *upstream_tip_sha* when histories diverge).
    """
    upstream_tip, display = upstream_tracking_tip_and_display(cwd, branch)
    rebase_fork = git_out("merge-base", head, upstream_tip, cwd=cwd)
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
    if ".." in input_arg:
        idx = input_arg.index("..")
        right = input_arg[idx + 2 :].strip() or "HEAD"
        logger.debug("rev_spec_stack_base_to_end rev-parse %r (range right)", right)
    else:
        right = input_arg
        logger.debug("rev_spec_stack_base_to_end rev-parse %r (end ref)", right)
    end = git_out("rev-parse", right, cwd=cwd)
    return f"{upstream_tip}..{end}"


def rev_spec_target_tip_to_end(cwd: Path | str | None, input_arg: str) -> str:
    """Backward-compatible alias for :func:`rev_spec_stack_base_to_end` (upstream-based stack base)."""
    return rev_spec_stack_base_to_end(cwd, input_arg)


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
    it = iter(parts)
    return [(sha.strip(), msg) for sha, msg in zip(it, it, strict=False)]


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
    it = iter(parts)
    return [
        Commit(
            sha=sha.strip(),
            short_sha=short_s.strip(),
            subject=subj.strip(),
            body=body.strip(),
            change_id=parse_change_id_footer(body.strip()),
        )
        for sha, short_s, subj, body in zip(it, it, it, it, strict=False)
    ]


def commits_in_range(
    cwd: Path | str | None,
    rev_range: str,
    *,
    first_parent: bool = False,
) -> list[Commit]:
    """
    Oldest-first commits in *rev_range* (e.g. ``upstream_tip..HEAD``).

    One ``git log`` call.  Pass *first_parent=True* to restrict traversal to
    first-parent edges only (i.e. ignore commits reachable via merge parents).
    """
    # Git expands %x1e to ASCII RS; keeps argv free of NUL (required on Windows).
    fmt = "%H%x1e%h%x1e%s%x1e%B%x1e"
    extra: list[str] = ["--first-parent"] if first_parent else []
    p = git(
        "log",
        "--reverse",
        *extra,
        rev_range,
        f"--format={fmt}",
        cwd=cwd,
        check=False,
    )
    if p.returncode != 0:
        raise GitError("git log failed", stderr=p.stderr, returncode=p.returncode)
    return _parse_rs_metadata_records(p.stdout)


def resolve_stack_commit(
    cwd: Path | str | None,
    ref: str,
    *,
    settings: Settings,
    branch: str | None = None,
    client: GerritRest | None = None,
) -> str:
    """Resolve *ref* to a full SHA, or map a changeish to the unique commit on the current stack."""
    from gerrit_workflow_tools.core.gerrit.change_resolution import (
        ChangeAmbiguousError,
        ChangeResolutionError,
        classify_changeish,
        resolve_to_stack_sha,
    )

    s = ref.strip()
    kind = classify_changeish(s)
    if kind in ("change-id", "triplet", "change-number", "change-ref", "url", "query"):
        try:
            full = resolve_to_stack_sha(s, cwd=cwd, settings=settings, branch=branch, client=client)
        except ChangeAmbiguousError as e:
            raise GitError(str(e)) from e
        except ChangeResolutionError as e:
            raise GitError(str(e)) from e
        logger.debug("resolve_stack_commit: changeish %r -> %s", s, full[:8])
        return full
    full = git_out("rev-parse", s, cwd=cwd)
    logger.debug("resolve_stack_commit: ref %r -> %s", s, full[:8])
    return full


def commit_in_stack(
    cwd: Path | str | None,
    commit: str,
    *,
    settings: Settings,
    branch: str | None = None,
) -> bool:
    """True if commit is in the default ``upstream_tip..HEAD`` stack."""
    try:
        snap = get_stack_snapshot(cwd, branch)
    except GitError:
        return False
    c = resolve_stack_commit(cwd, commit, settings=settings, branch=branch)
    return c in {x.sha for x in snap.commits}
