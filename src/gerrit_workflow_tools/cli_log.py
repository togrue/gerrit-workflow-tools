from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.cli_common import HELP_JSON, add_color_args, configure_logging, cwd_from_env
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_STRIKE,
    ANSI_YELLOW,
    color_text,
    init_color_mode,
    is_color_enabled,
    visible_len,
)
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

def _visible_len(s: str) -> int:
    """Length of ``s`` as displayed in a terminal (ANSI color codes omitted)."""
    return visible_len(s)


def _fmt_summary_strike(summary: str, *, use_color: bool) -> str:
    """Strike through the commit summary (ANSI SGR 9, or combining chars without a TTY)."""
    if use_color:
        return f"{ANSI_STRIKE}{summary}{ANSI_RESET}"
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
        return color_text("a", ANSI_DIM, enabled=use_color)
    status = commit.patchset_status
    if status == "active":
        return color_text("p", ANSI_GREEN, enabled=use_color)
    if status == "newer":
        return color_text("n", ANSI_YELLOW, enabled=use_color)
    if status == "outdated":
        return color_text("o", ANSI_RED, enabled=use_color)
    return color_text("-", ANSI_DIM, enabled=use_color)


def _fmt_verified(v: int | None, *, use_color: bool) -> str:
    if v is None:
        return color_text("v? ", ANSI_DIM, enabled=use_color) if use_color else "v? "
    if v >= 1:
        return color_text("v+1", ANSI_GREEN, enabled=use_color)
    if v <= -1:
        return color_text("v-1", ANSI_RED, enabled=use_color)
    return "v0 "


def _fmt_code_review(cr: int | None, *, use_color: bool) -> str:
    if cr is None:
        return color_text("cr? ", ANSI_DIM, enabled=use_color) if use_color else "cr? "
    if cr >= 2:
        return color_text("cr+2", ANSI_GREEN, enabled=use_color)
    if cr == 1:
        return color_text("cr+1", ANSI_LIGHT_GREEN, enabled=use_color)
    if cr == -1:
        return color_text("cr-1", ANSI_YELLOW, enabled=use_color)
    if cr <= -2:
        return color_text("cr-2", ANSI_RED, enabled=use_color)
    return "cr0 "


def _fmt_comments(count: int, *, use_color: bool) -> str:
    if count > 0:
        return color_text("com", ANSI_YELLOW, enabled=use_color)
    return "   "


def _primary_line_prefix(commit: LogCommit, *, use_color: bool) -> str:
    """Text before the subject on the primary line (through ``  # ``), same as in :func:`_primary_line`."""
    sha = commit.short_sha
    push = _fmt_patchset_column(commit, use_color=use_color)
    verified = _fmt_verified(commit.verified, use_color=use_color)
    cr = _fmt_code_review(commit.code_review, use_color=use_color)
    comments = _fmt_comments(commit.comments_unresolved, use_color=use_color)
    return f"{sha} {push} {verified} {cr} {comments} # "


def _continuation_indent(commit: LogCommit, *, use_color: bool) -> int:
    """Column where the subject starts; continuation lines align using :func:`_visible_len` on the prefix."""
    return _visible_len(_primary_line_prefix(commit, use_color=use_color))


def _url_line(url: str, *, use_color: bool) -> str:
    return color_text(url, ANSI_DIM, enabled=use_color)


def _detail_lines(commit: LogCommit, *, use_color: bool) -> list[str]:
    lines: list[str] = []
    if commit.ci_failures:
        text = f"# failed: {', '.join(commit.ci_failures)}"
        lines.append(color_text(text, ANSI_RED, enabled=use_color))
    elif commit.verified is not None and commit.verified <= -1:
        lines.append(color_text("# failed", ANSI_RED, enabled=use_color))
    if commit.comments_unresolved > 0:
        text = f"# comments: {commit.comments_unresolved} unresolved"
        lines.append(color_text(text, ANSI_YELLOW, enabled=use_color))
    return lines


# Primary line format: {sha} {patchset p/n/o/-} {verified} {code_review} {comments}  # {summary}
# Continuation indent = visible width of that prefix (colors ignored), so it stays aligned with ``_fmt_*``.


def _fmt_change_id_suffix(change_id: str | None, *, use_color: bool) -> str:
    if not change_id:
        return ""
    disp = change_id if len(change_id) <= 14 else change_id[:12] + "…"
    return color_text(f"  {disp}", ANSI_DIM, enabled=use_color)


def _primary_line(commit: LogCommit, *, use_color: bool, show_change_id: bool = False) -> str:
    summ = _fmt_summary_strike(commit.summary, use_color=use_color) if commit.abandoned else commit.summary
    line = f"{_primary_line_prefix(commit, use_color=use_color)}{summ}"
    if show_change_id:
        line += _fmt_change_id_suffix(commit.change_id, use_color=use_color)
    return line


def _attention_tokens(commit: LogCommit) -> list[tuple[str, str]]:
    if commit.abandoned:
        return [("abandoned", ANSI_RED)]

    tokens: list[tuple[str, str]] = []
    if commit.ci_failures or (commit.verified is not None and commit.verified <= -1):
        tokens.append(("build failed", ANSI_RED))
    if commit.comments_unresolved > 0:
        noun = "comment" if commit.comments_unresolved == 1 else "comments"
        tokens.append((f"{commit.comments_unresolved} unresolved {noun}", ANSI_YELLOW))
    if commit.submittable and not tokens:
        tokens.append(("submittable", ANSI_GREEN))
    return tokens


def _attention_suffix(commit: LogCommit, *, use_color: bool) -> str:
    tokens = _attention_tokens(commit)
    if not tokens:
        return ""

    rendered: list[str] = [color_text("# ", ANSI_DIM, enabled=use_color)]
    for idx, (text, code) in enumerate(tokens):
        if idx:
            rendered.append(color_text(", ", ANSI_DIM, enabled=use_color))
        rendered.append(color_text(text, code, enabled=use_color))
    return "".join(rendered)


def _attention_column(commits: list[LogCommit], *, use_color: bool, show_change_id: bool = False) -> int:
    widths = [
        _visible_len(_primary_line(commit, use_color=use_color, show_change_id=show_change_id))
        for commit in commits
        if _attention_tokens(commit)
    ]
    if not widths:
        return 0
    return max(widths) + 2


def _oneline_line(
    commit: LogCommit,
    *,
    use_color: bool,
    include_url: bool,
    show_change_id: bool = False,
    attention_column: int = 0,
) -> str:
    base = _primary_line(commit, use_color=use_color, show_change_id=show_change_id)
    attention = _attention_suffix(commit, use_color=use_color)
    if attention:
        gap = max(2, attention_column - _visible_len(base)) if attention_column else 2
        base = f"{base}{' ' * gap}{attention}"
    if include_url and commit.gerrit_url:
        base = f"{base}  {_url_line(commit.gerrit_url, use_color=use_color)}"
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
        parts.append(color_text(label, f"{ANSI_BOLD}{ANSI_CYAN}", enabled=True))
        parts.append(" ")
        parts.append(color_text("ready ", ANSI_DIM, enabled=True))
        parts.append(color_text(f"{ready_n}/{total_n}", ANSI_GREEN, enabled=True))
    else:
        parts.append(f"{label} ready {ready_n}/{total_n}")

    ci = summary.get("ci-failures", 0)
    if ci:
        if use_color:
            parts.append(color_text(sep, ANSI_DIM, enabled=True))
            parts.append(color_text("CI ", ANSI_DIM, enabled=True))
            parts.append(color_text(str(ci), ANSI_RED, enabled=True))
        else:
            parts.append(f"{sep}CI {ci}")

    unres = summary.get("unresolved-comments", 0)
    if unres:
        if use_color:
            parts.append(color_text(sep, ANSI_DIM, enabled=True))
            parts.append(color_text("comments ", ANSI_DIM, enabled=True))
            parts.append(color_text(str(unres), ANSI_YELLOW, enabled=True))
        else:
            parts.append(f"{sep}comments {unres}")

    review = summary.get("awaiting-review", 0)
    if review:
        if use_color:
            parts.append(color_text(sep, ANSI_DIM, enabled=True))
            parts.append(color_text("review ", ANSI_DIM, enabled=True))
            parts.append(color_text(str(review), ANSI_CYAN, enabled=True))
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
    add_color_args(p)
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
    init_color_mode(no_color=args.no_color)
    use_color = is_color_enabled()
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
    attention_column = _attention_column(visible, use_color=use_color, show_change_id=show_change_id)
    for commit in visible:
        if use_compact:
            primary = _compact_line(commit, show_change_id=show_change_id)
            print(primary)
            if show_url and commit.gerrit_url:
                ind = " " * (_visible_len(primary) + 2)
                print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
        elif use_oneline:
            print(
                _oneline_line(
                    commit,
                    use_color=use_color,
                    include_url=show_url,
                    show_change_id=show_change_id,
                    attention_column=attention_column,
                )
            )
        else:
            print(
                _oneline_line(
                    commit,
                    use_color=use_color,
                    include_url=show_url,
                    show_change_id=show_change_id,
                    attention_column=attention_column,
                )
            )

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
