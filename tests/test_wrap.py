"""Word wrap for ger show prose. Kept apart from the shared show-render tests."""

from __future__ import annotations

from gerrit_workflow_tools.core.comment_chain import CommentChain, ContextLine, InlineComment
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.render.comments import format_comment_chain_human
from gerrit_workflow_tools.render.comments_markdown import format_comment_chain_markdown
from gerrit_workflow_tools.render.style import strip_ansi
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
    assert Settings.from_map({"ger.wrapWidth": "80"}).wrap_width == 80
    assert Settings.from_map({"ger.wrapWidth": "0"}).wrap_width == 120
    assert Settings.from_map({"ger.wrapWidth": "nope"}).wrap_width == 120


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


def test_a_next_line_breaks_between_words_never_inside_a_command_or_a_step() -> None:
    from gerrit_workflow_tools.render.wrap import note_lines

    line = "next: resolve the conflicts in calc/trig.py, `git add` them, then `ger rebase --continue` to finish"
    assert note_lines(line, None) == [line], "piped: one line"
    wrapped = note_lines(line, 40)
    assert all(len(part) <= 40 for part in wrapped)
    assert " ".join(part.strip() for part in wrapped) == line
    assert all(part.startswith("      ") for part in wrapped[1:]), "indented under the text after next:"
    assert any("`ger rebase --continue`" in part for part in wrapped)
    steps = "next: cd ../pg-review · ger comment PATH:LINE -m … · ger review --next"
    assert note_lines(steps, 40) == [
        "next: cd ../pg-review ·",
        "      ger comment PATH:LINE -m … ·",
        "      ger review --next",
    ]
    other = 'updated change-12 from patchset 1 to change 12 patchset 3 "a long subject", rebased 2 commits above it'
    assert [part[:2] for part in note_lines(other, 50)[1:]] == ["  "] * (len(note_lines(other, 50)) - 1)
