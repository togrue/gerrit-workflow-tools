"""Seed Gerrit projects, accounts, and branches for integration tests."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache, set_branch_config
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id

logger = logging.getLogger(__name__)


def create_account(session: GerritHttpSession, username: str, *, email: str, http_password: str) -> None:
    enc = quote(username, safe="")
    session.put_json(
        f"accounts/{enc}",
        body={
            "name": username,
            "email": email,
            "http_password": http_password,
        },
    )


def create_project(session: GerritHttpSession, name: str, *, parent: str = "All-Projects") -> None:
    enc = quote(name, safe="")
    session.put_json(
        f"projects/{enc}",
        body={
            "description": f"integration {name}",
            "parent": parent,
            "create_empty_commit": True,
        },
    )


def list_branches(session: GerritHttpSession, project: str) -> list[dict[str, object]]:
    enc = quote(project, safe="")
    data = session.get_json(f"projects/{enc}/branches/")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def delete_project(session: GerritHttpSession, name: str) -> None:
    enc = quote(name, safe="")
    try:
        session.delete(f"projects/{enc}")
    except RuntimeError:
        logger.debug("delete project %s failed (may not exist)", name)


def _http_remote_url(*, scheme: str, user: str, password: str, host: str, port: int, project: str) -> str:
    user_enc = quote(user, safe="")
    pw_enc = quote(password, safe="")
    host_part = f"{host}:{port}" if port not in (80, 443) else host
    return f"{scheme}://{user_enc}:{pw_enc}@{host_part}/{quote(project, safe='')}"


def _run_git(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    p = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=full_env,
        check=False,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (cwd={cwd}): {p.stderr or p.stdout}")


def set_origin_url(
    repo: Path,
    *,
    http_base: str,
    user: str,
    password: str,
    project: str,
) -> None:
    u = urlparse(http_base)
    host = u.hostname or "localhost"
    port = u.port or (443 if u.scheme == "https" else 80)
    scheme = u.scheme or "http"
    url = _http_remote_url(
        scheme=scheme,
        user=user,
        password=password,
        host=host,
        port=port,
        project=project,
    )
    _run_git(["remote", "set-url", "origin", url], cwd=repo)


def seed_repo_with_branches(
    *,
    work_root: Path,
    http_base: str,
    admin_user: str,
    admin_password: str,
    project: str,
    branches: tuple[str, ...] = ("main", "dev", "hotfix_123"),
) -> Path:
    """
    Clone *project* and create *branches* from the initial empty commit (idempotent push).
    """
    u = urlparse(http_base)
    host = u.hostname or "localhost"
    port = u.port or (443 if u.scheme == "https" else 80)
    scheme = u.scheme or "http"

    repo_dir = work_root / f"seed_{project.replace('/', '_')}"
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    clone_url = _http_remote_url(
        scheme=scheme,
        user=admin_user,
        password=admin_password,
        host=host,
        port=port,
        project=project,
    )
    _run_git(["clone", clone_url, str(repo_dir)], cwd=work_root)

    cur = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir).strip()
    tip = git_out("rev-parse", "HEAD", cwd=repo_dir).strip()

    # Prefer main as the primary integration name when create_empty_commit used master.
    if cur == "master" and "main" in branches:
        _run_git(["branch", "-m", "master", "main"], cwd=repo_dir)
        cur = "main"

    _run_git(["push", "-u", "origin", cur], cwd=repo_dir)

    for b in branches:
        if b == cur:
            continue
        _run_git(["branch", "-f", b, tip], cwd=repo_dir)
        _run_git(["push", "-u", "origin", b], cwd=repo_dir)

    return repo_dir


def add_verified_label_to_project_meta(
    *,
    repo_dir: Path,
    http_base: str,
    admin_user: str,
    admin_password: str,
    project: str,
) -> None:
    """Fetch ``refs/meta/config``, add ``Verified`` label, push ``refs/meta/config``."""
    set_origin_url(
        repo_dir,
        http_base=http_base,
        user=admin_user,
        password=admin_password,
        project=project,
    )

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "integration@test.example",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "integration@test.example",
    }

    fetch_meta = git(
        "fetch",
        "origin",
        "refs/meta/config:refs/meta/config",
        cwd=repo_dir,
        env=env,
        check=False,
    )
    if fetch_meta.returncode == 0:
        _run_git(["checkout", "refs/meta/config"], cwd=repo_dir, env=env)
    elif "couldn't find remote ref refs/meta/config" in (fetch_meta.stderr or ""):
        # Newer Gerrit setups may not create refs/meta/config until first push.
        _run_git(["checkout", "--orphan", "refs/meta/config"], cwd=repo_dir, env=env)
    else:
        raise RuntimeError(
            f"git fetch refs/meta/config failed (cwd={repo_dir}): {fetch_meta.stderr or fetch_meta.stdout}",
        )

    cfg_path = repo_dir / "project.config"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    block = (
        '\n[label "Verified"]\n'
        "\tfunction = MaxWithBlock\n"
        "\tvalue = -1 Fails\n"
        "\tvalue =  0 No score\n"
        "\tvalue = +1 Verified\n"
    )
    if '[label "Verified"]' not in text:
        cfg_path.write_text(text.rstrip() + block + "\n", encoding="utf-8")

    _run_git(["add", "project.config"], cwd=repo_dir, env=env)
    _run_git(["commit", "-m", "Add Verified label (integration)"], cwd=repo_dir, env=env)
    _run_git(["push", "origin", "HEAD:refs/meta/config"], cwd=repo_dir, env=env)

    for cand in ("main", "master"):
        p = git("rev-parse", "--verify", cand, cwd=repo_dir, check=False)
        if p.returncode == 0:
            _run_git(["checkout", cand], cwd=repo_dir, env=env)
            break


def post_review_labels(
    session: GerritHttpSession,
    change_id: str,
    *,
    code_review: int | None = None,
    verified: int | None = None,
    message: str | None = None,
) -> None:
    labels: dict[str, int] = {}
    if code_review is not None:
        labels["Code-Review"] = code_review
    if verified is not None:
        labels["Verified"] = verified
    body: dict[str, object] = {"labels": labels}
    if message is not None:
        body["message"] = message
    enc = quote_change_id(change_id)
    session.post_json(f"changes/{enc}/revisions/current/review", body=body)


def configure_ger_git_repo(
    repo: Path,
    *,
    web_base: str,
    gerrit_user: str,
    gerrit_secret: str,
    gerrit_remote: str = "origin",
    branch: str,
    gerrit_target: str,
) -> None:
    """Apply ``gerrit.*`` and branch gerritTarget; clear config cache."""
    git("config", "gerrit.webUrl", web_base, cwd=repo)
    git("config", "gerrit.user", gerrit_user, cwd=repo)
    git("config", "gerrit.token", gerrit_secret, cwd=repo)
    git("config", "gerrit.remote", gerrit_remote, cwd=repo)
    set_branch_config(repo, branch, gerrit_target=gerrit_target)
    git("branch", "--set-upstream-to", f"{gerrit_remote}/{branch}", branch, cwd=repo)
    clear_gerrit_git_config_cache()
