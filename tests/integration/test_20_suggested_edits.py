"""Integration: a comment's suggested edits, as Gerrit stores them, reach ``ger show``.

Posts one comment with ``fix_suggestions`` and one whose text holds a ```` ```suggestion ````
block through REST, then checks ``ger show --json`` carries both as ``suggested_edits`` and
the human box shows the fix.
"""

from __future__ import annotations

import json
import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import open_changes_on_branch, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def test_ger_show_carries_fix_suggestions_and_suggestion_blocks(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_zero_change_trust_window(monkeypatch)
    topic = f"suggest_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["suggested edits subject"])
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err

    rows = open_changes_on_branch(gerrit_admin_session, gerrit_integration_context.project_verified, topic)
    assert rows
    change_id = str(rows[0].get("change_id") or rows[0].get("id"))
    whole_line_1 = {"start_line": 1, "start_character": 0, "end_line": 2, "end_character": 0}
    gerrit_admin_session.post_json(
        f"changes/{quote_change_id(change_id)}/revisions/current/review",
        body={
            "comments": {
                "chain_0.txt": [
                    {
                        "line": 1,
                        "message": "Say it better.",
                        "unresolved": True,
                        "fix_suggestions": [
                            {
                                "description": "Better subject",
                                "replacements": [
                                    {"path": "chain_0.txt", "range": whole_line_1, "replacement": "better subject\n"}
                                ],
                            }
                        ],
                    },
                    {"line": 2, "message": "Or:\n\n```suggestion\nsecond line\n```\n", "unresolved": True},
                ]
            }
        },
    )

    code_show, out_show, eshow = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    assert code_show in (0, 1), eshow
    by_line = {c["line"]: c for c in json.loads(out_show)["comments"]}
    [fix] = by_line[1]["suggested_edits"]
    assert fix["fix_id"], "Gerrit assigns the fix an id"
    assert (fix["description"], fix["in_message"]) == ("Better subject", False)
    [rep] = fix["replacements"]
    assert (rep["path"], rep["range"], rep["replacement"]) == ("chain_0.txt", whole_line_1, "better subject\n")
    [block] = by_line[2]["suggested_edits"]
    assert block["in_message"] is True
    assert block["replacements"][0]["replacement"] == "second line\n"

    code_human, out_human, ehuman = run_cli(repo, ger_show_main, ["--color", "never", "HEAD"], monkeypatch)
    assert code_human in (0, 1), ehuman
    assert "suggested fix: Better subject" in out_human
    assert "+ better subject" in out_human
    assert "```suggestion" in out_human
