"""Unit tests for show range parsing and comment rendering."""

from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment
from gerrit_workflow_tools.core.gerrit_show import parse_show_range
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.render.comments import (
    apply_comment_tail,
    format_comment_chain_human,
    format_comment_chain_markdown,
)


def test_parse_show_range_two_and_three_dots() -> None:
    assert parse_show_range("main..HEAD") == ("main", "..", "HEAD")
    assert parse_show_range("a...b") == ("a", "...", "b")
    assert parse_show_range("origin/main..") == ("origin/main", "..", "HEAD")
    assert parse_show_range("HEAD") is None


def test_parse_show_range_rejects_empty_left() -> None:
    with pytest.raises(ChangeResolutionError):
        parse_show_range("   ..HEAD")


def test_apply_comment_tail() -> None:
    text = "\n".join(f"L{i}" for i in range(5))
    body, trunc = apply_comment_tail(text, 2, full=False)
    assert trunc is True
    assert "L3" in body and "L4" in body
    assert "L0" not in body
    full, trunc2 = apply_comment_tail(text, 2, full=True)
    assert trunc2 is False
    assert full == text


def test_format_comment_chain_human_gutter() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(
            InlineComment(path="f.py", line=3, message="root", author="alice"),
            InlineComment(path="f.py", line=3, message="reply", author="bob"),
        ),
        resolved=False,
    )
    lines = format_comment_chain_human(chain, "https://g.example/c/1", tail_n=10, full=True)
    joined = "\n".join(lines)
    assert "f.py:3" in joined
    assert "└ " in joined
    assert "reply" in joined


def test_format_comment_chain_markdown() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(
            InlineComment(path="f.py", line=3, message="root\nline2", author="alice"),
            InlineComment(path="f.py", line=3, message="reply", author="bob"),
        ),
        resolved=False,
    )
    lines = format_comment_chain_markdown(chain, "https://g.example/c/1")
    joined = "\n".join(lines)
    assert "### `f.py:3`" in joined
    assert "**alice**" in joined
    assert "> root" in joined
    assert "> line2" in joined
    assert "**bob**" in joined
    assert "> reply" in joined
