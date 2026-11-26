"""Unit tests for show range parsing and comment rendering."""

from __future__ import annotations

from typing import Any

import pytest

from gerrit_workflow_tools.core.comment_chain import CommentChain, ContextLine, InlineComment
from gerrit_workflow_tools.core.comment_chains import build_comment_chains
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_show import parse_show_range
from gerrit_workflow_tools.render.comments import format_comment_chain_human
from gerrit_workflow_tools.render.comments_markdown import format_comment_chain_markdown
from gerrit_workflow_tools.render.style import (
    ANSI_BLUE,
    ANSI_DIM_GRAY,
    ANSI_YELLOW,
    GERRIT_LINK_LABEL,
    set_color_mode,
    set_hyperlink_mode,
    strip_ansi,
)
from tests.test_suggested_edits import BLOCK_COMMENT, FIX_COMMENT, _with_replacement


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
    from gerrit_workflow_tools.render.style import ANSI_YELLOW, set_color_mode

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


def _answered_chain() -> CommentChain:
    return CommentChain(
        root_id="r1",
        path="f.py",
        line=3,
        comments=(
            InlineComment(path="f.py", line=3, message="rename n", comment_id="r1", author="Rita", unresolved=True),
            InlineComment(path="f.py", line=3, message="Done", comment_id="d1", unresolved=False, draft=True),
        ),
        resolved=False,
    )


def test_draft_reply_is_blue_and_says_it_resolves() -> None:
    set_color_mode(True)
    try:
        lines = format_comment_chain_human(_answered_chain(), "https://g.example/c/p/+/1")
    finally:
        set_color_mode(False)
    label = next(line for line in lines if "draft (you)" in line)
    assert ANSI_BLUE in label
    assert "draft (you) -> resolves" in strip_ansi(label)
    done = next(line for line in lines if "Done" in line)
    assert ANSI_BLUE in done


def test_draft_reply_markdown_is_marked_unpublished() -> None:
    lines = format_comment_chain_markdown(_answered_chain(), "https://g.example/c/p/+/1")
    assert "**Draft reply (unpublished) -> resolves**" in lines
    assert "> Done" in lines


# --- Suggested edits (docu/spec/commands/show.md) ------------------------------------------


def _chain_of(path: str, comment: dict[str, Any]) -> CommentChain:
    [chain] = build_comment_chains({path: [comment]})
    return chain


def test_show_human_box_shows_the_suggested_fix_and_the_block() -> None:
    fix = strip_ansi("\n".join(format_comment_chain_human(_chain_of("epsilon.txt", FIX_COMMENT), None)))
    assert "suggested fix: Use the LIMIT constant" in fix
    assert fix.count("suggested fix") == 1
    assert "epsilon.txt:2-3 becomes:" in fix
    assert "+ LIMIT = 10" in fix
    assert "+ use(LIMIT)" in fix
    # The block is shown once, as the reviewer wrote it, not again as a fix.
    block = strip_ansi("\n".join(format_comment_chain_human(_chain_of("zeta.py", BLOCK_COMMENT), None)))
    assert "```suggestion" in block
    assert "    return total" in block
    assert "suggested fix" not in block


def test_show_markdown_quotes_the_suggested_fix_fenced() -> None:
    fix = "\n".join(format_comment_chain_markdown(_chain_of("epsilon.txt", FIX_COMMENT), None))
    assert "> **Suggested fix:** Use the LIMIT constant" in fix
    assert fix.count("Suggested fix") == 1
    assert "> `epsilon.txt:2-3` becomes:\n> ```\n> LIMIT = 10\n> use(LIMIT)\n> ```" in fix
    block = "\n".join(format_comment_chain_markdown(_chain_of("zeta.py", BLOCK_COMMENT), None))
    assert "> ```suggestion\n>     return total\n> ```" in block
    assert "Suggested fix" not in block


def test_markdown_fence_is_longer_than_any_backtick_run_in_the_replacement() -> None:
    comment = _with_replacement("```\nx = `y`\n```\n")
    out = "\n".join(format_comment_chain_markdown(_chain_of("epsilon.txt", comment), None))
    assert "> ````\n> ```\n> x = `y`\n> ```\n> ````" in out
