"""Integration: ``ger reply`` drafts and publishing them, against a real Gerrit.

``ger`` runs as the dev user (the change owner); the admin plays the reviewer. Drafts are
private until published, so the reviewer must not see a reply before it is published.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_reply import main as ger_reply_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.comment_chains import build_comment_chains
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import (
    open_changes_on_branch,
    post_unresolved_inline_comment,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain


def _pushed_change_with_comment(
    tmp_path: Path, ctx: Any, admin: GerritHttpSession, monkeypatch: pytest.MonkeyPatch, tag: str
) -> tuple[Path, str]:
    """One pushed change with one unresolved reviewer comment; returns the repo and Change-Id."""
    force_zero_change_trust_window(monkeypatch)
    topic = f"{tag}_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, [f"{tag} subject"])
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err
    rows = open_changes_on_branch(admin, ctx.project_verified, topic)
    assert rows, "pushed change not found"
    change_id = str(rows[0].get("change_id") or rows[0].get("id"))
    post_unresolved_inline_comment(admin, change_id, "chain_0.txt", 1, "please rename this")
    return repo, change_id


def _root_id(repo: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    code, out, err = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    assert code in (0, 1), err
    (chain,) = json.loads(out)["comment_chains"]
    return str(chain["root_id"])


def _published_chain(session: GerritHttpSession, change_id: str) -> Any:
    (chain,) = build_comment_chains(session.get_json(f"changes/{quote_change_id(change_id)}/comments"))
    return chain


def _drafts(session: GerritHttpSession, change_id: str) -> list[dict[str, Any]]:
    file_map = session.get_json(f"changes/{quote_change_id(change_id)}/drafts")
    return [row for rows in file_map.values() for row in rows]


def _amend(repo: Path, text: str) -> None:
    (repo / "chain_0.txt").write_text(text, encoding="utf-8")
    git("commit", "-a", "--amend", "--no-edit", cwd=repo)


def test_reply_update_discard_and_publish(
    tmp_path: Path,
    gerrit_integration_context: Any,
    gerrit_admin_session: GerritHttpSession,
    gerrit_dev_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, change_id = _pushed_change_with_comment(
        tmp_path, gerrit_integration_context, gerrit_admin_session, monkeypatch, "reply"
    )
    root = _root_id(repo, monkeypatch)

    code, out, err = run_cli(repo, ger_reply_main, [root, "-m", "Will do", "--json"], monkeypatch)
    assert code == 0, err
    assert json.loads(out)["action"] == "created"
    (draft,) = _drafts(gerrit_dev_session, change_id)
    assert (draft["in_reply_to"], draft["unresolved"]) == (root, True)
    # Private: the reviewer sees nothing yet.
    assert len(_published_chain(gerrit_admin_session, change_id).comments) == 1

    code, out, err = run_cli(repo, ger_reply_main, [root, "--done", "--json"], monkeypatch)
    assert code == 0, err
    payload = json.loads(out)
    assert (payload["action"], payload["replaced_message"]) == ("updated", "Will do")
    (draft,) = _drafts(gerrit_dev_session, change_id)
    assert (draft["message"], draft["unresolved"]) == ("Done", False)

    code, out, err = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    (chain,) = json.loads(out)["comment_chains"]
    assert (chain["resolved"], chain["resolved_after_publish"]) == (False, True)

    code, _out, err = run_cli(repo, ger_reply_main, [root, "--discard"], monkeypatch)
    assert code == 0, err
    assert _drafts(gerrit_dev_session, change_id) == []

    run_cli(repo, ger_reply_main, [root, "--done"], monkeypatch)
    code, out, err = run_cli(repo, ger_reply_main, ["--publish", "-y", "--json"], monkeypatch)
    assert code == 0, err
    assert [row["status"] for row in json.loads(out)["changes"]] == ["published"]
    assert _drafts(gerrit_dev_session, change_id) == []
    published = _published_chain(gerrit_admin_session, change_id)
    assert published.resolved is True
    assert [c.message for c in published.comments] == ["please rename this", "Done"]

    code, out, err = run_cli(
        repo, ger_show_main, ["--json", "--color", "never", "--comments", "all", "HEAD"], monkeypatch
    )
    (chain,) = json.loads(out)["comment_chains"]
    assert chain["resolved"] is True
    assert [c["draft"] for c in chain["comments"]] == [False, False]


def test_push_keeps_or_publishes_drafts_with_the_new_patchset(
    tmp_path: Path,
    gerrit_integration_context: Any,
    gerrit_admin_session: GerritHttpSession,
    gerrit_dev_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, change_id = _pushed_change_with_comment(
        tmp_path, gerrit_integration_context, gerrit_admin_session, monkeypatch, "pushpub"
    )
    root = _root_id(repo, monkeypatch)
    code, _out, err = run_cli(repo, ger_reply_main, [root, "--done"], monkeypatch)
    assert code == 0, err

    # -y without --publish-comments: Gerrit must accept no-publish-comments and keep the draft.
    _amend(repo, "renamed once\n")
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err
    assert len(_drafts(gerrit_dev_session, change_id)) == 1
    assert len(_published_chain(gerrit_admin_session, change_id).comments) == 1

    # --publish-comments: the reply arrives with the new patchset.
    _amend(repo, "renamed twice\n")
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check", "--publish-comments"], monkeypatch)
    assert code == 0, err
    assert _drafts(gerrit_dev_session, change_id) == []
    published = _published_chain(gerrit_admin_session, change_id)
    assert published.resolved is True
    assert published.comments[-1].message == "Done"
