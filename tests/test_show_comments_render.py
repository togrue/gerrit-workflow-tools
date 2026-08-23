"""Unit tests for show range parsing and comment rendering."""

from __future__ import annotations

import pytest

from gerrit_workflow_tools.cli_style import GERRIT_LINK_LABEL, set_hyperlink_mode, strip_ansi
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment
from gerrit_workflow_tools.core.gerrit_show import parse_show_range
from gerrit_workflow_tools.render.comments import (
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


def test_format_comment_chain_human_rounded_box() -> None:
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
    lines = format_comment_chain_human(chain, "https://g.example/c/1")
    joined = "\n".join(lines)
    assert "╭─ f.py:3" in joined
    assert "╰" in joined
    assert "│ alice" in joined or "│alice" in joined
    assert "root" in joined
    assert "bob" in joined
    assert "reply" in joined
    assert "└ " not in joined
    assert "url:" in joined


def test_format_comment_chain_human_hyperlink_label() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="root", author="alice"),),
        resolved=False,
    )
    set_hyperlink_mode(True)
    try:
        lines = format_comment_chain_human(chain, "https://g.example/c/1")
    finally:
        set_hyperlink_mode(False)
    joined = "\n".join(lines)
    assert "\x1b]8;;https://g.example/c/1" in joined
    visible = strip_ansi(joined)
    assert GERRIT_LINK_LABEL in visible
    assert "url:" in visible
    assert "https://g.example/c/1" not in visible


def test_format_comment_chain_human_box_border_is_yellow() -> None:
    from gerrit_workflow_tools.cli_style import ANSI_YELLOW, set_color_mode

    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="root", author="alice"),),
        resolved=False,
    )
    set_color_mode(True)
    try:
        lines = format_comment_chain_human(chain, "https://g.example/c/1")
    finally:
        set_color_mode(False)
    joined = "\n".join(lines)
    assert ANSI_YELLOW in joined
    assert "╭" in joined


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


def test_format_comment_chain_markdown_ignores_hyperlinks() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="root", author="alice"),),
        resolved=False,
    )
    set_hyperlink_mode(True)
    try:
        lines = format_comment_chain_markdown(chain, "https://g.example/c/1")
    finally:
        set_hyperlink_mode(False)
    joined = "\n".join(lines)
    assert "https://g.example/c/1" in joined
    assert "\x1b]8;" not in joined
    assert GERRIT_LINK_LABEL not in joined
