from __future__ import annotations

import argparse
import json
import logging
import re
import sys

from gerrit_workflow_tools.cli_common import HELP_JSON, configure_logging, cwd_from_env
from gerrit_workflow_tools.config import log_defaults
from gerrit_workflow_tools.gerrit_change_status import (
    LogCommit,
    determine_attention,
    fetch_gerrit_data,
)
from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.stack import (
    merge_base_with_target,
    parse_change_id,
    stack_commits_metadata_one_log,
)

logger = logging.getLogger(__name__)

# ANSI color codes
_RESET = "\033[0m"
_DIM = "\033[2m"
_STRIKE = "\033[9m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_LIGHT_GREEN = "\033[92m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"

# Terminal SGR sequences (same family as ``_color``); used only to measure visible width.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Length of ``s`` as displayed in a terminal (ANSI color codes omitted)."""
    return len(_ANSI_ESCAPE_RE.sub("", s))


def _color(text: str, code: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{_RESET}"


def _fmt_summary_strike(summary: str, *, use_color: bool) -> str:
    """Strike through the commit summary (ANSI SGR 9, or combining chars without a TTY)."""
    if use_color:
        return f"{_STRIKE}{summary}{_RESET}"
    return "".join(f"{c}\u0336" for c in summary)


def _annotate_attention(commits: list[LogCommit]) -> None:
    """Populate attention_reasons on each commit, including chain-blocking."""
    for i, commit in enumerate(commits):
        chain_blocked = False
        if commit.pushed:
            for earlier in commits[:i]:
                if earlier.pushed and not earlier.submittable:
                    chain_blocked = True
                    break
        commit.attention_reasons = determine_attention(commit, chain_blocked=chain_blocked)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_patchset_column(commit: LogCommit, *, use_color: bool) -> str:
    """Single-letter column: current patch set / local ahead / outdated / not on Gerrit."""
    if commit.abandoned:
        return _color("a", _DIM, use_color=use_color)
    status = commit.patchset_status
    if status == "active":
        return _color("p", _GREEN, use_color=use_color)
    if status == "newer":
        return _color("n", _YELLOW, use_color=use_color)
    if status == "outdated":
        return _color("o", _RED, use_color=use_color)
    return _color("-", _DIM, use_color=use_color)


def _fmt_verified(v: int | None, *, use_color: bool) -> str:
    if v is None:
        return _color(" · ", _DIM, use_color=use_color) if use_color else " · "
    if v >= 1:
        return _color("v+1", _GREEN, use_color=use_color)
    if v <= -1:
        return _color("v-1", _RED, use_color=use_color)
    return "   "


def _fmt_code_review(cr: int | None, *, use_color: bool) -> str:
    if cr is None:
        return _color("  · ", _DIM, use_color=use_color) if use_color else "  · "
    if cr >= 2:
        return _color("cr+2", _GREEN, use_color=use_color)
    if cr == 1:
        return _color("cr+1", _LIGHT_GREEN, use_color=use_color)
    if cr == -1:
        return _color("cr-1", _YELLOW, use_color=use_color)
    if cr <= -2:
        return _color("cr-2", _RED, use_color=use_color)
    return "    "


def _fmt_comments(count: int, *, use_color: bool) -> str:
    if count > 0:
        return _color("com", _YELLOW, use_color=use_color)
    return "   "


def _fmt_submittable(commit: LogCommit, *, use_color: bool) -> str:
    """Narrow column: submittable checkmark vs not, or blank when not on Gerrit."""
    if not commit.pushed:
        return "  "
    if commit.submittable:
        return _color("✓", _GREEN, use_color=use_color) + " "
    return _color("·", _DIM, use_color=use_color) + " "


def _primary_line_prefix(commit: LogCommit, *, use_color: bool) -> str:
    """Text before the subject on the primary line (through ``  # ``), same as in :func:`_primary_line`."""
    sha = commit.short_sha
    push = _fmt_patchset_column(commit, use_color=use_color)
    verified = _fmt_verified(commit.verified, use_color=use_color)
    cr = _fmt_code_review(commit.code_review, use_color=use_color)
    comments = _fmt_comments(commit.comments_unresolved, use_color=use_color)
    subm = _fmt_submittable(commit, use_color=use_color)
    return f"{sha} {push} {verified} {cr} {comments} {subm} # "


def _continuation_indent(commit: LogCommit, *, use_color: bool) -> int:
    """Column where the subject starts; continuation lines align using :func:`_visible_len` on the prefix."""
    return _visible_len(_primary_line_prefix(commit, use_color=use_color))


def _url_line(url: str, *, use_color: bool) -> str:
    return _color(url, _DIM, use_color=use_color)


def _detail_lines(commit: LogCommit, *, use_color: bool) -> list[str]:
    lines: list[str] = []
    if commit.ci_failures:
        text = f"# failed: {', '.join(commit.ci_failures)}"
        lines.append(_color(text, _RED, use_color=use_color))
    elif commit.verified is not None and commit.verified <= -1:
        lines.append(_color("# failed", _RED, use_color=use_color))
    if commit.comments_unresolved > 0:
        text = f"# comments: {commit.comments_unresolved} unresolved"
        lines.append(_color(text, _YELLOW, use_color=use_color))
    return lines


# Primary line format: {sha} {patchset p/n/o/-} {verified} {code_review} {comments}  # {summary}
# Continuation indent = visible width of that prefix (colors ignored), so it stays aligned with ``_fmt_*``.


def _fmt_change_id_suffix(change_id: str | None, *, use_color: bool) -> str:
    if not change_id:
        return ""
    disp = change_id if len(change_id) <= 14 else change_id[:12] + "…"
    return _color(f"  {disp}", _DIM, use_color=use_color)


def _primary_line(commit: LogCommit, *, use_color: bool, show_change_id: bool = False) -> str:
    summ = _fmt_summary_strike(commit.summary, use_color=use_color) if commit.abandoned else commit.summary
    line = f"{_primary_line_prefix(commit, use_color=use_color)}{summ}"
    if show_change_id:
        line += _fmt_change_id_suffix(commit.change_id, use_color=use_color)
    return line


def _oneline_line(
    commit: LogCommit,
    *,
    use_color: bool,
    include_url: bool,
    show_change_id: bool = False,
) -> str:
    base = _primary_line(commit, use_color=use_color, show_change_id=show_change_id)
    if include_url and commit.gerrit_url:
        base = f"{base}  {_url_line(commit.gerrit_url, use_color=use_color)}"
    extras = _detail_lines(commit, use_color=False)  # strip color for inline
    if extras:
        suffix = "  " + "  ".join(extras)
        return base + suffix
    return base


# Compact format: {sha:7} {p|n|o|-} {v} {cr} {com}
#   verified: +1 / -1 / .
#   code_review: +2 / +1 / -1 / -2 / .
#   comments: c / .


def _compact_verified(v: int | None) -> str:
    if v is None:
        return "."
    if v >= 1:
        return "+1"
    if v <= -1:
        return "-1"
    return "."


def _compact_cr(cr: int | None) -> str:
    if cr is None:
        return "."
    if cr >= 2:
        return "+2"
    if cr == 1:
        return "+1"
    if cr == -1:
        return "-1"
    if cr <= -2:
        return "-2"
    return "."


def _compact_patchset_letter(commit: LogCommit) -> str:
    if commit.abandoned:
        return "a"
    status = commit.patchset_status
    if status == "active":
        return "p"
    if status == "newer":
        return "n"
    if status == "outdated":
        return "o"
    return "-"


def _compact_line(commit: LogCommit, *, show_change_id: bool = False) -> str:
    push = _compact_patchset_letter(commit)
    v = _compact_verified(commit.verified)
    cr = _compact_cr(commit.code_review)
    if not commit.pushed:
        sub = "-"
    elif commit.submittable:
        sub = "+"
    else:
        sub = "."
    com = "c" if commit.comments_unresolved else "."
    line = f"{commit.short_sha} {push} {v} {cr} {sub}{com}"
    if show_change_id and commit.change_id:
        cid = commit.change_id
        if len(cid) > 14:
            cid = cid[:12] + "…"
        line += f"  {cid}"
    return line


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[LogCommit]) -> tuple[dict[str, int], int, int]:
    """Return (per-category counts, ready count, total commits) for summary printing."""
    counts: dict[str, int] = {
        "ci-failures": 0,
        "unresolved-comments": 0,
        "awaiting-review": 0,
    }
    ready = 0
    total = len(commits)
    for c in commits:
        if c.patchset_status in ("absent", "newer"):
            ready += 1
        if c.pushed and c.verified is not None and c.verified <= -1:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts["unresolved-comments"] += 1
        # awaiting-review = pushed commits that still need attention
        if c.pushed and c.attention_reasons:
            counts["awaiting-review"] += 1
    return counts, ready, total


def _format_summary_dashboard_line(
    summary: dict[str, int],
    ready_n: int,
    total_n: int,
    *,
    use_color: bool,
) -> str:
    """Single-line summary: ``summary: ready N/M · …`` with optional ANSI styling."""
    sep = " · "
    parts: list[str] = []

    label = "summary:"
    if use_color:
        parts.append(_color(label, f"{_BOLD}{_CYAN}", use_color=True))
        parts.append(" ")
        parts.append(_color("ready ", _DIM, use_color=True))
        parts.append(_color(f"{ready_n}/{total_n}", _GREEN, use_color=True))
    else:
        parts.append(f"{label} ready {ready_n}/{total_n}")

    ci = summary.get("ci-failures", 0)
    if ci:
        if use_color:
            parts.append(_color(sep, _DIM, use_color=True))
            parts.append(_color("CI ", _DIM, use_color=True))
            parts.append(_color(str(ci), _RED, use_color=True))
        else:
            parts.append(f"{sep}CI {ci}")

    unres = summary.get("unresolved-comments", 0)
    if unres:
        if use_color:
            parts.append(_color(sep, _DIM, use_color=True))
            parts.append(_color("comments ", _DIM, use_color=True))
            parts.append(_color(str(unres), _YELLOW, use_color=True))
        else:
            parts.append(f"{sep}comments {unres}")

    review = summary.get("awaiting-review", 0)
    if review:
        if use_color:
            parts.append(_color(sep, _DIM, use_color=True))
            parts.append(_color("review ", _DIM, use_color=True))
            parts.append(_color(str(review), _CYAN, use_color=True))
        else:
            parts.append(f"{sep}review {review}")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    p = argparse.ArgumentParser(
        prog="ger log",
        description="Compact, actionable overview of the local commit chain vs Gerrit.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Show all commits in the range, not only attention-required ones.",
    )
    p.add_argument(
        "--oneline",
        action="store_true",
        help="Use one line per commit (suppress detail lines).",
    )
    p.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    p.add_argument("--no-color", action="store_true", help="Disable colored output.")
    p.add_argument(
        "--url",
        "--show-url",
        action="store_true",
        dest="url",
        help=(
            "Include each change's Gerrit web URL in text output (JSON always includes gerrit_url). "
            "Default: ``gerrit.logShowUrl``."
        ),
    )
    p.add_argument(
        "--show-change-id",
        action="store_true",
        help="Append Change-Id to each text line. Default: ``gerrit.logShowChangeId``.",
    )
    p.add_argument(
        "--no-oneline",
        action="store_true",
        help="Override ``gerrit.logOneline`` when set.",
    )
    p.add_argument(
        "--no-compact",
        action="store_true",
        help="Override ``gerrit.logCompact`` when set.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log git commands to stderr.",
    )
    p.add_argument(
        "rev_range",
        nargs="?",
        default=None,
        metavar="REV_RANGE",
        help="Commit range (e.g. origin/main..HEAD); default merge-base..HEAD.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)

    cwd = cwd_from_env()
    use_color = not args.no_color and sys.stdout.isatty()
    gdef = log_defaults(cwd)
    show_url = bool(args.url) or gdef["show_url"]
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"]
    use_oneline = bool(args.oneline) or (gdef["oneline"] and not args.no_oneline)
    use_compact = gdef["compact"] and not args.no_compact

    # Determine commit range
    if args.rev_range:
        rev_range = args.rev_range
    else:
        try:
            mb, _target, _base_ref = merge_base_with_target(cwd)
        except GitError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        rev_range = f"{mb}..HEAD"

    rows = stack_commits_metadata_one_log(cwd, rev_range)
    if not rows:
        print("(no commits in range)")
        return 0

    commit_data: list[tuple[str, str, str, str | None]] = [
        (sha, short, sub, parse_change_id(raw)) for sha, short, sub, raw in rows
    ]

    # Connect to Gerrit
    try:
        web_base = resolve_gerrit_web_base(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    client = GerritClient(web_base, cwd=str(cwd))

    try:
        commits = fetch_gerrit_data(client, web_base, commit_data)
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return 3

    _annotate_attention(commits)

    visible = commits if args.full else [c for c in commits if c.attention_reasons]

    # JSON output
    if args.json_:
        payload = [
            {
                "sha": c.sha,
                "summary": c.summary,
                "pushed": c.pushed,
                "patchset_status": c.patchset_status,
                "verified": c.verified,
                "code_review": c.code_review,
                "comments_unresolved": c.comments_unresolved,
                "ci_failures": c.ci_failures,
                "gerrit_url": c.gerrit_url,
                "submittable": c.submittable,
                "change_id": c.change_id,
                "abandoned": c.abandoned,
                "attention_reasons": c.attention_reasons,
            }
            for c in visible
        ]
        print(json.dumps(payload, indent=2))
        return 1 if any(c.attention_reasons for c in commits) else 0

    # Text output
    prev_had_details = False
    for commit in visible:
        if prev_had_details:
            print()
        if use_compact:
            primary = _compact_line(commit, show_change_id=show_change_id)
            print(primary)
            if show_url and commit.gerrit_url:
                ind = " " * (_visible_len(primary) + 2)
                print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
            prev_had_details = False
        elif use_oneline:
            print(
                _oneline_line(
                    commit,
                    use_color=use_color,
                    include_url=show_url,
                    show_change_id=show_change_id,
                )
            )
            prev_had_details = False
        else:
            print(_primary_line(commit, use_color=use_color, show_change_id=show_change_id))
            ind = " " * _continuation_indent(commit, use_color=use_color)
            if show_url and commit.gerrit_url:
                print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
            details = _detail_lines(commit, use_color=use_color)
            for d in details:
                print(f"{ind}{d}")
            prev_had_details = bool(details) or bool(show_url and commit.gerrit_url)

    # Summary section (suppressed for --oneline and compact text mode)
    if not use_oneline and not use_compact:
        summary, ready_n, total_n = _build_summary(commits)
        print()
        print(
            _format_summary_dashboard_line(
                summary,
                ready_n,
                total_n,
                use_color=use_color,
            )
        )

    return 1 if any(c.attention_reasons for c in commits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
