from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env
from gerrit_workflow_tools.gerrit_change_status import (
    GlogCommit,
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
_GREEN = "\033[32m"
_RED = "\033[31m"
_LIGHT_GREEN = "\033[92m"
_YELLOW = "\033[33m"

# Terminal SGR sequences (same family as ``_color``); used only to measure visible width.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Length of ``s`` as displayed in a terminal (ANSI color codes omitted)."""
    return len(_ANSI_ESCAPE_RE.sub("", s))


def _color(text: str, code: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{_RESET}"


def _annotate_attention(commits: list[GlogCommit]) -> None:
    """Populate attention_reasons on each commit, including chain-blocking."""
    for i, commit in enumerate(commits):
        chain_blocked = False
        if commit.pushed:
            for earlier in commits[:i]:
                if earlier.pushed and not earlier.submittable:
                    chain_blocked = True
                    break
        commit.attention_reasons = determine_attention(
            commit, chain_blocked=chain_blocked
        )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_patchset_column(status: str, *, use_color: bool) -> str:
    """Single-letter column: current patch set / local ahead / outdated / not on Gerrit."""
    if status == "active":
        return _color("p", _GREEN, use_color=use_color)
    if status == "newer":
        return _color("n", _YELLOW, use_color=use_color)
    if status == "outdated":
        return _color("o", _RED, use_color=use_color)
    return _color("-", _DIM, use_color=use_color)


def _fmt_verified(v: int | None, *, use_color: bool) -> str:
    if v is None:
        return "   "
    if v >= 1:
        return _color("v+1", _GREEN, use_color=use_color)
    if v <= -1:
        return _color("v-1", _RED, use_color=use_color)
    return "   "


def _fmt_code_review(cr: int | None, *, use_color: bool) -> str:
    if cr is None:
        return "    "
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


def _primary_line_prefix(commit: GlogCommit, *, use_color: bool) -> str:
    """Text before the subject on the primary line (through ``  # ``), same as in :func:`_primary_line`."""
    sha = commit.short_sha
    push = _fmt_patchset_column(commit.patchset_status, use_color=use_color)
    verified = _fmt_verified(commit.verified, use_color=use_color)
    cr = _fmt_code_review(commit.code_review, use_color=use_color)
    comments = _fmt_comments(commit.comments_unresolved, use_color=use_color)
    return f"{sha} {push} {verified} {cr} {comments}  # "


def _continuation_indent(commit: GlogCommit, *, use_color: bool) -> int:
    """Column where the subject starts; continuation lines align using :func:`_visible_len` on the prefix."""
    return _visible_len(_primary_line_prefix(commit, use_color=use_color))


def _url_line(url: str, *, use_color: bool) -> str:
    return _color(url, _DIM, use_color=use_color)


def _detail_lines(commit: GlogCommit, *, use_color: bool) -> list[str]:
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


def _primary_line(commit: GlogCommit, *, use_color: bool) -> str:
    return f"{_primary_line_prefix(commit, use_color=use_color)}{commit.summary}"


def _oneline_line(commit: GlogCommit, *, use_color: bool, include_url: bool) -> str:
    base = _primary_line(commit, use_color=use_color)
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


def _compact_patchset_letter(status: str) -> str:
    if status == "active":
        return "p"
    if status == "newer":
        return "n"
    if status == "outdated":
        return "o"
    return "-"


def _compact_line(commit: GlogCommit) -> str:
    push = _compact_patchset_letter(commit.patchset_status)
    v = _compact_verified(commit.verified)
    cr = _compact_cr(commit.code_review)
    com = "c" if commit.comments_unresolved else "."
    return f"{commit.short_sha} {push} {v} {cr} {com}"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[GlogCommit]) -> dict[str, int]:
    counts: dict[str, int] = {
        "ready-to-push": 0,
        "ci-failures": 0,
        "unresolved-comments": 0,
        "awaiting-review": 0,
    }
    for c in commits:
        if c.patchset_status in ("absent", "newer"):
            counts["ready-to-push"] += 1
        if c.pushed and c.verified is not None and c.verified <= -1:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts["unresolved-comments"] += 1
        # awaiting-review = pushed commits that still need attention
        if c.pushed and c.attention_reasons:
            counts["awaiting-review"] += 1
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git glog``: show local commits vs Gerrit labels, comments, and CI status."""
    p = argparse.ArgumentParser(
        prog="git glog",
        description="Compact, actionable overview of the local commit chain vs Gerrit.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="show all commits, not just attention-required",
    )
    p.add_argument(
        "--oneline",
        action="store_true",
        help="one line per commit (suppress detail lines)",
    )
    p.add_argument(
        "--json", action="store_true", dest="json_", help="machine-readable JSON output"
    )
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    p.add_argument(
        "--compact",
        action="store_true",
        help="compact single-character status representation",
    )
    p.add_argument(
        "--url",
        action="store_true",
        help="include each change's Gerrit web URL in text output (JSON always includes gerrit_url)",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="log git commands to stderr"
    )
    p.add_argument(
        "revset",
        nargs="?",
        default=None,
        metavar="REVSET",
        help="commit range (e.g. origin/main..HEAD); default merge-base..HEAD",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)

    cwd = cwd_from_env()
    use_color = not args.no_color and sys.stdout.isatty()

    # Determine commit range
    if args.revset:
        rev_range = args.revset
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
        if args.compact:
            primary = _compact_line(commit)
            print(primary)
            if args.url and commit.gerrit_url:
                ind = " " * (_visible_len(primary) + 2)
                print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
            prev_had_details = False
        elif args.oneline:
            print(_oneline_line(commit, use_color=use_color, include_url=args.url))
            prev_had_details = False
        else:
            print(_primary_line(commit, use_color=use_color))
            ind = " " * _continuation_indent(commit, use_color=use_color)
            if args.url and commit.gerrit_url:
                print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
            details = _detail_lines(commit, use_color=use_color)
            for d in details:
                print(f"{ind}{d}")
            prev_had_details = bool(details) or bool(args.url and commit.gerrit_url)

    # Summary section (suppressed for --oneline and --compact)
    if not args.oneline and not args.compact:
        summary = _build_summary(commits)
        print()
        print("summary:")
        for key, val in summary.items():
            if val:
                print(f"{key}: {val}")

    return 1 if any(c.attention_reasons for c in commits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
