from __future__ import annotations

import re
from pathlib import Path

from gerrit_workflow_tools.git_run import git_out


def current_branch(cwd: Path | str | None) -> str:
    return git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)


def branch_gerrit_target(
    cwd: Path | str | None, branch: str | None = None
) -> str | None:
    b = branch or current_branch(cwd)
    key = f"branch.{b}.gerritTarget"
    return _config_get(cwd, key)


def branch_gerrit_reviewers(
    cwd: Path | str | None, branch: str | None = None
) -> str | None:
    b = branch or current_branch(cwd)
    return _config_get(cwd, f"branch.{b}.gerritReviewers")


def branch_gerrit_push_mode(
    cwd: Path | str | None, branch: str | None = None
) -> str | None:
    b = branch or current_branch(cwd)
    return _config_get(cwd, f"branch.{b}.gerritPushMode")


def default_push_mode(cwd: Path | str | None) -> str:
    v = _config_get(cwd, "gerrit.defaultPushMode")
    return v or "ready"


def gerrit_remote(cwd: Path | str | None) -> str:
    v = _config_get(cwd, "gerrit.remote")
    return v or "origin"


def stop_patterns(cwd: Path | str | None) -> list[str]:
    from gerrit_workflow_tools.git_run import git

    p = git("config", "--get-all", "gerrit.stopPattern", cwd=cwd, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return [r"^dropme!", r"^TODO\b", r"^test!"]
    lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
    return lines


def _config_get(cwd: Path | str | None, key: str) -> str | None:
    from gerrit_workflow_tools.git_run import git

    p = git("config", "--get", key, cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_target: str | None = None,
    gerrit_reviewers: str | None = None,
    gerrit_push_mode: str | None = None,
) -> None:
    from gerrit_workflow_tools.git_run import git

    if gerrit_target is not None:
        git("config", f"branch.{branch}.gerritTarget", gerrit_target, cwd=cwd)
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerritReviewers", gerrit_reviewers, cwd=cwd)
    if gerrit_push_mode is not None:
        git("config", f"branch.{branch}.gerritPushMode", gerrit_push_mode, cwd=cwd)


def set_global_gerrit(
    cwd: Path | str | None,
    *,
    remote: str | None = None,
    default_push_mode: str | None = None,
    stop_patterns: list[str] | None = None,
) -> None:
    from gerrit_workflow_tools.git_run import git

    if remote is not None:
        git("config", "gerrit.remote", remote, cwd=cwd)
    if default_push_mode is not None:
        git("config", "gerrit.defaultPushMode", default_push_mode, cwd=cwd)
    if stop_patterns is not None:
        git("config", "--unset-all", "gerrit.stopPattern", cwd=cwd, check=False)
        for pat in stop_patterns:
            git("config", "--add", "gerrit.stopPattern", pat, cwd=cwd)


def escape_branch_for_config(branch: str) -> str:
    """Quote branch name for use in git config section if needed."""
    if re.search(r'[\s"\\\[\]]', branch):
        return '"' + branch.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return branch


def resolve_local_base_ref(
    cwd: Path | str | None, branch: str | None = None
) -> tuple[str, str]:
    """
    Return (ref_for_merge_base, display_name) for merge-base, e.g. ('main', 'main').
    Order: branch.gerritTarget -> @{upstream} -> main -> master.
    """
    from gerrit_workflow_tools.git_run import GitError, git

    b = branch or current_branch(cwd)
    target = branch_gerrit_target(cwd, b)
    if target:
        p = git("rev-parse", "--verify", target, cwd=cwd, check=False)
        if p.returncode != 0:
            p = git(
                "rev-parse", "--verify", f"refs/heads/{target}", cwd=cwd, check=False
            )
        if p.returncode == 0:
            return (p.stdout.strip(), target)
        raise GitError(
            f"gerritTarget '{target}' is configured but ref not found locally. "
            f"Create branch '{target}' or fix branch.{b}.gerritTarget."
        )

    p = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    if p.returncode == 0:
        upstream = p.stdout.strip()
        if "/" in upstream:
            _remote, name = upstream.split("/", 1)
            p2 = git(
                "rev-parse", "--verify", f"refs/heads/{name}", cwd=cwd, check=False
            )
            if p2.returncode == 0:
                return (p2.stdout.strip(), name)
        p3 = git("rev-parse", "--verify", upstream, cwd=cwd, check=False)
        if p3.returncode == 0:
            return (p3.stdout.strip(), upstream)

    for name in ("main", "master"):
        p = git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{name}",
            cwd=cwd,
            check=False,
        )
        if p.returncode == 0:
            p2 = git("rev-parse", "--verify", f"refs/heads/{name}", cwd=cwd)
            return (p2.stdout.strip(), name)

    raise GitError(
        "No base branch: set branch.<name>.gerritTarget, configure @{upstream}, or create main/master."
    )
