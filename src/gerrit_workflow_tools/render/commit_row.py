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
    format_link,
    is_color_enabled,
    is_hyperlink_enabled,
    visible_len,
)
from gerrit_workflow_tools.core.ci_links import CiLink, CiPipeline
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


def _ci_state_color(state: str) -> str:
    if state == "FAILED":
        return ANSI_RED
    if state == "SUCCESSFUL":
        return ANSI_GREEN
    return ANSI_DIM


def _format_ci_link_body(link: CiLink) -> str:
    """OSC 8 label when hyperlinks are on; otherwise ``label url``."""
    if is_hyperlink_enabled():
        return format_link(link.url, label=link.label)
    return f"{link.label} {link.url}"


def _format_ci_pipeline_item(pipeline: CiPipeline) -> str:
    color = _ci_state_color(pipeline.state)
    if pipeline.url and is_hyperlink_enabled():
        return color_text(format_link(pipeline.url, label=pipeline.label), color)
    if pipeline.url:
        return color_text(f"{pipeline.label} {pipeline.url}", color)
    return color_text(pipeline.label, color)


def _pipelines_for_display(commit: LogCommit) -> list[CiPipeline]:
    if commit.ci_pipelines:
        return commit.ci_pipelines
    if commit.ci_links:
        return [CiPipeline(label=link.label, state="FAILED", url=link.url) for link in commit.ci_links]
    return [CiPipeline(label=name, state="FAILED", url=None) for name in commit.ci_failures]


def format_ci_lines(commit: LogCommit) -> list[str]:
    """Indented CI continuation lines, or empty when there is no CI data."""
    pipelines = _pipelines_for_display(commit)
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


def continuation_lines(
    commit: LogCommit,
    *,
    verbose_level: int = 0,
    show_change_id: bool = False,
) -> list[str]:
    """Continuation detail lines below the primary oneline row."""
    lines: list[str] = []
    if verbose_level >= 1:
        lines.extend(format_ci_lines(commit))
    if show_change_id:
        change_line = format_change_id_line(commit.change_id)
        if change_line:
            lines.append(change_line)
    return lines


def extra_detail_lines(commit: LogCommit) -> list[str]:
    """CI continuation lines for ``ger show`` and other compact detail views."""
    return format_ci_lines(commit)


def attention_tokens(commit: LogCommit) -> list[tuple[str, str]]:
    """Attention (text, ANSI-color) pairs for the trailing annotation column."""
    if "missing-change-id" in commit.attention_reasons or not commit.change_id:
        return [("missing Change-Id", ANSI_YELLOW)]
    if commit.abandoned:
        return [("abandoned", ANSI_RED)]
    if not commit.pushed:
        return [("not-pushed", ANSI_YELLOW)]
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
