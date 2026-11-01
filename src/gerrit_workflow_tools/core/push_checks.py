"""What a push looks at before it runs. Does not print or prompt."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.git_state import head_is_linear_on_remote_gerrit_target


_REBASE_HINT = "Hint: run `ger rebase --onto-remote` to replay your commits on top of the latest target branch."


def git_or_none(cwd: Path, *args: str) -> str | None:
    try:
        return git_out(*args, cwd=cwd)
    except GitError:
        return None


def rebase_check(
    cwd: Path,
    branch: str,
    *,
    policy: str,
    no_rebase_check: bool,
    settings: Settings,
) -> tuple[str | None, tuple[str, ...]]:
    if no_rebase_check or policy == "ignore-not-rebased":
        return None, ()
    remote_name = settings.gerrit_remote
    try:
        git("fetch", remote_name, cwd=cwd)
    except GitError as exc:
        return None, (f"warning: could not fetch `{remote_name}`; skipping remote rebase check: {exc}",)
    try:
        ok, onto = head_is_linear_on_remote_gerrit_target(cwd, branch, head=branch, settings=settings)
    except GitError as exc:
        return None, (f"warning: could not compare HEAD to remote target; skipping remote rebase check: {exc}",)
    if ok:
        return None, ()
    message = (
        "Local HEAD is not based directly on the current Gerrit target branch after fetch "
        f"({_rebase_detail(cwd, branch, onto)}). {_REBASE_HINT}"
    )
    if policy == "error-not-rebased":
        return f"error: {message}", ()
    return None, (f"warning: {message}",)


def _rebase_detail(cwd: Path, head: str, onto: str) -> str:
    short_onto = (
        git_or_none(cwd, "rev-parse", "--abbrev-ref", onto) or git_or_none(cwd, "rev-parse", "--short", onto) or onto
    )
    tip = git_or_none(cwd, "rev-parse", "--short", onto)
    fork = git_or_none(cwd, "merge-base", head, onto)
    fork_s = git_or_none(cwd, "rev-parse", "--short", fork) if fork else None
    if tip and fork_s:
        return (
            f"tip of `{short_onto}` ({tip}) is not an ancestor of HEAD "
            f"(fork at {fork_s}; rebase onto the fetched target to linearize)"
        )
    return f"tip of `{short_onto}` is not an ancestor of HEAD"
