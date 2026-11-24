"""``ger log``'s text view: the commit rows, the ready boundary line, the summary and the ``next:`` lines."""

from __future__ import annotations

from dataclasses import dataclass

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.rebase_state import RebaseProgress, RebaseStop
from gerrit_workflow_tools.core.rebasing_stack import RebaseStep, RebasingStack
from gerrit_workflow_tools.core.upstream_moved import UpstreamMoved
from gerrit_workflow_tools.render.commit_row import (
    attention_column,
    continuation_indent,
    continuation_lines,
    fit_subjects,
    oneline_body,
    oneline_line,
)
from gerrit_workflow_tools.render.style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_YELLOW,
    color_stat,
    color_text,
    visible_len,
)
from gerrit_workflow_tools.render.wrap import wrap_hanging
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


# Readable without colour (piped, NO_COLOR): commits above it are what `ger push` sends.
READY_BOUNDARY_MARKER = "---- ready boundary: `ger push` sends the commits above ----"


@dataclass(frozen=True)
class RowOptions:
    """What each ``ger log`` row shows beyond its primary line."""

    verbose_level: int = 0
    show_url: bool = False
    show_change_id: bool = False
    width: int | None = None
    """The terminal's columns: each subject is cut to fit its row in them. ``None``: nothing is cut."""


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[LogCommit]) -> dict[str, int]:
    """Return per-category counts for summary printing."""
    counts: dict[str, int] = {
        "ci-failures": 0,
        Attention.UNRESOLVED_COMMENTS: 0,
        "on-gerrit": 0,
        Attention.WIP: 0,
    }
    for c in commits:
        if c.pushed and Attention.CI_FAILED in c.attention_reasons:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts[Attention.UNRESOLVED_COMMENTS] += 1
        if c.pushed:
            counts["on-gerrit"] += 1
        if Attention.WIP in c.attention_reasons:
            counts[Attention.WIP] += 1
    return counts


def summary_line(commits: list[LogCommit], ready_n: int, *, width: int | None = None) -> str:
    """``summary: ready N/M · …`` with optional ANSI styling; broken between stats to fit *width*."""
    summary = _build_summary(commits)
    stats = [color_stat("ready ", f"{ready_n}/{len(commits)}", ANSI_GREEN)]
    for label, count, color in (
        ("CI ", summary.get("ci-failures", 0), ANSI_RED),
        ("comments ", summary.get(Attention.UNRESOLVED_COMMENTS, 0), ANSI_YELLOW),
        ("on-gerrit ", summary.get("on-gerrit", 0), ANSI_CYAN),
        ("WIP ", summary.get(Attention.WIP, 0), ANSI_YELLOW),
    ):
        if count:
            stats.append(color_stat(label, count, color))
    return _pack(color_text("summary:", f"{ANSI_BOLD}{ANSI_CYAN}"), stats, width=width)


def _pack(head: str, items: list[str], *, width: int | None) -> str:
    """``head item · item · …``, a new line (indented under the first item) where the next would not fit."""
    sep = color_text(" · ", ANSI_DIM)
    indent = visible_len(head) + 1
    lines = [f"{head} {items[0]}"] if items else [head]
    for item in items[1:]:
        if width and visible_len(lines[-1]) + visible_len(sep) + visible_len(item) > width:
            lines.append(" " * indent + item)
        else:
            lines[-1] += sep + item
    return "\n".join(lines)


def footer_lines(lines: list[str], *, width: int | None) -> list[str]:
    """The ``next:`` and other closing lines, dim; wrapped at word boundaries to fit *width*."""
    out: list[str] = []
    for line in lines:
        parts = wrap_hanging(line, width, indent=6 if line.startswith("next: ") else 2) if width else [line]
        out.extend(color_text(part, ANSI_DIM) for part in parts)
    return out


def next_steps(commits: list[LogCommit], upstream: UpstreamMoved | None = None) -> list[str]:
    """One ``next:`` line per state the stack is in that has a ger command for it, oldest commit first."""
    lines: list[str] = []
    if upstream and upstream.behind:
        lines.append(f"next: {upstream.advice()}")
    merged = [c for c in commits if c.patchset_status in (PatchsetStatus.MERGED_SAME, PatchsetStatus.MERGED_DRIFT)]
    if merged and not (upstream and upstream.behind):
        lines.append(
            f"next: {merged[0].short_sha} is merged on Gerrit; drop it and rebase onto the target with "
            "`ger rebase --onto-remote --drop-merged-equivalent`"
        )
    wip = sum(1 for c in commits if c.pushed and Attention.WIP in c.attention_reasons)
    if wip:
        lines.append(f"next: {wip} WIP change{'' if wip == 1 else 's'} on Gerrit; ready them with `ger push --ready`")
    for reason, how in (
        (Attention.DIVERGED, "merge Gerrit's patchset into yours with `ger checkout change:{n} --update`"),
        (Attention.OUTDATED_PATCHSET, "take Gerrit's newer patchset with `ger checkout change:{n} --update`"),
    ):
        hit = next((c for c in commits if reason in c.attention_reasons and c.number), None)
        if hit is not None:
            lines.append(f"next: {hit.short_sha} {reason}: {how.format(n=hit.number)}")
    if any(Attention.CHAIN_BLOCKED in c.attention_reasons for c in commits):
        lines.append(
            "chain blocked: a change below is not submittable yet (votes, CI, comments); it clears when that one is"
        )
    return lines


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _url_start_visible(
    visible: list[LogCommit], *, summary_highlighter: SummaryHighlighter | None, attn_col: int
) -> int | None:
    """Visible column where URLs start in compact one-line output."""
    widths = [
        visible_len(oneline_body(c, summary_highlighter=summary_highlighter, attention_col=attn_col))
        for c in visible
        if c.gerrit_url
    ]
    return max(widths) + 2 if widths else None


def row_lines(
    visible: list[LogCommit],
    options: RowOptions,
    *,
    summary_highlighter: SummaryHighlighter | None,
    blocked: frozenset[str] = frozenset(),
) -> list[str]:
    """The rows of ``ger log``, with a plain-text line where the ready boundary falls."""
    if options.width:
        visible = fit_subjects(visible, options.width, show_url=options.show_url)
    attn_col = attention_column(visible, summary_highlighter=summary_highlighter)
    url_start = (
        _url_start_visible(visible, summary_highlighter=summary_highlighter, attn_col=attn_col)
        if options.show_url
        else None
    )
    boundary = next((c.sha for c in visible if c.sha in blocked), None)
    lines: list[str] = []
    for commit in visible:
        if commit.sha == boundary:
            lines.append(color_text(READY_BOUNDARY_MARKER, ANSI_DIM))
        lines.append(
            oneline_line(
                commit,
                summary_highlighter=summary_highlighter,
                include_url=options.show_url,
                attention_col=attn_col,
                url_start_visible=url_start,
            )
        )
        ind = " " * continuation_indent(commit)
        details = continuation_lines(commit, verbose_level=options.verbose_level, show_change_id=options.show_change_id)
        lines.extend(f"{ind}{line}" for line in details)
    return lines


# ---------------------------------------------------------------------------
# Rebase note
# ---------------------------------------------------------------------------


_STOPS = {
    RebaseStop.EDIT: "stopped{at} to edit it",
    RebaseStop.SPLIT: (
        "stopped{at} to edit it, and HEAD has moved on from it: the commits above it were made at the stop"
    ),
    RebaseStop.CONFLICT: "stopped on a conflict{at} in {paths}",
    RebaseStop.RESOLVED: "stopped{at} with its conflicts resolved, ready for `git rebase --continue`",
    RebaseStop.PAUSED: "stopped between commits",
}


def rebase_note(progress: RebaseProgress, rebasing: RebasingStack | None) -> str:
    """The note above a stack a rebase is underway on: whose, where it stopped, what is shown as it was."""
    current = rebasing.current if rebasing else None
    at = f' at {current.short_sha} "{current.subject}"' if current else ""
    if not current and progress.current and progress.stop is RebaseStop.SPLIT:
        at = f" at {progress.current[:7]}"
    stop = progress.stop
    said = _STOPS[stop].format(at=at, paths=", ".join(progress.unmerged)) if stop else "stopped"
    whose = f"note: a rebase is in progress on {progress.branch or 'a detached HEAD'}, {said}"
    if rebasing is None:
        # No rebasing stack: the backend keeps no todo (the branch as it was), or a range was asked for (as it is).
        shown = (
            "the branch as it was before the rebase" if not progress.keeps_todo else "the range asked for, as it is now"
        )
        return f"{whose}; this shows {shown}."
    todo = rebasing.steps.count(RebaseStep.TODO)
    rest = f"; {todo} commit{'s' if todo != 1 else ''} still to replay, shown as before" if todo else ""
    return f"{whose}{rest}."
