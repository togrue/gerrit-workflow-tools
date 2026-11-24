"""ANSI-colored commit-row rendering for ``ger log`` and ``ger show``."""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.ci_links import CiPipeline
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.render.status_fmt import (
    PRIVATE,
    SUBMITTABLE,
    attention_hints,
    code_review_token,
    comments_token,
    patchset_token,
    verified_token,
)
from gerrit_workflow_tools.render.style import (
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
    format_link,
    is_color_enabled,
    is_hyperlink_enabled,
    visible_len,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


# Fixed width for the abbreviated SHA so status columns line up across commits.
_STATUS_SHA_COL_WIDTH = 8

_DETAIL_LABEL_WIDTH = len("Change-Id:")

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
    if commit.pushed:
        verified = fmt_verified(commit.verified)
        cr = fmt_code_review(commit.code_review)
        comments = fmt_comments(commit.comments_unresolved)
    else:
        verified = "   "
        cr = "    "
        comments = "   "
    return f"{sha} {push} {verified} {cr} {comments} # "


def continuation_indent(commit: LogCommit) -> int:
    """Column where the subject starts; continuation lines align using visible_len on the prefix."""
    return visible_len(primary_line_prefix(commit))


def _detail_label(text: str) -> str:
    return color_text(text.ljust(_DETAIL_LABEL_WIDTH), ANSI_DIM)


def _detail_value_pad() -> str:
    return " " * (_DETAIL_LABEL_WIDTH + 1)


def primary_line(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
) -> str:
    summ = fmt_summary_strike(commit.summary) if commit.abandoned else commit.summary
    if summary_highlighter is not None and not commit.abandoned:
        summ = summary_highlighter.highlight(summ, sha=commit.sha)
    return f"{primary_line_prefix(commit)}{summ}"


def _ci_state_color(state: str, *, outdated: bool = False) -> str:
    if outdated:
        return ANSI_DIM_GRAY
    if state == "FAILED":
        return ANSI_RED
    if state == "SUCCESSFUL":
        return ANSI_GREEN
    return ANSI_DIM


def _format_ci_pipeline_item(pipeline: CiPipeline) -> str:
    color = _ci_state_color(pipeline.state, outdated=pipeline.outdated)
    if pipeline.url and is_hyperlink_enabled():
        return color_text(format_link(pipeline.url, label=pipeline.label), color)
    if pipeline.url:
        return color_text(f"{pipeline.label} {pipeline.url}", color)
    return color_text(pipeline.label, color)


def _pipelines_for_display(commit: LogCommit, *, verbose_level: int = 1) -> list[CiPipeline]:
    if commit.ci_pipelines:
        pipelines = commit.ci_pipelines
    elif commit.ci_links:
        pipelines = [CiPipeline(label=link.label, state="FAILED", url=link.url) for link in commit.ci_links]
    else:
        pipelines = [CiPipeline(label=name, state="FAILED", url=None) for name in commit.ci_failures]

    if verbose_level >= 2:
        return pipelines
    return [p for p in pipelines if p.state == "FAILED" or p.outdated]


def format_ci_lines(commit: LogCommit, *, verbose_level: int = 1) -> list[str]:
    """Indented CI continuation lines, or empty when there is no CI data."""
    pipelines = _pipelines_for_display(commit, verbose_level=verbose_level)
    if not pipelines:
        return []

    label = _detail_label("CI:")
    if is_hyperlink_enabled():
        items = " ".join(_format_ci_pipeline_item(p) for p in pipelines)
        return [f"{label} {items}"]

    lines = [f"{label} {_format_ci_pipeline_item(pipelines[0])}"]
    pad = _detail_value_pad()
    for pipeline in pipelines[1:]:
        lines.append(f"{pad}{_format_ci_pipeline_item(pipeline)}")
    return lines


def format_change_id_line(change_id: str | None) -> str | None:
    if not change_id:
        return None
    return f"{_detail_label('Change-Id:')} {change_id}"


def format_reviewers_line(commit: LogCommit) -> str | None:
    """``Reviewers: alice, bob (cc: carol)``, or ``None`` when Gerrit lists nobody on the change."""
    reviewers = [r.slug for r in commit.reviewers if not r.cc]
    ccs = [r.slug for r in commit.reviewers if r.cc]
    if not reviewers and not ccs:
        return None
    parts = [", ".join(reviewers)] if reviewers else []
    if ccs:
        parts.append(color_text(f"(cc: {', '.join(ccs)})", ANSI_DIM))
    return f"{_detail_label('Reviewers:')} {' '.join(parts)}"


def continuation_lines(
    commit: LogCommit,
    *,
    verbose_level: int = 0,
    show_change_id: bool = False,
) -> list[str]:
    """Continuation detail lines below the primary oneline row."""
    lines: list[str] = []
    if verbose_level >= 1:
        lines.extend(format_ci_lines(commit, verbose_level=verbose_level))
        reviewers_line = format_reviewers_line(commit)
        if reviewers_line:
            lines.append(reviewers_line)
    if show_change_id:
        change_line = format_change_id_line(commit.change_id)
        if change_line:
            lines.append(change_line)
    return lines


_HINT_COLOR: dict[str, str] = {
    Attention.ABANDONED: ANSI_RED,
    Attention.MERGED_DRIFT: ANSI_RED,
    Attention.WIP: ANSI_YELLOW,
    PRIVATE: ANSI_DIM_GRAY,
    Attention.CI_FAILED: ANSI_RED,
    Attention.UNRESOLVED_COMMENTS: ANSI_YELLOW,
    Attention.NO_REVIEWERS: ANSI_DIM_GRAY,
    SUBMITTABLE: ANSI_GREEN,
    Attention.AHEAD_OF_GERRIT: ANSI_YELLOW,
    Attention.DIVERGED: ANSI_RED,
    Attention.OUTDATED_PATCHSET: ANSI_RED,
    Attention.REVIEW_ISSUES: ANSI_YELLOW,
    Attention.CHAIN_BLOCKED: ANSI_DIM_GRAY,
}
"""Colour per hint key; a terminal hint not listed (missing Change-Id, not-pushed, …) is yellow."""


def attention_tokens(commit: LogCommit) -> list[tuple[str, str]]:
    """Attention (text, ANSI-color) pairs for the trailing annotation column: :func:`attention_hints`, coloured."""
    return [(text, _HINT_COLOR.get(key, ANSI_YELLOW)) for key, text in attention_hints(commit)]


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
) -> int:
    widths = [
        visible_len(
            primary_line(
                commit,
                summary_highlighter=summary_highlighter,
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
    attention_col: int = 0,
) -> str:
    """Oneline text through attention suffix; excludes Gerrit URL."""
    base = primary_line(
        commit,
        summary_highlighter=summary_highlighter,
    )
    suffix = attention_suffix(commit)
    if suffix:
        gap = max(2, attention_col - visible_len(base)) if attention_col else 2
        base = f"{base}{' ' * gap}{suffix}"
    return base


def attention_summary(commit: LogCommit) -> str:
    """One line on what *commit* needs: its attention reasons, else whether Gerrit has it."""
    if commit.attention_reasons:
        return "attention: " + ", ".join(commit.attention_reasons)
    if not commit.pushed:
        return "not on Gerrit"
    return "ok"


def votes_text(commit: LogCommit) -> str:
    """Who voted what: ``Code-Review +2 Alice, -1 Bob · Verified +1 CI``; empty without votes."""
    by_label: dict[str, list[str]] = {}
    for vote in commit.votes:
        by_label.setdefault(vote.label, []).append(f"{vote.value:+d} {vote.by}")
    return " · ".join(f"{label} {', '.join(votes)}" for label, votes in by_label.items())


def show_headline(commit: LogCommit) -> str:
    """``commit <sha> <status cols>  # <attention>`` — same tokens as ``ger log``."""
    sha = color_short_sha(commit.short_sha)
    push = fmt_patchset_column(commit)
    if commit.pushed:
        verified = fmt_verified(commit.verified)
        cr = fmt_code_review(commit.code_review)
        comments = fmt_comments(commit.comments_unresolved)
    else:
        verified = "   "
        cr = "    "
        comments = "   "
    base = f"commit {sha} {push} {verified} {cr} {comments}"
    suffix = attention_suffix(commit)
    if suffix:
        return f"{base}  {suffix}"
    return base


def oneline_line(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    include_url: bool,
    attention_col: int = 0,
    url_start_visible: int | None = None,
) -> str:
    body = oneline_body(
        commit,
        summary_highlighter=summary_highlighter,
        attention_col=attention_col,
    )
    if include_url and commit.gerrit_url:
        url_text = color_text(format_link(commit.gerrit_url), ANSI_DIM)
        if url_start_visible is not None:
            pad = max(url_start_visible - visible_len(body), 2)
            return f"{body}{' ' * pad}{url_text}"
        return f"{body}  {url_text}"
    return body
