"""Shared helpers for integration tests."""

from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.reviewer import account_slug_from_gerrit, reviewer_accounts_from_change_info
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.gerrit_seed import configure_ger_git_repo, set_origin_url
from tests.integration.profile import phase
from tests.integration.repo_builder import build_linear_chain, install_commit_msg_hook, prepare_worktree_clone


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


def resolve_unresolved_inline_comments(
    session: GerritHttpSession,
    change_id: str,
    *,
    message: str = "done",
) -> int:
    """Reply ``unresolved: false`` to every unresolved inline comment. Return how many."""

    enc = quote_change_id(change_id)
    file_map = session.get_json(f"changes/{enc}/comments")
    if not isinstance(file_map, dict):
        return 0
    resolved = 0
    for path, rows in file_map.items():
        if not isinstance(rows, list):
            continue
        for comment in rows:
            if not isinstance(comment, dict) or comment.get("unresolved") is not True:
                continue
            reply: dict[str, Any] = {
                "message": message,
                "in_reply_to": comment.get("id"),
                "unresolved": False,
            }
            line = comment.get("line")
            if isinstance(line, int):
                reply["line"] = line
            rng = comment.get("range")
            if isinstance(rng, dict):
                reply["range"] = rng
            side = comment.get("side")
            if isinstance(side, str):
                reply["side"] = side
            session.post_json(
                f"changes/{enc}/revisions/current/review",
                body={"comments": {path: [reply]}},
            )
            resolved += 1
    return resolved


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
                if av is None:
                    continue
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
    with phase(f"prepare_topic_repo({topic})"):
        return _prepare_topic_repo_impl(
            ctx=ctx,
            tmp_path=tmp_path,
            topic=topic,
            proj=proj,
            seed=seed,
            dest=dest,
        )


def _prepare_topic_repo_impl(
    *,
    ctx: Any,
    tmp_path: Path,
    topic: str,
    proj: str,
    seed: Path,
    dest: Path,
) -> Path:
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


def run_ger(
    repo: Path, main_fn: Callable[..., int], argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, str, str]:
    """Run a ``ger`` command in *repo* against the real Gerrit; ``(code, stdout, stderr)``."""
    return run_cli(repo, main_fn, argv, monkeypatch, catch_sys_exit=True)


def ger_json(repo: Path, main_fn: Callable[..., int], argv: list[str], monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run ``ger <command> --json …`` and parse stdout; attention (exit 1) is not a failure."""
    code, out, err = run_ger(repo, main_fn, ["--json", *argv], monkeypatch)
    assert code in (0, 1), f"exit {code}: {err}"
    return json.loads(out)


def ger_push(repo: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[str, str]:
    """``ger push --yes --no-rebase-check …`` that must succeed; ``(stdout, stderr)``."""
    from gerrit_workflow_tools.cli_push import main as ger_push_main  # pylint: disable=import-outside-toplevel

    code, out, err = run_ger(repo, ger_push_main, ["--yes", "--no-rebase-check", *argv], monkeypatch)
    assert code == 0, f"ger push exited {code}: {out}\n{err}"
    return out, err


def log_commits_by_change_id(repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """``ger log --json`` commits keyed by Change-Id."""
    from gerrit_workflow_tools.cli_log import main as ger_log_main  # pylint: disable=import-outside-toplevel

    commits = ger_json(repo, ger_log_main, [], monkeypatch)["commits"]
    return {c["change_id"]: c for c in commits if c.get("change_id")}


def change_detail(session: GerritHttpSession, change: str) -> dict[str, Any]:
    """``GET changes/<change>`` with the current revision, labels and reviewers."""
    params = [("o", o) for o in ("CURRENT_REVISION", "DETAILED_LABELS", "DETAILED_ACCOUNTS")]
    return dict(session.get_json(f"changes/{quote_change_id(change)}", params=params))


def current_patchset(session: GerritHttpSession, change: str) -> int:
    """The current patchset number of *change* (a triplet, number or unique Change-Id)."""
    detail = change_detail(session, change)
    return int(detail["revisions"][detail["current_revision"]]["_number"])


def submit_change(session: GerritHttpSession, change: str) -> None:
    """Approve and submit *change* as the session's user (Code-Review+2 is all the project needs)."""
    enc = quote_change_id(change)
    session.post_json(f"changes/{enc}/revisions/current/review", body={"labels": {"Code-Review": 2}})
    session.post_json(f"changes/{enc}/submit", body={})


def publish_web_edit(session: GerritHttpSession, change: str, path: str, content: str) -> None:
    """Edit *path* in Gerrit's web editor and publish it: a new patchset no clone has."""
    enc = quote_change_id(change)
    data = base64.b64encode(content.encode()).decode()
    session.put_json(f"changes/{enc}/edit/{quote(path, safe='')}", body={"binary_content": f"data:;base64,{data}"})
    session.post_json(f"changes/{enc}/edit:publish", body={"notify": "NONE"})


def rebase_change_on_gerrit(session: GerritHttpSession, change: str) -> None:
    """Press Gerrit's Rebase button: the change moves onto its parent's current patchset."""
    session.post_json(f"changes/{quote_change_id(change)}/rebase", body={})


@dataclass(frozen=True)
class SiblingChanges:
    """One commit pushed to ``main`` and cherry-picked to ``dev``: one Change-Id, two changes.

    *repo* has ``feat_main`` (pushing to main) and ``feat_dev`` (pushing to dev), each holding
    its own copy of the commit; ``feat_main`` is checked out.
    """

    repo: Path
    project: str
    change_id: str
    main: dict[str, Any]
    dev: dict[str, Any]

    def triplet(self, branch: str) -> str:
        return f"{self.project}~{branch}~{self.change_id}"


def push_sibling_changes(
    ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, workdir: str = "siblings"
) -> SiblingChanges:
    """Push one commit to ``main`` and its cherry-pick to ``dev`` with ``ger push``."""
    repo = prepare_clone_at_branch(ctx, tmp_path, "main", workdir)
    git("checkout", "-q", "-b", "feat_main", "origin/main", cwd=repo)
    build_linear_chain(repo, [f"sibling change {secrets.token_hex(4)}"])
    sha = git_out("rev-parse", "HEAD", cwd=repo)
    change_id = commits_in_range(repo, "HEAD~1..HEAD")[0].change_id
    assert change_id
    ger_push(repo, monkeypatch)
    git("checkout", "-q", "-b", "feat_dev", "origin/dev", cwd=repo)
    git("cherry-pick", sha, cwd=repo)
    ger_push(repo, monkeypatch)
    git("checkout", "-q", "feat_main", cwd=repo)

    session = GerritHttpSession(ctx.http_base, user=ctx.admin_user, password=ctx.admin_password)
    rows = session.get_json("changes/", params=[("q", f"project:{ctx.project_verified} change:{change_id}")])
    by_branch = {row["branch"]: row for row in rows}
    assert set(by_branch) == {"main", "dev"}, rows
    return SiblingChanges(repo, ctx.project_verified, change_id, by_branch["main"], by_branch["dev"])


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
    p = git("checkout", branch, cwd=dest, check=False)
    if p.returncode != 0:
        p2 = git("checkout", "-b", branch, f"origin/{branch}", cwd=dest, check=False)
        if p2.returncode != 0:
            git("checkout", "-b", branch, branch, cwd=dest)
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
