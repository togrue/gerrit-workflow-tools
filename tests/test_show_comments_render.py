"""Unit tests for show range parsing and comment rendering."""

from __future__ import annotations

import pytest

from gerrit_workflow_tools.cli_style import (
    ANSI_DIM_GRAY,
    ANSI_YELLOW,
    GERRIT_LINK_LABEL,
    set_color_mode,
    set_hyperlink_mode,
    strip_ansi,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, ContextLine, InlineComment
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


def test_format_comment_chain_human_resolved_grey_border() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="done", author="alice"),),
        resolved=True,
    )
    set_color_mode(True)
    try:
        lines = format_comment_chain_human(chain, "https://g.example/c/1")
    finally:
        set_color_mode(False)
    joined = "\n".join(lines)
    visible = strip_ansi(joined)
    assert "(resolved)" in visible
    assert "╭─ f.py:3 (resolved)" in visible
    assert lines[0].startswith(f"    {ANSI_DIM_GRAY}╭")


def test_format_comment_chain_human_resolved_marker_without_color() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="done", author="alice"),),
        resolved=True,
    )
    set_color_mode(False)
    lines = format_comment_chain_human(chain, "https://g.example/c/1")
    joined = "\n".join(lines)
    assert "(resolved)" in joined
    assert ANSI_DIM_GRAY not in joined
    assert ANSI_YELLOW not in joined


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


def test_format_comment_chain_markdown_resolved_marker() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(InlineComment(path="f.py", line=3, message="done", author="alice"),),
        resolved=True,
    )
    joined = "\n".join(format_comment_chain_markdown(chain, "https://g.example/c/1"))
    assert "### `f.py:3` (resolved)" in joined
    assert "> done" in joined


def test_format_comment_chain_human_includes_context_lines() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=2,
        comments=(
            InlineComment(
                path="f.py",
                line=2,
                message="nit",
                author="alice",
                context_lines=(
                    ContextLine(1, "alpha()"),
                    ContextLine(2, "beta()"),
                ),
            ),
        ),
        resolved=False,
    )
    joined = "\n".join(format_comment_chain_human(chain, None))
    visible = strip_ansi(joined)
    assert "   1  alpha()" in visible
    assert "   2  beta()" in visible
    assert "nit" in visible


def test_format_comment_chain_markdown_includes_context_lines() -> None:
    chain = CommentChain(
        root_id="r1",
        path="f.py",
        line=2,
        comments=(
            InlineComment(
                path="f.py",
                line=2,
                message="nit",
                author="alice",
                context_lines=(ContextLine(2, "beta()"),),
            ),
        ),
        resolved=False,
    )
    joined = "\n".join(format_comment_chain_markdown(chain, None))
    assert "```" in joined
    assert "   2  beta()" in joined
    assert "> nit" in joined


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
