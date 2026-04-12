from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env
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
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_LIGHT_GREEN = "\033[92m"
_YELLOW = "\033[33m"


def _color(text: str, code: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{_RESET}"


@dataclass
class GlogCommit:
    sha: str
    short_sha: str
    summary: str
    change_id: str | None
    pushed: bool
    verified: int | None  # -1, 0, +1; None = no vote
    code_review: int | None  # -2, -1, 0, +1, +2; None = no vote
    comments_unresolved: int
    ci_failures: list[str] = field(default_factory=list)
    gerrit_url: str | None = None
    submittable: bool = False
    attention_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gerrit data extraction helpers
# ---------------------------------------------------------------------------

def _extract_label_value(labels: dict[str, Any], label_name: str) -> int | None:
    """Return the effective vote value for a Gerrit label, or None if no vote."""
    label = labels.get(label_name)
    if not isinstance(label, dict):
        return None
    v = label.get("value")
    if v is not None:
        try:
            iv = int(v)
            # A "value" of 0 with no "all" votes means no vote was cast.
            all_votes = label.get("all", [])
            real_votes = [
                int(vote["value"])
                for vote in (all_votes or [])
                if isinstance(vote, dict) and vote.get("value") is not None
                and int(vote["value"]) != 0
            ]
            if iv == 0 and not real_votes:
                return None
            return iv
        except (TypeError, ValueError):
            pass
    return None


def _count_unresolved(file_map: dict[str, list[dict[str, Any]]]) -> int:
    count = 0
    for comments in file_map.values():
        for c in comments:
            if isinstance(c, dict) and c.get("unresolved") is True:
                count += 1
    return count


def _gerrit_change_url(web_base: str, change: dict[str, Any]) -> str | None:
    proj = change.get("project")
    num = change.get("_number")
    if not proj or not isinstance(num, int):
        return None
    proj_enc = quote(str(proj), safe="")
    return f"{web_base}/c/{proj_enc}/+/{num}"


def _fetch_check_failures(client: GerritClient, change_id: str) -> list[str]:
    """Attempt to retrieve failed CI check names via the Gerrit Checks API."""
    enc = quote(change_id, safe="")
    try:
        data = client._request_json(f"changes/{enc}/revisions/current/checks")
    except GerritApiError:
        return []
    if not isinstance(data, list):
        return []
    failed: list[str] = []
    for check in data:
        if not isinstance(check, dict):
            continue
        if check.get("state") == "FAILED":
            name = check.get("checker_name") or check.get("name") or ""
            if name:
                failed.append(str(name))
    return failed


def _fetch_gerrit_data(
    client: GerritClient,
    web_base: str,
    commits: list[tuple[str, str, str, str | None]],
) -> list[GlogCommit]:
    """Query Gerrit for each commit and return populated GlogCommit objects."""
    result: list[GlogCommit] = []

    for sha, short, summary, change_id in commits:
        if not change_id:
            result.append(
                GlogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=None,
                    pushed=False,
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                )
            )
            continue

        try:
            rows = client.query_changes(f"change:{change_id}", n=5)
        except GerritApiError as e:
            logger.warning("Gerrit query failed for %s: %s", change_id, e)
            result.append(
                GlogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=change_id,
                    pushed=False,
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                )
            )
            continue

        if not rows:
            result.append(
                GlogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=change_id,
                    pushed=False,
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                )
            )
            continue

        change = rows[0]

        # Fetch full detail (includes labels, submittable)
        try:
            detail = client.get_change(change.get("id") or change_id)
        except GerritApiError as e:
            logger.warning("Gerrit detail failed for %s: %s", change_id, e)
            detail = change

        labels = detail.get("labels") or {}
        verified = _extract_label_value(labels, "Verified")
        code_review = _extract_label_value(labels, "Code-Review")
        submittable = bool(detail.get("submittable"))
        url = _gerrit_change_url(web_base, detail)

        # Unresolved comments
        unresolved = 0
        try:
            file_map = client.get_comments(detail.get("id") or change_id)
            unresolved = _count_unresolved(file_map)
        except GerritApiError as e:
            logger.warning("Gerrit comments failed for %s: %s", change_id, e)

        # CI failure job names (best-effort via Checks API)
        ci_failures: list[str] = []
        if verified is not None and verified < 0:
            try:
                ci_failures = _fetch_check_failures(client, detail.get("id") or change_id)
            except Exception as e:
                logger.debug("checks API failed for %s: %s", change_id, e)

        result.append(
            GlogCommit(
                sha=sha,
                short_sha=short,
                summary=summary,
                change_id=change_id,
                pushed=True,
                verified=verified,
                code_review=code_review,
                comments_unresolved=unresolved,
                ci_failures=ci_failures,
                gerrit_url=url,
                submittable=submittable,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Attention detection
# ---------------------------------------------------------------------------

def _determine_attention(commit: GlogCommit, *, chain_blocked: bool) -> list[str]:
    """Return list of reasons why this commit needs attention (empty = stable)."""
    reasons: list[str] = []
    if not commit.pushed:
        reasons.append("not-pushed")
        return reasons
    if commit.verified == -1:
        reasons.append("ci-failed")
    if commit.code_review is not None and commit.code_review < 0:
        reasons.append("review-issues")
    if commit.code_review != 2:
        # lacks full approval
        reasons.append("awaiting-review")
    if commit.comments_unresolved > 0:
        reasons.append("unresolved-comments")
    if chain_blocked:
        reasons.append("chain-blocked")
    return reasons


def _annotate_attention(commits: list[GlogCommit]) -> None:
    """Populate attention_reasons on each commit, including chain-blocking."""
    for i, commit in enumerate(commits):
        chain_blocked = False
        if commit.pushed:
            for earlier in commits[:i]:
                if earlier.pushed and not earlier.submittable:
                    chain_blocked = True
                    break
        commit.attention_reasons = _determine_attention(commit, chain_blocked=chain_blocked)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _fmt_push(pushed: bool, *, use_color: bool) -> str:
    if pushed:
        return _color("p", _DIM, use_color=use_color)
    return _color("n", _CYAN, use_color=use_color)


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


# Primary line format: {sha:7} {push:1} {verified:3} {code_review:4} {comments:3}  # {summary}
# Total fixed-width prefix = 7+1+1+1+3+1+4+1+3+2 = 24 chars before "# "

def _primary_line(commit: GlogCommit, *, use_color: bool) -> str:
    sha = commit.short_sha
    push = _fmt_push(commit.pushed, use_color=use_color)
    verified = _fmt_verified(commit.verified, use_color=use_color)
    cr = _fmt_code_review(commit.code_review, use_color=use_color)
    comments = _fmt_comments(commit.comments_unresolved, use_color=use_color)
    return f"{sha} {push} {verified} {cr} {comments}  # {commit.summary}"


def _oneline_line(commit: GlogCommit, *, use_color: bool) -> str:
    base = _primary_line(commit, use_color=use_color)
    extras = _detail_lines(commit, use_color=False)  # strip color for inline
    if extras:
        suffix = "  " + "  ".join(extras)
        return base + suffix
    return base


# Compact format: {sha:7} {push:1} {v} {cr} {com}
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


def _compact_line(commit: GlogCommit) -> str:
    push = "p" if commit.pushed else "n"
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
        if not c.pushed:
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
    p.add_argument("--full", action="store_true", help="show all commits, not just attention-required")
    p.add_argument("--oneline", action="store_true", help="one line per commit (suppress detail lines)")
    p.add_argument("--json", action="store_true", dest="json_", help="machine-readable JSON output")
    p.add_argument("--range", dest="range_", metavar="REVSET", help="override commit range (e.g. origin/main..HEAD)")
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    p.add_argument("--compact", action="store_true", help="compact single-character status representation")
    p.add_argument("-v", "--verbose", action="store_true", help="log git commands to stderr")
    args = p.parse_args(argv)
    configure_logging(args.verbose)

    cwd = cwd_from_env()
    use_color = not args.no_color and sys.stdout.isatty()

    # Determine commit range
    if args.range_:
        rev_range = args.range_
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
        commits = _fetch_gerrit_data(client, web_base, commit_data)
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
            print(_compact_line(commit))
            prev_had_details = False
        elif args.oneline:
            print(_oneline_line(commit, use_color=use_color))
            prev_had_details = False
        else:
            print(_primary_line(commit, use_color=use_color))
            details = _detail_lines(commit, use_color=use_color)
            for d in details:
                print(d)
            prev_had_details = bool(details)

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
