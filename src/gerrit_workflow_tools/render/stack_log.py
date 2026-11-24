"""``ger log``'s text view: the commit rows, the ready boundary line, the summary and the ``next:`` lines."""

from __future__ import annotations

from dataclasses import dataclass

from gerrit_workflow_tools.core.annotated_stack import NextStep, NextStepKind, StackOverview
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
from gerrit_workflow_tools.render.status_fmt import chain_blocked_text
from gerrit_workflow_tools.render.style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_YELLOW,
    color_text,
    visible_len,
)
from gerrit_workflow_tools.render.wrap import wrap_hanging
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


# Readable without colour (piped, NO_COLOR): commits above it are what `ger push` sends.
READY_BOUNDARY_MARKER = "---- ready boundary: `ger push` sends the commits above ----"

_MERGED = (PatchsetStatus.MERGED_SAME, PatchsetStatus.MERGED_DRIFT, PatchsetStatus.MERGED_UNKNOWN)


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


def _stat(count: int, label: str, color: str) -> str:
    return color_text(str(count), color) + color_text(f" {label}", ANSI_DIM)


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


def summary_line(overview: StackOverview, *, width: int | None = None) -> str:
    """``summary: 4 on Gerrit · 1 to upload · …``: each count names what it counts; broken to fit *width*."""
    commits = overview.view.commits
    merged = sum(1 for c in commits if c.patchset_status in _MERGED)
    abandoned = sum(1 for c in commits if c.abandoned)
    commented = sum(1 for c in commits if c.comments_unresolved > 0)
    counts = (
        (
            sum(1 for c in commits if c.pushed and not c.abandoned and c.patchset_status not in _MERGED),
            "on Gerrit",
            ANSI_CYAN,
        ),
        (len(overview.to_upload), "to upload", ANSI_GREEN),
        (len(overview.blocked), "held back", ANSI_YELLOW),
        (merged, "merged", ANSI_GREEN),
        (abandoned, "abandoned", ANSI_RED),
        (sum(1 for c in commits if c.pushed and Attention.CI_FAILED in c.attention_reasons), "failed CI", ANSI_RED),
        (commented, f"{_plural(commented, 'commit')} with comments", ANSI_YELLOW),
        (sum(1 for c in commits if Attention.WIP in c.attention_reasons), "WIP", ANSI_YELLOW),
    )
    stats = [_stat(count, label, color) for count, label, color in counts if count]
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


def _step_text(step: NextStep, moved: UpstreamMoved | None) -> str:
    first = step.commits[0] if step.commits else None
    sha, n = (first.short_sha, first.number) if first else ("", None)
    count = len(step.commits)
    match step.kind:
        case NextStepKind.UPSTREAM_MOVED:
            return f"next: {moved.advice() if moved else 'rebase onto the upstream'}"
        case NextStepKind.MERGED:
            return (
                f"next: {sha} is merged on Gerrit; drop it and rebase onto the target with "
                "`ger rebase --onto-remote --drop-merged-equivalent`"
            )
        case NextStepKind.ABANDONED:
            return f"next: {sha} is abandoned on Gerrit; restore it there or drop the commit (`ger edit --drop {sha}`)"
        case NextStepKind.DIVERGED:
            how = f"merge Gerrit's patchset into yours with `ger checkout change:{n} --update`"
            return f"next: {sha} diverged from Gerrit: {how}"
        case NextStepKind.OUTDATED:
            return f"next: {sha} has a newer patchset on Gerrit: take it with `ger checkout change:{n} --update`"
        case NextStepKind.UPLOAD:
            return f"next: {count} {_plural(count, 'commit')} to upload with `ger push`"
        case NextStepKind.WIP:
            push = "ger push --all --ready" if step.beyond_boundary else "ger push --ready"
            return f"next: {count} WIP {_plural(count, 'change')} on Gerrit; ready them with `{push}`"
        case NextStepKind.CHAIN_BLOCKED:
            return chain_blocked_text(step.commits[0])
    raise AssertionError(step.kind)


def next_steps(overview: StackOverview) -> list[str]:
    """One line per next step (:meth:`StackOverview.next_steps`): ``next: …`` with the ger command for it."""
    return [_step_text(step, overview.moved) for step in overview.next_steps()]


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
