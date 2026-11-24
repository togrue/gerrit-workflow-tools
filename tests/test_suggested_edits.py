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
from gerrit_workflow_tools.core.suggested_edits import FixReplacement, SuggestedEdit, message_suggestions
from gerrit_workflow_tools.render.suggested_fixes import comment_text
from tests.change_store import ChangeStore
from tests.conftest import run_cli


_SHA = "abc12345678901234567890123456789012345678"
_CID = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# A CommentInfo with ``fix_suggestions``, in the documented CommentInfo / FixSuggestionInfo
# shape. Gerrit stores them on a human comment only with the experiment
# GerritBackendFeature__allow_fix_suggestions_in_comments (tests/integration/test_20_suggested_edits.py).
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

# A comment on line 7 whose text holds a ```suggestion block, what the web UI's "Suggest edit"
# writes, in the shape the test Gerrit (3.14) returns it: the block is only text.
BLOCK_COMMENT: dict[str, Any] = {
    "author": {"_account_id": 1000001, "name": "Bob", "email": "bob@example.com", "username": "bob"},
    "change_message_id": "c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0",
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


def _show(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str, fix_comment: dict[str, Any] = FIX_COMMENT) -> str:
    git("config", "ger.webUrl", "https://g.example", cwd=repo)
    store = _store({"epsilon.txt": [fix_comment], "zeta.py": [BLOCK_COMMENT]})
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


def _with_replacement(text: str, rng: dict[str, int] | None = None) -> dict[str, Any]:
    [fix] = FIX_COMMENT["fix_suggestions"]
    [rep] = fix["replacements"]
    new_rep = dict(rep, replacement=text, range=rng or rep["range"])
    return dict(FIX_COMMENT, fix_suggestions=[dict(fix, replacements=[new_rep])])


def test_a_suggestion_block_with_crlf_line_endings_is_read() -> None:
    message = "Typo:\r\n\r\n```suggestion\r\n    return total\r\n```\r\nThanks.\r\n"
    [edit] = message_suggestions("zeta.py", message, start_line=None, line=7)
    assert edit.replacements[0].replacement == "    return total\n"


def test_the_first_fence_line_ends_a_suggestion_block() -> None:
    message = "```suggestion\na\n```\nb\n```\n"
    [edit] = message_suggestions("f.py", message, start_line=None, line=3)
    assert edit.replacements[0].replacement == "a\n"


def test_human_box_does_not_wrap_a_suggested_code_line(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = "        " + " ".join(f"argument_{n}" for n in range(20))
    out = strip_ansi(_show(stack_repo, monkeypatch, "--color=never", fix_comment=_with_replacement(code + "\n")))
    assert f"+ {code}" in out


def test_an_insertion_says_where_it_goes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = {"start_line": 2, "start_character": 4, "end_line": 2, "end_character": 4}
    fix_comment = _with_replacement("LIMIT", at)
    assert "insert at epsilon.txt:2.5:" in strip_ansi(
        _show(stack_repo, monkeypatch, "--color=never", fix_comment=fix_comment)
    )
    assert "> insert at `epsilon.txt:2.5`:" in _show(stack_repo, monkeypatch, "--ai", fix_comment=fix_comment)


@pytest.mark.parametrize(
    ("rng", "where"),
    [
        (CommentRange(5, 0, 6, 0), "f.py:5"),
        (CommentRange(5, 0, 8, 0), "f.py:5-7"),
        (CommentRange(5, 2, 5, 9), "f.py:5.3-5.9"),
        (CommentRange(5, 2, 7, 0), "f.py:5.3-7.0"),
        (CommentRange(5, 0, 7, 3), "f.py:5.1-7.3"),
        (CommentRange(5, 2, 5, 2), "f.py:5.3"),
    ],
)
def test_replacement_describes_the_range_it_replaces(rng: CommentRange, where: str) -> None:
    assert FixReplacement(path="f.py", range=rng, replacement="x").describe() == where


# What ger serve sends as a comment's `text` and `markdown`: VS Code shows them as they are.
_LINES = CommentRange(2, 0, 4, 0)


def _fix(*replacements: tuple[CommentRange, str], description: str = "Use the LIMIT constant") -> SuggestedEdit:
    return SuggestedEdit(
        description, tuple(FixReplacement("epsilon.txt", rng, text) for rng, text in replacements), fix_id="f1"
    )


def test_a_comments_text_writes_its_fixes_out_below_it_in_markdown() -> None:
    fix = _fix((_LINES, "LIMIT = 10\nuse(LIMIT)\n"))
    assert comment_text("Name the limit.", (fix,), markdown=True) == (
        "Name the limit.\n\n**Suggested fix:** Use the LIMIT constant\n`epsilon.txt:2-3` becomes:\n"
        "```\nLIMIT = 10\nuse(LIMIT)\n```"
    )
    deletion = _fix((_LINES, ""), description="")
    assert comment_text("", (deletion,), markdown=True) == "**Suggested fix:**\n`epsilon.txt:2-3`: delete"
    block = "Typo:\n\n```suggestion\n    return total\n```\n"
    in_message = SuggestedEdit("", (FixReplacement("epsilon.txt", _LINES, "    return total\n"),), in_message=True)
    assert comment_text(block, (in_message,), markdown=True) == block
    assert comment_text("plain", (), markdown=True) == "plain"


def test_a_fix_is_fenced_longer_than_any_backtick_run_in_it() -> None:
    fix = _fix((CommentRange(2, 0, 3, 0), "```\ncode\n```\n"), description="")
    assert (
        comment_text("", (fix,), markdown=True)
        == "**Suggested fix:**\n`epsilon.txt:2` becomes:\n````\n```\ncode\n```\n````"
    )


def test_a_comments_plain_text_says_an_insertion_as_one() -> None:
    fix = _fix((_LINES, "LIMIT = 10\r\n    use(LIMIT)\r\n"), (CommentRange(5, 2, 5, 2), "x"), (_LINES, ""))
    assert comment_text("Name the limit.", (fix,)) == (
        "Name the limit.\n\nsuggested fix: Use the LIMIT constant\n  epsilon.txt:2-3 becomes:\n"
        "  + LIMIT = 10\n  +     use(LIMIT)\n  insert at epsilon.txt:5.3:\n  + x\n  epsilon.txt:2-3: delete"
    )
    assert comment_text("plain", ()) == "plain"
