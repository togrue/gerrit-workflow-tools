"""Word wrap for ger show prose. Kept apart from the shared show-render tests."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import strip_ansi
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, ContextLine, InlineComment
from gerrit_workflow_tools.render.comments import (
    format_comment_chain_human,
    format_comment_chain_markdown,
)
from gerrit_workflow_tools.render.wrap import fit_header, wrap_lines


def test_wrap_lines_breaks_on_words() -> None:
    assert wrap_lines("one two three", 7) == ["one two", "three"]


def test_wrap_lines_keeps_leading_indent_with_the_first_word() -> None:
    assert wrap_lines("  hello world", 10) == ["  hello", "world"]


def test_wrap_lines_slices_a_token_with_no_space() -> None:
    assert wrap_lines("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_wrap_lines_preserves_existing_newlines() -> None:
    assert wrap_lines("already\nfine", 20) == ["already", "fine"]


def test_fit_header_keeps_the_tail() -> None:
    text = "src/pkg/file.py:3 (resolved)"
    fitted = fit_header(text, 22)
    assert len(fitted) == 22
    assert fitted.startswith("…")
    assert fitted.endswith("(resolved)")
    assert "file.py" in fitted


def test_wrap_width_defaults_and_rejects_invalid_values() -> None:
    assert Settings.from_map({}).wrap_width == 120
    assert Settings.from_map({"gerrit.wrapWidth": "80"}).wrap_width == 80
    assert Settings.from_map({"gerrit.wrapWidth": "0"}).wrap_width == 120
    assert Settings.from_map({"gerrit.wrapWidth": "nope"}).wrap_width == 120


def _chain(*, message: str, path: str, context: str = "") -> CommentChain:
    lines = (ContextLine(2, context),) if context else ()
    return CommentChain(
        root_id="r1",
        path=path,
        line=2,
        comments=(InlineComment(path=path, line=2, message=message, author="alice", context_lines=lines),),
        resolved=False,
    )


def _inner_text(line: str) -> str | None:
    start = line.find("│")
    end = line.rfind("│")
    if start == -1 or end <= start:
        return None
    return line[start + 1 : end].strip()


def test_comment_box_wraps_text_and_context_but_keeps_the_url() -> None:
    width = 24
    url = "https://gerrit.example.com/c/project/+/12345"
    chain = _chain(
        message="please rework this function so it stays readable",
        path="src/gerrit_workflow_tools/render/comments.py",
        context="supercalifragilisticexpialidocious()",
    )
    visible = [strip_ansi(line) for line in format_comment_chain_human(chain, url, width=width)]
    joined = "\n".join(visible)
    assert url in joined
    assert any(url in line for line in visible)
    header = next(line for line in visible if "╭" in line)
    assert "comments.py:2" in header
    assert "src/gerrit_workflow_tools/render/comments.py" not in header
    for line in visible:
        if url in line:
            continue
        inner = _inner_text(line)
        if inner is not None:
            assert len(inner) <= width


def test_markdown_comments_stay_unwrapped() -> None:
    message = "please rework this function so it stays readable"
    chain = _chain(message=message, path="f.py")
    joined = "\n".join(format_comment_chain_markdown(chain, "https://g.example/c/1"))
    assert f"> {message}" in joined
