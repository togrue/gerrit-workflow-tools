"""Shared helpers for integration tests."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.reviewer import account_slug_from_gerrit, reviewer_accounts_from_change_info
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.gerrit_seed import configure_ger_git_repo, set_origin_url
from tests.integration.repo_builder import install_commit_msg_hook, prepare_worktree_clone


@dataclass(frozen=True)
class ChainCommit:
    """One local commit from an integration topic chain (oldest first)."""

    sha: str
    subject: str
    change_id: str
    chain_index: int


def chain_commits_oldest_first(repo: Path) -> list[ChainCommit]:
    """Return pushed-ready commits on the current stack, oldest first."""
    _fork, _, target_tip = merge_base_with_target(repo)
    rows = commits_in_range(repo, f"{target_tip}..HEAD")
    out: list[ChainCommit] = []
    for i, row in enumerate(rows):
        if not row.change_id:
            continue
        out.append(
            ChainCommit(
                sha=row.sha,
                subject=row.subject.strip(),
                change_id=row.change_id,
                chain_index=i,
            )
        )
    return out


def add_change_reviewer(session: GerritHttpSession, change_id: str, reviewer: str) -> None:
    """Add *reviewer* to a change via REST (same endpoint as lazy push strategy)."""
    enc = quote_change_id(change_id)
    session.post_json(f"changes/{enc}/reviewers", body={"reviewer": reviewer})


def clear_change_reviewers(session: GerritHttpSession, change_id: str) -> None:
    """Remove all REVIEWER/CC accounts from a change."""
    enc = quote_change_id(change_id)
    rows = session.get_json(f"changes/{enc}/reviewers/")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = row.get("_account_id")
        if isinstance(account_id, int):
            session.delete(f"changes/{enc}/reviewers/{account_id}")


def abandon_change(session: GerritHttpSession, change_id: str) -> None:
    """Abandon an open change."""
    enc = quote_change_id(change_id)
    session.post_json(f"changes/{enc}/abandon", body={})


def post_unresolved_inline_comment(
    session: GerritHttpSession,
    change_id: str,
    file_path: str,
    line: int,
    message: str,
) -> None:
    """Post one unresolved inline review comment on the current revision."""
    enc = quote_change_id(change_id)
    session.post_json(
        f"changes/{enc}/revisions/current/review",
        body={
            "comments": {
                file_path: [
                    {
                        "line": line,
                        "message": message,
                        "unresolved": True,
                    }
                ]
            }
        },
    )


def parse_trailing_attention_labels(log_text: str, subject_tag: str) -> list[str] | None:
    """
    Parse trailing ``# …`` attention tokens from a ``ger log`` text line containing *subject_tag*.

    Returns ``None`` when no matching line exists, or an empty list when the line has no trailing label.
    """
    marker = f"# {subject_tag}"
    for line in log_text.splitlines():
        plain = line.replace("\u0336", "")
        idx = plain.find(marker)
        if idx == -1:
            continue
        after_marker = plain[idx + len(marker) :]
        if after_marker and not after_marker[0].isspace():
            continue
        match = re.search(r"\s+#\s+(.+)$", after_marker)
        if not match:
            return []
        return [part.strip() for part in match.group(1).split(",")]
    return None


def open_changes_on_branch(session: GerritHttpSession, project: str, branch: str) -> list[dict[str, Any]]:
    """Return open changes for *project* and *branch* (newest first)."""
    q = f"project:{project} branch:{branch} is:open"
    data = session.get_json("changes/", params=[("q", q), ("n", "100")])
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def reviewer_slugs_on_change(detail: dict[str, Any]) -> list[str]:
    """REVIEWER/CC account usernames from a Gerrit ``.../detail`` payload (same rules as core)."""

    return [a.slug for a in reviewer_accounts_from_change_info(detail)]


def reviewer_slugs_from_reviewers_rest(session: GerritHttpSession, change_id: str) -> list[str]:
    """Account slugs from a live ``GET changes/<id>/reviewers/`` (Gerrit reviewer list endpoint)."""

    enc = quote_change_id(change_id)
    data = session.get_json(f"changes/{enc}/reviewers/")
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        slug = account_slug_from_gerrit(row)
        if slug:
            out.append(slug)
    return out


def label_value(detail: dict[str, Any], name: str) -> int | None:
    labels = detail.get("labels")
    if not isinstance(labels, dict):
        return None
    lab = labels.get(name)
    if not isinstance(lab, dict):
        return None
    v = lab.get("value")
    if v is None:
        # Gerrit 3.14+ puts applied votes under labels.<name>.all[*].value
        all_votes = lab.get("all")
        if isinstance(all_votes, list):
            for item in reversed(all_votes):
                if not isinstance(item, dict):
                    continue
                av = item.get("value")
                if isinstance(av, int):
                    return av
                try:
                    return int(av)
                except (TypeError, ValueError):
                    continue
        # Gerrit may omit explicit vote entries for "no score"; treat as 0 when
        # the label definition exists but no vote value is present.
        if "values" in lab:
            return 0
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def first_change_id_from_tip(session: GerritHttpSession, project: str, branch: str) -> str | None:
    rows = open_changes_on_branch(session, project, branch)
    if not rows:
        return None
    cid = rows[0].get("change_id") or rows[0].get("id")
    return str(cid) if cid else None


def prepare_topic_repo(
    ctx: Any,
    tmp_path: Path,
    topic: str,
    *,
    use_verified_project: bool = True,
) -> Path:
    """
    Copy the seeded template, create a unique topic branch from ``main``, push it, and configure ``ger``.
    """
    proj = ctx.project_verified if use_verified_project else ctx.project_plain
    seed = ctx.seed_repo_verified if use_verified_project else ctx.seed_repo_plain
    dest = tmp_path / f"wk_{topic}"
    prepare_worktree_clone(
        source_seed_repo=seed,
        dest=dest,
        branch="main",
        http_base=ctx.http_base,
        project=proj,
        git_user=ctx.dev_user,
        git_password=ctx.dev_password,
    )
    p_main = git("rev-parse", "--verify", "origin/main", cwd=dest, check=False)
    base = "origin/main" if p_main.returncode == 0 else "origin/master"
    git("checkout", "-b", topic, base, cwd=dest)
    try:
        git("push", "-u", "origin", topic, cwd=dest)
    except GitError as e:
        # Some Gerrit setups don't grant the test user branch-create rights.
        # Create the topic branch with admin credentials, then restore dev credentials
        # so the actual test pushes still run as the dev user.
        err = (e.stderr or str(e)).lower()
        if "not permitted: create" not in err:
            raise
        set_origin_url(
            dest,
            http_base=ctx.http_base,
            user=ctx.admin_user,
            password=ctx.admin_password,
            project=proj,
        )
        git("push", "-u", "origin", topic, cwd=dest)
        set_origin_url(
            dest,
            http_base=ctx.http_base,
            user=ctx.dev_user,
            password=ctx.dev_password,
            project=proj,
        )
    install_commit_msg_hook(dest, http_base=ctx.http_base)
    configure_ger_git_repo(
        dest,
        web_base=ctx.http_base,
        gerrit_user=ctx.dev_user,
        gerrit_secret=ctx.dev_password,
        branch=topic,
    )
    return dest


def prepare_clone_at_branch(
    ctx: Any,
    tmp_path: Path,
    branch: str,
    workdir: str,
    *,
    use_verified_project: bool = True,
) -> Path:
    """Copy the seeded template, fetch, check out an existing remote *branch*, configure ``ger``."""
    proj = ctx.project_verified if use_verified_project else ctx.project_plain
    seed = ctx.seed_repo_verified if use_verified_project else ctx.seed_repo_plain
    dest = tmp_path / workdir
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(seed, dest, symlinks=True)
    set_origin_url(
        dest,
        http_base=ctx.http_base,
        user=ctx.dev_user,
        password=ctx.dev_password,
        project=proj,
    )
    git("fetch", "origin", cwd=dest)
    p = git("checkout", branch, cwd=dest, check=False)
    if p.returncode != 0:
        git("checkout", "-b", branch, f"origin/{branch}", cwd=dest)
    git("config", "user.name", "Dev User", cwd=dest)
    git("config", "user.email", f"{ctx.dev_user}@example.com", cwd=dest)
    install_commit_msg_hook(dest, http_base=ctx.http_base)
    configure_ger_git_repo(
        dest,
        web_base=ctx.http_base,
        gerrit_user=ctx.dev_user,
        gerrit_secret=ctx.dev_password,
        branch=branch,
    )
    return dest
