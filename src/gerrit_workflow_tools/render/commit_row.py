"""ANSI-colored commit-row rendering for ``ger log`` and ``ger show``."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import (
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_STRIKE,
    ANSI_YELLOW,
    color_short_sha,
    color_text,
    is_color_enabled,
    visible_len,
)
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.render.status_fmt import (
    code_review_token,
    comments_token,
    patchset_token,
    verified_token,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

# Fixed width for the abbreviated SHA so status columns line up across commits.
_STATUS_SHA_COL_WIDTH = 8

_PATCHSET_COLOR: dict[str, str] = {
    "a": ANSI_DIM,
    "m": ANSI_GREEN,
    "!": ANSI_RED,
    "?": ANSI_YELLOW,
    "p": ANSI_GREEN,
    "n": ANSI_YELLOW,
    "o": ANSI_RED,
    "-": ANSI_DIM,
}

_VERIFIED_COLOR: dict[str, str] = {
    "v+1": ANSI_GREEN,
    "v-1": ANSI_RED,
    "v0 ": ANSI_DIM,
    "v? ": ANSI_DIM,
}

_CODE_REVIEW_COLOR: dict[str, str] = {
    "cr+2": ANSI_GREEN,
    "cr+1": ANSI_LIGHT_GREEN,
    "cr0 ": ANSI_DIM,
    "cr-1": ANSI_YELLOW,
    "cr-2": ANSI_RED,
    "cr? ": ANSI_DIM,
}


def _status_sha_column(short_sha: str) -> str:
    return short_sha.ljust(_STATUS_SHA_COL_WIDTH)


def fmt_summary_strike(summary: str) -> str:
    """Strike through the commit summary (ANSI SGR 9, or combining chars without a TTY)."""
    if is_color_enabled():
        return f"{ANSI_STRIKE}{summary}{ANSI_RESET}"
    return "".join(f"{c}̶" for c in summary)


def fmt_patchset_column(commit: LogCommit) -> str:
    """Single-letter patchset column with ANSI color."""
    tok = patchset_token(commit)
    return color_text(tok, _PATCHSET_COLOR.get(tok, ANSI_DIM))


def fmt_verified(v: int | None) -> str:
    """Three-char verified label with ANSI color."""
    tok = verified_token(v)
    return color_text(tok, _VERIFIED_COLOR.get(tok, ANSI_DIM))


def fmt_code_review(cr: int | None) -> str:
    """Four-char code-review label with ANSI color."""
    tok = code_review_token(cr)
    return color_text(tok, _CODE_REVIEW_COLOR.get(tok, ANSI_DIM))


def fmt_comments(count: int) -> str:
    """Three-char comment indicator with ANSI color."""
    tok = comments_token(count)
    return color_text(tok, ANSI_YELLOW if count > 0 else ANSI_DIM)


def primary_line_prefix(commit: LogCommit) -> str:
    """Text before the subject on the primary line (through ``  # ``)."""
    sha = color_short_sha(_status_sha_column(commit.short_sha))
    push = fmt_patchset_column(commit)
    verified = fmt_verified(commit.verified)
    cr = fmt_code_review(commit.code_review)
    comments = fmt_comments(commit.comments_unresolved)
    return f"{sha} {push} {verified} {cr} {comments} # "


def continuation_indent(commit: LogCommit) -> int:
    """Column where the subject starts; continuation lines align using visible_len on the prefix."""
    return visible_len(primary_line_prefix(commit))


def fmt_change_id_suffix(change_id: str | None) -> str:
    if not change_id:
        return ""
    disp = change_id if len(change_id) <= 14 else change_id[:12] + "…"
    return color_text(f"  {disp}", ANSI_DIM)


def primary_line(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
) -> str:
    summ = fmt_summary_strike(commit.summary) if commit.abandoned else commit.summary
    if summary_highlighter is not None and not commit.abandoned:
        summ = summary_highlighter.highlight(summ)
    line = f"{primary_line_prefix(commit)}{summ}"
    if show_change_id:
        line += fmt_change_id_suffix(commit.change_id)
    return line


def extra_detail_lines(commit: LogCommit) -> list[str]:
    """Indented CI failure lines (one or many), or empty list when CI is clean."""
    failures = commit.ci_failures
    if not failures:
        return []
    if len(failures) == 1:
        return [color_text(f"# failed: {failures[0]}", ANSI_RED)]
    lines: list[str] = [color_text("# failed checks:", ANSI_RED)]
    for name in failures:
        lines.append(color_text(f"  · {name}", ANSI_RED))
    return lines


def attention_tokens(commit: LogCommit) -> list[tuple[str, str]]:
    """Attention (text, ANSI-color) pairs for the trailing annotation column."""
    if commit.abandoned:
        return [("abandoned", ANSI_RED)]
    if commit.patchset_status == "merged-drift":
        return [("merged drift", ANSI_RED)]
    if commit.patchset_status == "merged-unknown":
        return [("merged (equiv. unknown)", ANSI_YELLOW)]
    if commit.patchset_status == "merged-same":
        return []

    tokens: list[tuple[str, str]] = []
    if commit.ci_failures or (commit.verified is not None and commit.verified <= -1):
        tokens.append(("build failed", ANSI_RED))
    if commit.comments_unresolved > 0:
        noun = "comment" if commit.comments_unresolved == 1 else "comments"
        tokens.append((f"{commit.comments_unresolved} unresolved {noun}", ANSI_YELLOW))
    if "no-reviewers" in commit.attention_reasons:
        tokens.append(("no reviewers", ANSI_DIM_GRAY))
    if commit.submittable and not tokens:
        tokens.append(("submittable", ANSI_GREEN))
    return tokens


def attention_suffix(commit: LogCommit) -> str:
    tokens = attention_tokens(commit)
    if not tokens:
        return ""
    rendered: list[str] = [color_text("# ", ANSI_DIM)]
    for idx, (text, code) in enumerate(tokens):
        if idx:
            rendered.append(color_text(", ", ANSI_DIM))
        rendered.append(color_text(text, code))
    return "".join(rendered)


def attention_column(
    commits: list[LogCommit],
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
) -> int:
    widths = [
        visible_len(
            primary_line(
                commit,
                summary_highlighter=summary_highlighter,
                show_change_id=show_change_id,
            )
        )
        for commit in commits
        if attention_tokens(commit)
    ]
    if not widths:
        return 0
    return max(widths) + 2


def oneline_body(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
    attention_col: int = 0,
) -> str:
    """Oneline text through attention suffix; excludes Gerrit URL."""
    base = primary_line(
        commit,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
    )
    suffix = attention_suffix(commit)
    if suffix:
        gap = max(2, attention_col - visible_len(base)) if attention_col else 2
        base = f"{base}{' ' * gap}{suffix}"
    return base


def oneline_line(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    include_url: bool,
    show_change_id: bool = False,
    attention_col: int = 0,
    url_start_visible: int | None = None,
) -> str:
    body = oneline_body(
        commit,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
        attention_col=attention_col,
    )
    if include_url and commit.gerrit_url:
        if url_start_visible is not None:
            pad = max(url_start_visible - visible_len(body), 2)
            return f"{body}{' ' * pad}{color_text(commit.gerrit_url, ANSI_DIM)}"
        return f"{body}  {color_text(commit.gerrit_url, ANSI_DIM)}"
    return body
