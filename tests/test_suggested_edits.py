# Spec: docu/spec/commands/show.md (Suggested edits)
# Covers: Gerrit fix suggestions and ```suggestion blocks on inline comments, in ger show human, Markdown and --json

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.cli_style import strip_ansi
from gerrit_workflow_tools.core.comment_anchor import CommentRange
from gerrit_workflow_tools.core.comment_chains import build_comment_chains
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.suggested_edits import FixReplacement, message_suggestions
from tests.change_store import ChangeStore
from tests.conftest import run_cli


_SHA = "abc12345678901234567890123456789012345678"
_CID = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# CommentInfo as Gerrit 3.10 returns it for a comment posted with ``fix_suggestions``
# (``POST …/revisions/current/review``, ``CommentInput.fix_suggestions``).
FIX_COMMENT: dict[str, Any] = {
    "author": {"_account_id": 1000000, "name": "Administrator", "email": "admin@example.com", "username": "admin"},
    "change_message_id": "9b1e0f3c4b0b1a3fd8c2e6f0a0c9d1e2b3c4d5e6",
    "unresolved": True,
    "patch_set": 1,
    "id": "4f0a1c2b_8d9e7f60",
    "line": 3,
    "range": {"start_line": 2, "start_character": 0, "end_line": 3, "end_character": 9},
    "updated": "2026-11-18 10:00:00.000000000",
    "message": "Name the limit.",
    "commit_id": _SHA,
    "fix_suggestions": [
        {
            "fix_id": "a6b3c2d1_0e9f8a7b",
            "description": "Use the LIMIT constant",
            "replacements": [
                {
                    "path": "epsilon.txt",
                    "range": {"start_line": 2, "start_character": 0, "end_line": 4, "end_character": 0},
                    "replacement": "LIMIT = 10\nuse(LIMIT)\n",
                }
            ],
        }
    ],
}

# What the web UI's "Suggest edit" writes into the text of a comment on line 7.
BLOCK_COMMENT: dict[str, Any] = {
    "author": {"_account_id": 1000001, "name": "Bob", "username": "bob"},
    "unresolved": True,
    "patch_set": 1,
    "id": "7c6d5e4f_3a2b1c0d",
    "line": 7,
    "updated": "2026-11-18 11:00:00.000000000",
    "message": "Typo:\n\n```suggestion\n    return total\n```\n",
    "commit_id": _SHA,
}


def _store(comments: dict[str, list[dict[str, Any]]]) -> ChangeStore:
    change = {
        "id": f"testproj~main~{_CID}",
        "change_id": _CID,
        "project": "testproj",
        "branch": "main",
        "_number": 42,
        "subject": "subj",
        "current_revision": _SHA,
        "unresolved_comment_count": 2,
        "revisions": {_SHA: {"_number": 1}},
        "labels": {"Code-Review": {"value": 0, "all": []}},
    }
    store = ChangeStore({str(change["id"]): change}, web_base="https://g.example")
    store.set_comments(str(change["id"]), comments)
    return store


def _show(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> str:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    store = _store({"epsilon.txt": [FIX_COMMENT], "zeta.py": [BLOCK_COMMENT]})
    code, out, err = run_cli(repo, gshow_main, [*args, "change:42"], monkeypatch, gerrit=store)
    assert code == 0, err
    return out


def test_show_json_carries_a_fix_suggestion_and_a_suggestion_block(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = json.loads(_show(stack_repo, monkeypatch, "--json"))
    by_id = {c["comment_id"]: c for c in shown["comments"]}
    fix = by_id["4f0a1c2b_8d9e7f60"]
    assert fix["message"] == "Name the limit."
    assert fix["suggested_edits"] == [
        {
            "fix_id": "a6b3c2d1_0e9f8a7b",
            "description": "Use the LIMIT constant",
            "in_message": False,
            "replacements": [dict(FIX_COMMENT["fix_suggestions"][0]["replacements"][0], location="epsilon.txt:2-3")],
        }
    ]
    block = by_id["7c6d5e4f_3a2b1c0d"]
    assert block["message"] == BLOCK_COMMENT["message"], "the text stays as written"
    assert block["suggested_edits"] == [
        {
            "fix_id": None,
            "description": "",
            "in_message": True,
            "replacements": [
                {
                    "path": "zeta.py",
                    "range": {"start_line": 7, "start_character": 0, "end_line": 8, "end_character": 0},
                    "replacement": "    return total\n",
                    "location": "zeta.py:7",
                }
            ],
        }
    ]
    # The chains carry the same comments as the flat list.
    chained = {c["comment_id"]: c for chain in shown["comment_chains"] for c in chain["comments"]}
    assert chained["4f0a1c2b_8d9e7f60"]["suggested_edits"] == fix["suggested_edits"]


def test_show_human_box_shows_the_suggested_fix_and_the_block(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = strip_ansi(_show(stack_repo, monkeypatch, "--color=never"))
    assert "suggested fix: Use the LIMIT constant" in out
    assert "epsilon.txt:2-3 becomes:" in out
    assert "+ LIMIT = 10" in out
    assert "+ use(LIMIT)" in out
    # The block is shown once, as the reviewer wrote it, not again as a fix.
    assert "```suggestion" in out
    assert "    return total" in out
    assert out.count("suggested fix") == 1


def test_show_markdown_quotes_the_suggested_fix_fenced(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _show(stack_repo, monkeypatch, "--ai")
    assert "> **Suggested fix:** Use the LIMIT constant" in out
    assert "> `epsilon.txt:2-3` becomes:\n> ```\n> LIMIT = 10\n> use(LIMIT)\n> ```" in out
    assert "> ```suggestion\n>     return total\n> ```" in out
    assert out.count("Suggested fix") == 1


def test_a_block_a_fix_suggestion_already_carries_is_one_edit() -> None:
    both = dict(FIX_COMMENT, message="Name it:\n```suggestion\nLIMIT = 10\nuse(LIMIT)\n```")
    [chain] = build_comment_chains({"epsilon.txt": [both]})
    [edit] = chain.comments[0].suggested_edits
    assert edit.fix_id == "a6b3c2d1_0e9f8a7b"


def test_block_on_a_range_replaces_its_lines_and_a_file_comment_has_none() -> None:
    [edit] = message_suggestions("f.py", "```suggestion\n```", start_line=4, line=6)
    assert edit.replacements[0].range == CommentRange(4, 0, 7, 0)
    assert edit.replacements[0].replacement == ""
    assert message_suggestions("f.py", "```suggestion\nx\n```", start_line=None, line=None) == ()
    assert message_suggestions("f.py", "```python\nx\n```", start_line=None, line=3) == ()


def test_malformed_fix_suggestions_are_left_out() -> None:
    broken = dict(FIX_COMMENT, fix_suggestions=[{"fix_id": "x", "replacements": [{"path": "f", "range": {}}]}, "junk"])
    [chain] = build_comment_chains({"epsilon.txt": [broken]})
    assert chain.comments[0].suggested_edits == ()


@pytest.mark.parametrize(
    ("rng", "where"),
    [
        (CommentRange(5, 0, 6, 0), "f.py:5"),
        (CommentRange(5, 0, 8, 0), "f.py:5-7"),
        (CommentRange(5, 2, 5, 9), "f.py:5.3-5.9"),
        (CommentRange(5, 2, 7, 0), "f.py:5.3-6"),
        (CommentRange(5, 2, 5, 2), "f.py:5.3 (insert)"),
    ],
)
def test_replacement_describes_where_it_applies_in_location_grammar(rng: CommentRange, where: str) -> None:
    assert FixReplacement(path="f.py", range=rng, replacement="x").describe() == where
