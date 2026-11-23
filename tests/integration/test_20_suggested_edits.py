"""Integration: a comment's suggested edits, as Gerrit stores them, reach ``ger show``.

Two ways a reviewer suggests an edit, each posted through REST on a pushed change:

- a ```` ```suggestion ```` block in a comment's text, what the web UI writes. Any Gerrit
  stores it: it is only text.
- ``fix_suggestions`` on a human comment. Gerrit 3.14 stores them only with the experiment
  ``GerritBackendFeature__allow_fix_suggestions_in_comments``, which ``docker_gerrit.py``
  turns on for the test container (without it the review POST fails with HTTP 500).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import open_changes_on_branch, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def _pushed_change(
    ctx: Any, tmp_path: Path, session: GerritHttpSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A repository with one commit pushed for review, and its change as ``project~branch~Change-Id``."""
    force_zero_change_trust_window(monkeypatch)
    topic = f"suggest_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, ["suggested edits subject"])
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err
    [row] = open_changes_on_branch(session, ctx.project_verified, topic)
    return repo, f"{ctx.project_verified}~{topic}~{row['change_id']}"


def _post_comment(session: GerritHttpSession, change: str, comment: dict[str, Any]) -> None:
    session.post_json(
        f"changes/{quote_change_id(change)}/revisions/current/review",
        body={"comments": {"chain_0.txt": [comment]}},
    )


def _show_json_comment(repo: Path, monkeypatch: pytest.MonkeyPatch, line: int) -> dict[str, Any]:
    code, out, err = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    assert code in (0, 1), err
    [comment] = [c for c in json.loads(out)["comments"] if c["line"] == line]
    return dict(comment)


def test_ger_show_carries_a_suggestion_block(
    tmp_path, gerrit_integration_context, gerrit_admin_session: GerritHttpSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, change = _pushed_change(gerrit_integration_context, tmp_path, gerrit_admin_session, monkeypatch)
    message = "Or:\n\n```suggestion\nsecond line\n```\n"
    _post_comment(gerrit_admin_session, change, {"line": 2, "message": message, "unresolved": True})

    # The CommentInfo Gerrit keeps: the block is only text, there is no fix_suggestions.
    stored = gerrit_admin_session.get_json(f"changes/{quote_change_id(change)}/comments")
    [raw] = stored["chain_0.txt"]
    assert "fix_suggestions" not in raw
    fields = {"author", "id", "line", "message", "unresolved", "patch_set", "commit_id", "updated", "change_message_id"}
    assert fields <= raw.keys()
    assert (raw["line"], raw["message"].rstrip("\n")) == (2, message.rstrip("\n"))

    shown = _show_json_comment(repo, monkeypatch, 2)
    [block] = shown["suggested_edits"]
    assert (block["fix_id"], block["in_message"]) == (None, True)
    [rep] = block["replacements"]
    assert (rep["path"], rep["replacement"], rep["location"]) == ("chain_0.txt", "second line\n", "chain_0.txt:2")

    code, out, err = run_cli(repo, ger_show_main, ["--color", "never", "HEAD"], monkeypatch)
    assert code in (0, 1), err
    assert "```suggestion" in out
    assert "suggested fix" not in out, "the block shows once, in the text"


def test_ger_show_carries_a_fix_suggestion(
    tmp_path, gerrit_integration_context, gerrit_admin_session: GerritHttpSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, change = _pushed_change(gerrit_integration_context, tmp_path, gerrit_admin_session, monkeypatch)
    whole_line_1 = {"start_line": 1, "start_character": 0, "end_line": 2, "end_character": 0}
    replacement = {"path": "chain_0.txt", "range": whole_line_1, "replacement": "better subject\n"}
    fix = {"description": "Better subject", "replacements": [replacement]}
    _post_comment(
        gerrit_admin_session,
        change,
        {"line": 1, "message": "Say it better.", "unresolved": True, "fix_suggestions": [fix]},
    )

    shown = _show_json_comment(repo, monkeypatch, 1)
    [edit] = shown["suggested_edits"]
    assert edit["fix_id"], "Gerrit assigns the fix an id"
    assert (edit["description"], edit["in_message"]) == ("Better subject", False)
    [rep] = edit["replacements"]
    assert (rep["path"], rep["range"], rep["replacement"]) == ("chain_0.txt", whole_line_1, "better subject\n")
    assert rep["location"] == "chain_0.txt:1"

    code, out, err = run_cli(repo, ger_show_main, ["--color", "never", "HEAD"], monkeypatch)
    assert code in (0, 1), err
    assert "suggested fix: Better subject" in out
    assert "chain_0.txt:1 becomes:" in out
    assert "+ better subject" in out
