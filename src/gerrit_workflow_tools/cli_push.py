"""CLI for pushing one commit or a stack to Gerrit."""

from __future__ import annotations

import argparse
import contextlib
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_follow_merges_args,
    add_stop_pattern_args,
    add_verbose_and_debug_log_args,
    handle_git_error,
    init_cli_runtime,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    ANSI_YELLOW,
    color_short_sha,
    color_text,
)
from gerrit_workflow_tools.core.change_id import classify_issues
from gerrit_workflow_tools.core.config import (
    branch_gerrit_reviewers,
    checked_out_branch_name,
    effective_gerrit_destination_branch,
    ger_push_defaults,
    ger_push_mode,
    gerrit_push_remote_policy,
    gerrit_remote,
    head_is_linear_on_remote_gerrit_target,
    is_detached_head,
    refs_for_push_branch_name,
    resolve_push_context_branch,
    resolve_upstream_parsed,
    set_branch_config,
    stop_patterns,
)
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritClient, norm_change_id, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.push_reviewers import (
    ReviewerApplyChangeOutcome,
    apply_reviewer_strategy_after_push_service,
    stack_change_ids_ordered,
)
from gerrit_workflow_tools.core.ready_calc import ReadyResult, change_id_rows_for_range, compute_ready
from gerrit_workflow_tools.core.reviewer import (
    ReviewerStrategy,
    gerrit_credentials_configured,
    reviewer_accounts_from_change_info,
)
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target, parse_change_id
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream
from gerrit_workflow_tools.push_input_line import (
    ParseResult,
    PushLineState,
    refspec_options,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

logger = logging.getLogger(__name__)


def _service_from_cwd(cwd: Path) -> GerritService:
    web_base = resolve_gerrit_web_base(cwd)
    client = GerritClient(web_base, cwd=str(cwd))
    client.web_base = web_base
    return GerritService(client, GerritCache.for_web_base(web_base))


_REBASE_ONTO_REMOTE_HINT = (
    "Hint: run `ger rebase --onto-remote` to replay your commits on top of the latest target branch."
)


def _git_push_output_text(proc: subprocess.CompletedProcess[bytes]) -> str:
    parts: list[bytes] = []
    for chunk in (proc.stdout, proc.stderr):
        if isinstance(chunk, bytes) and chunk:
            parts.append(chunk)
    if not parts:
        return ""
    return b"".join(parts).decode(errors="replace")


def _git_push_rejected_no_new_changes(proc: subprocess.CompletedProcess[bytes]) -> bool:
    """True when Gerrit rejected the refspec because the commit is already on the server."""

    return "no new changes" in _git_push_output_text(proc).lower()


def _emit_git_push_output(proc: subprocess.CompletedProcess[bytes]) -> None:
    for stream, buf in ((sys.stdout, proc.stdout), (sys.stderr, proc.stderr)):
        if not isinstance(buf, bytes) or not buf:
            continue
        text = buf.decode(errors="replace")
        stream.write(text)
        if not text.endswith("\n"):
            stream.write("\n")
        stream.flush()


def _run_git_push(cmd: list[str], cwd: Path | str | None) -> subprocess.CompletedProcess[bytes]:
    """Run ``git push`` (separate hook so tests can monkeypatch without affecting other subprocess use)."""
    proc = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True)
    _emit_git_push_output(proc)
    return proc


def _push_succeeded_for_reviewer_rest(
    proc: subprocess.CompletedProcess[bytes],
    *,
    strategy: ReviewerStrategy,
    reviewers: list[str],
) -> bool:
    if proc.returncode == 0:
        return True
    return _needs_rest_assignment(strategy, reviewers) and _git_push_rejected_no_new_changes(proc)


def _resolve_push_reviewers(
    cwd: Path,
    branch: str,
    reviewer_flag_segments: list[str],
    *,
    interactive: str | None = None,
) -> list[str]:
    """Resolve reviewers without merging unrelated sources.

    A non-empty ``-i`` line replaces branch config and ``--reviewers``.
    ``--reviewers`` alone replaces branch config. Otherwise branch ``gerritReviewers`` applies.
    Multiple ``--reviewers`` flags are concatenated and deduped in order.
    """
    if interactive:
        return _parse_reviewers_list(interactive)
    if reviewer_flag_segments:
        return _parse_reviewers_list(",".join(reviewer_flag_segments))
    cfg = branch_gerrit_reviewers(cwd, branch)
    return _parse_reviewers_list(cfg) if cfg else []


def _gerrit_credentials_configured(cwd: Path) -> bool:
    return gerrit_credentials_configured(cwd)


def _reviewer_accounts_from_change_info(detail: dict[str, object]) -> list[str]:
    """Return reviewer account slugs in Gerrit API order (REVIEWER and CC entries)."""
    return [acc.slug for acc in reviewer_accounts_from_change_info(detail)]


def _format_gpush_attribute_string(reviewers: list[str], wip: bool, private: bool) -> str:
    parts: list[str] = [f"r={name}" for name in reviewers]
    if wip:
        parts.append("wip")
    if private:
        parts.append("private")
    if not parts:
        return "(none)"
    return ",".join(parts)


def _gpush_attribute_suffix(
    detail: dict[str, object] | None,
    merged_reviewers: list[str],
) -> str:
    """Append `` - `…` `` or `` - `…` -> `…` `` for attribute preview lines."""
    if detail is None:
        cur = "(none)"
        new = _format_gpush_attribute_string(merged_reviewers, wip=False, private=False)
        if cur == new:
            return f" - `{cur}`"
        return f" - `{cur}` -> `{new}`"
    wip = bool(detail.get("work_in_progress"))
    priv = bool(detail.get("private"))
    cur_revs = _reviewer_accounts_from_change_info(detail)
    cur_s = _format_gpush_attribute_string(cur_revs, wip, priv)
    new_s = _format_gpush_attribute_string(merged_reviewers, wip, priv)
    if cur_s == new_s:
        return f" - `{cur_s}`"
    return f" - `{cur_s}` -> `{new_s}`"


def _reviewer_seeds_for_prompt(cwd: Path, branch: str) -> list[str]:
    seeds: list[str] = []
    cfg = branch_gerrit_reviewers(cwd, branch)
    if cfg:
        seeds.extend(_parse_reviewers_list(cfg))
    return seeds


def _change_id_for_rev(cwd: Path, rev: str) -> str | None:
    try:
        msg = git_out("show", "-s", "--format=%B", rev, cwd=cwd)
    except GitError:
        return None
    return parse_change_id(msg)


def _prompt_interactive_reviewers(
    cwd: Path | None = None,
    branch: str | None = None,
    *,
    change_id_hint: str | None = None,
) -> ParseResult:
    """Pre-push interactive prompt (``-i``); reuses the new push-options input line."""
    from gerrit_workflow_tools.push_input_prompt import prompt_push_options_line

    seeds = _reviewer_seeds_for_prompt(cwd, branch) if (cwd is not None and branch is not None) else []
    return prompt_push_options_line(
        reviewer_seeds=seeds,
        message="Push options: ",
        cwd=cwd,
        change_id_hint=change_id_hint,
    )


def _prompt_save_reviewers() -> bool:
    ans = input("Save reviewers to branch config? [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def _refs_for_spec(
    tip: str,
    push_branch: str,
    state: PushLineState,
    strategy: ReviewerStrategy,
) -> str:
    ref = f"{tip}:refs/for/{push_branch}"
    opts = refspec_options(state, strategy.value)
    if opts:
        ref += f"%{','.join(opts)}"
    return ref


@dataclass
class GerritPushReviewers:
    """Effective push-options state and how to apply reviewers for one ``ger push`` run."""

    reviewers: list[str]
    strategy: ReviewerStrategy
    topic: str | None = None
    wip: bool = False
    private: bool = False

    def to_state(self) -> PushLineState:
        """Return a :class:`PushLineState` snapshot for refspec/canonical formatting."""
        return PushLineState(
            reviewers=list(self.reviewers),
            topic=self.topic,
            wip=self.wip,
            private=self.private,
            strategy=self.strategy.value,
        )

    def replace_from_state(self, state: PushLineState) -> None:
        """Overwrite reviewers/topic/wip/private/strategy with values from ``state``."""
        self.reviewers = list(state.reviewers)
        self.topic = state.topic
        self.wip = state.wip
        self.private = state.private
        self.strategy = ReviewerStrategy(state.strategy)


@dataclass
class GerritPushContext:
    """Resolved state for a Gerrit push operation.

    All fields except ``plan`` are set once during construction and treated as
    immutable. ``plan`` is mutated in-place by the interactive reviewer loop.
    """

    branch: str
    push_branch: str
    target: str
    remote: str
    ready: ReadyResult
    plan: GerritPushReviewers
    first_parent: bool
    show_attributes: bool
    update_last_pushed: bool


def _parse_reviewers_list(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    # Split by comma or space
    for part in raw.replace(",", " ").split():
        s = part.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _prompt_reviewers_line_ptk(
    cwd: Path | None = None,
    branch: str | None = None,
    *,
    change_id_hint: str | None = None,
) -> ParseResult:
    """Confirm-loop ``r`` action: open the highlighted push-options input line."""
    from gerrit_workflow_tools.push_input_prompt import prompt_push_options_line

    seeds = _reviewer_seeds_for_prompt(cwd, branch) if (cwd is not None and branch is not None) else []
    return prompt_push_options_line(
        reviewer_seeds=seeds,
        message="Push options: ",
        cwd=cwd,
        change_id_hint=change_id_hint,
    )


def _strategy_status_label(strategy: ReviewerStrategy) -> str:
    return {
        ReviewerStrategy.PUSH: "push (new changes are modified)",
        ReviewerStrategy.LAZY: "lazy (non-assigned are modified)",
        ReviewerStrategy.OVERWRITE: "overwrite (all changes are modified)",
    }[strategy]


def _needs_rest_assignment(strategy: ReviewerStrategy, reviewers: list[str]) -> bool:
    return strategy in (ReviewerStrategy.LAZY, ReviewerStrategy.OVERWRITE) and bool(reviewers)


def _validate_rest_plan(cwd: Path, plan: GerritPushReviewers) -> str | None:
    if not _needs_rest_assignment(plan.strategy, plan.reviewers):
        return None
    if not _gerrit_credentials_configured(cwd):
        return (
            "error: lazy/overwrite reviewer strategies need Gerrit REST; set gerrit.user and "
            "gerrit.token (or gerrit.password)"
        )
    try:
        resolve_gerrit_web_base(cwd)
    except ValueError as e:
        return f"error: {e}"
    return None


def _parse_gerrit_push_confirm(
    raw: str,
) -> Literal["push", "cancel", "reviewers", "invalid"]:
    s = raw.strip().lower()
    if s in ("n", "no"):
        return "cancel"
    if s in ("", "y", "yes"):
        return "push"
    if s in ("r", "reviewer", "reviewers"):
        return "reviewers"
    return "invalid"


def _prompt_gerrit_push_confirm_action() -> Literal["push", "cancel", "reviewers"]:
    while True:
        raw = input("Do you want to push these commits? [Y/n/r]: ")
        act = _parse_gerrit_push_confirm(raw)
        if act == "invalid":
            print(
                "  (Enter or y: push, n: cancel, r: set reviewers and strategy)",
                file=sys.stderr,
            )
            continue
        return act


def _escape_topic_for_assignment_report(topic: str) -> str:
    return topic.replace("\\", "\\\\").replace('"', '\\"')


def _assignment_attributes_fragment(plan: GerritPushReviewers, reviewers_assigned: tuple[str, ...]) -> str:
    parts: list[str] = []
    if plan.topic:
        parts.append(f'topic="{_escape_topic_for_assignment_report(plan.topic)}"')
    parts.extend(f"r={name}" for name in reviewers_assigned)
    if plan.wip:
        parts.append("wip")
    if plan.private:
        parts.append("private")
    return ",".join(parts)


def _print_post_push_assignment_report(
    cwd: Path,
    r: ReadyResult,
    first_parent: bool,
    plan: GerritPushReviewers,
    outcomes: list[ReviewerApplyChangeOutcome],
) -> None:
    if not r.push_range:
        return
    rows = commits_in_range(cwd, r.push_range, first_parent=first_parent)
    by_cid: dict[str, tuple[str, str]] = {}
    for c in rows:
        if c.change_id:
            by_cid[norm_change_id(c.change_id)] = (c.sha, c.subject)
    for outcome in outcomes:
        row = by_cid.get(outcome.change_id)
        if row is None:
            continue
        sha, subject = row
        frag = _assignment_attributes_fragment(plan, outcome.reviewers_assigned)
        if not frag:
            continue
        print(f"{sha} {subject} assigned {frag}")


def _apply_reviewer_strategy_after_push(  # pragma: no cover - thin CLI adapter
    cwd: Path,
    service: GerritService,
    strategy: ReviewerStrategy,
    reviewers: list[str],
    r: ReadyResult,
    first_parent: bool,
    plan: GerritPushReviewers,
) -> int:
    """Return 0 on success, non-zero if a required REST step failed."""
    change_ids = stack_change_ids_ordered(cwd, r, first_parent)
    result = apply_reviewer_strategy_after_push_service(service, strategy, reviewers, change_ids)
    for issue in result.issues:
        print(f"{issue.level}: {issue.message}", file=sys.stderr)
    if result.ok and result.outcomes:
        _print_post_push_assignment_report(cwd, r, first_parent, plan, result.outcomes)
    return 0 if result.ok else 1


# pylint: disable=too-many-locals
def _commit_lines_for_preview(
    cwd: Path,
    r: ReadyResult,
    *,
    summary_highlighter: SummaryHighlighter,
    show_attributes: bool,
    merged_reviewers: list[str],
    first_parent: bool = True,
) -> list[str]:
    """
    Generate a list of formatted commit lines for stack preview.

    Args:
        cwd: Working directory as a Path.
        r: ReadyResult containing stack and push range information.
        summary_highlighter: SummaryHighlighter for commit subject highlighting.
        show_attributes: Whether to append reviewer/WIP/private attribute previews.
        merged_reviewers: Reviewers list to show for preview, overriding branch/CLI defaults.
        first_parent: Restrict traversal to first-parent edges only.

    Returns:
        List of strings, each corresponding to a stack commit with optional attribute preview.
    """
    if not r.push_range:
        return []
    rows = commits_in_range(cwd, r.push_range, first_parent=first_parent)
    details_by_cid: dict[str, dict[str, object]] | None = None
    if show_attributes:
        ids: list[str] = []
        for c in rows:
            if c.change_id:
                ids.append(c.change_id)
        if ids:
            try:
                resolve_gerrit_web_base(cwd)
            except ValueError as e:
                raise ValueError(str(e)) from e
            if not _gerrit_credentials_configured(cwd):
                raise ValueError(
                    "Gerrit credentials are not configured; set gerrit.user and "
                    "gerrit.token (or gerrit.password) for REST access."
                )
            service = _service_from_cwd(cwd)
            raw_details = service.changes.get_payloads(ids)
            details_by_cid = {norm_change_id(k): v for k, v in raw_details.items()}
        else:
            details_by_cid = {}

    lines: list[str] = []
    for c in rows:
        short_sha, subj = c.short_sha, c.subject
        disp = summary_highlighter.highlight(subj)
        sha_p = short_sha.ljust(8)
        line = f"    {color_short_sha(sha_p)}{color_text(' # ', ANSI_DIM)}{disp}"
        if show_attributes and details_by_cid is not None:
            cid = c.change_id
            if cid:
                detail = details_by_cid.get(norm_change_id(cid))
                line += _gpush_attribute_suffix(detail if isinstance(detail, dict) else None, merged_reviewers)
        lines.append(line)
    return lines


def _stop_pattern_from_reason(boundary_reason: str) -> str | None:
    m = re.search(r"stop pattern (.+)$", boundary_reason)
    if not m:
        return None
    return m.group(1).strip()


def _remaining_not_ready_count(cwd: Path, boundary_sha: str | None, *, head: str = "HEAD") -> int:
    if not boundary_sha:
        return 0
    try:
        return int(git_out("rev-list", "--count", f"{boundary_sha}..{head}", cwd=cwd))
    except (GitError, ValueError):
        return 0


def _format_stop_pattern_notice(boundary_line: str, pat: str) -> str:
    """Explain the ready boundary without wrapping highlighted commit text in warning color."""
    return (
        "Stopped at commit "
        + color_text('"', ANSI_YELLOW)
        + boundary_line
        + color_text('"', ANSI_YELLOW)
        + ", because it matches the stop pattern "
        + color_text(pat, ANSI_DIM_GRAY)
        + color_text(".", ANSI_YELLOW)
    )


def _format_boundary_commit_line(
    cwd: Path,
    boundary_sha: str | None,
    *,
    summary_highlighter: SummaryHighlighter,
) -> str | None:
    if not boundary_sha:
        return None
    try:
        short_sha = git_out("rev-parse", "--short", boundary_sha, cwd=cwd)
        subject = git_out("show", "-s", "--format=%s", boundary_sha, cwd=cwd)
    except GitError:
        return None
    sha_p = short_sha.ljust(8)
    return f"{color_short_sha(sha_p)}{color_text(' # ', ANSI_DIM)}{summary_highlighter.highlight(subject)}"


def _print_gpush_preview(  # pylint: disable=too-many-arguments
    cwd: Path,
    r: ReadyResult,
    commit_lines: list[str],
    *,
    head: str = "HEAD",
    summary_highlighter: SummaryHighlighter,
) -> None:
    print(color_text("About to push commits:", f"{ANSI_BOLD}{ANSI_CYAN}"))
    for ln in commit_lines:
        print(ln)
    if r.boundary_sha:
        boundary_line = _format_boundary_commit_line(
            cwd,
            r.boundary_sha,
            summary_highlighter=summary_highlighter,
        )
        pat = _stop_pattern_from_reason(r.boundary_reason)
        if boundary_line and pat:
            print()
            print(_format_stop_pattern_notice(boundary_line, pat))
            remain = _remaining_not_ready_count(cwd, r.boundary_sha, head=head)
            if remain > 0:
                print(color_text(f"... {remain} not-ready commit(s) remain unpushed", ANSI_YELLOW))


def _parse_confirm_answer(raw: str) -> bool | None:
    """Return True to push, False to cancel, None if user should be asked again."""
    s = raw.strip().lower()
    if s in ("n", "no"):
        return False
    if s in ("", "y", "yes"):
        return True
    return None


def _print_gpush_confirm_status_line(
    local_branch: str,
    gerrit_target: str,
    reviewers: list[str],
    *,
    topic: str | None = None,
    wip: bool = False,
    private: bool = False,
    strategy: ReviewerStrategy | None = None,
) -> None:
    """One-line summary before the push confirmation prompt.

    *gerrit_target* is the branch segment inside ``refs/for/<branch>`` (from
    :func:`~gerrit_workflow_tools.core.config.refs_for_push_branch_name`).
    """
    branch_v = color_text(local_branch, f"{ANSI_BOLD}{ANSI_CYAN}")
    target_v = color_text(f"refs/for/{gerrit_target}", ANSI_GREEN)
    rev_v = color_text(", ".join(reviewers), ANSI_LIGHT_GREEN) if reviewers else color_text("(none)", ANSI_DIM)
    topic_v = color_text(topic, ANSI_CYAN) if topic else color_text("(none)", ANSI_DIM)
    wip_v = color_text("yes", ANSI_GREEN) if wip else color_text("no", ANSI_DIM)
    private_v = color_text("yes", ANSI_GREEN) if private else color_text("no", ANSI_DIM)
    sep = color_text("  ·  ", ANSI_DIM)
    line = (
        f"{color_text('Branch', ANSI_DIM)} {branch_v}"
        f"{sep}"
        f"{color_text('Target', ANSI_DIM)} {target_v}"
        f"{sep}"
        f"{color_text('Reviewers', ANSI_DIM)} {rev_v}"
        f"{sep}"
        f"{color_text('Topic', ANSI_DIM)} {topic_v}"
        f"{sep}"
        f"{color_text('WIP', ANSI_DIM)} {wip_v}"
        f"{sep}"
        f"{color_text('Private', ANSI_DIM)} {private_v}"
    )
    if strategy is not None and strategy != ReviewerStrategy.PUSH:
        strat_v = color_text(_strategy_status_label(strategy), ANSI_DIM)
        line += f"{sep}{color_text('Strategy', ANSI_DIM)} {strat_v}"
    print(line)


def _confirm_push_interactive(*, vanilla: bool = False) -> bool:
    prompt = "Do you want to run `git push`? [Y/n]: " if vanilla else "Do you want to push these commits? [Y/n]: "
    while True:
        ans = input(prompt)
        parsed = _parse_confirm_answer(ans)
        if parsed is not None:
            return parsed


def _maybe_check_rebased_onto_remote(
    cwd: Path,
    branch: str,
    *,
    head: str,
    policy: str,
    no_rebase_check: bool,
) -> int | None:
    """Check branch linearity on the fetched remote target.

    Warns or errors based on *policy*. Returns an exit code or ``None``.
    """
    if no_rebase_check or policy == "ignore-not-rebased":
        logger.debug(
            "gpush rebase check skipped: no_rebase_check=%s policy=%r",
            no_rebase_check,
            policy,
        )
        return None
    remote_name = gerrit_remote(cwd)
    logger.debug("gpush rebase check: fetching remote %r", remote_name)
    try:
        git("fetch", remote_name, cwd=cwd)
    except GitError as e:
        logger.debug("gpush rebase check: fetch failed: %s", e)
        print(
            f"warning: could not fetch `{remote_name}`; skipping remote rebase check: {e}",
            file=sys.stderr,
        )
        return None
    try:
        ok, onto = head_is_linear_on_remote_gerrit_target(cwd, branch, head=head)
    except GitError as e:
        print(
            f"warning: could not compare HEAD to remote target; skipping remote rebase check: {e}",
            file=sys.stderr,
        )
        return None
    if ok:
        logger.debug("gpush rebase check: HEAD is linear on remote target (ok)")
        return None
    logger.debug("gpush rebase check: HEAD not linear on remote target; policy=%r", policy)
    short_onto = onto
    try:
        short_onto = git_out("rev-parse", "--abbrev-ref", onto, cwd=cwd)
    except GitError:
        with contextlib.suppress(GitError):
            short_onto = git_out("rev-parse", "--short", onto, cwd=cwd)
    try:
        tip = git_out("rev-parse", "--short", onto, cwd=cwd)
        fork = git_out("merge-base", head, onto, cwd=cwd)
        fork_s = git_out("rev-parse", "--short", fork, cwd=cwd)
        detail = (
            f"tip of `{short_onto}` ({tip}) is not an ancestor of HEAD "
            f"(fork at {fork_s}; rebase onto the fetched target to linearize)"
        )
    except GitError:
        detail = f"tip of `{short_onto}` is not an ancestor of HEAD"
    msg = (
        f"Local HEAD is not based directly on the current Gerrit target branch after fetch ({detail}). "
        f"{_REBASE_ONTO_REMOTE_HINT}"
    )
    if policy == "error-not-rebased":
        print(f"error: {msg}", file=sys.stderr)
        return 1
    print(f"warning: {msg}", file=sys.stderr)
    return None


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``ger push``."""
    p = argparse.ArgumentParser(prog="ger push")
    p.add_argument(
        "-i",
        action="store_true",
        help=(
            "Prompt for reviewers (TTY only; non-empty line overwrites branch and --reviewers; "
            "cannot be used with --yes)."
        ),
    )
    p.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="Push the specified local branch instead of the current branch.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions only; do not push.")
    p.add_argument(
        "--no-rebase-check",
        action="store_true",
        help="Do not fetch or verify that HEAD is linear on the remote Gerrit target (see gerrit.push.remotePolicy).",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Push without confirmation (required when stdin is not a terminal).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_",
        help="Push the full stack (ignore stop patterns).",
    )
    add_color_args(p)
    add_stop_pattern_args(p)
    add_follow_merges_args(p)
    p.add_argument(
        "--reviewers",
        action="append",
        default=[],
        metavar="ACCOUNTS",
        help=(
            "Comma-separated Gerrit reviewer accounts (repeat for more; overwrites branch gerritReviewers). "
            "Appended as ref options %%r=…"
        ),
    )
    p.add_argument(
        "--reviewer-strategy",
        choices=[ReviewerStrategy.PUSH.value, ReviewerStrategy.LAZY.value, ReviewerStrategy.OVERWRITE.value],
        default=None,
        help=(
            "How to apply reviewers when pushing: push (%%r= on ref), lazy (REST: add only where none), "
            "overwrite (REST: replace on each change). Requires credentials for lazy/overwrite. "
            "Topic/WIP/private always use magic ref options (see --topic / --wip / --private)."
        ),
    )
    p.add_argument(
        "--topic",
        metavar="NAME",
        default=None,
        help="Gerrit change topic (magic ref %%topic=…; sent on push for every reviewer strategy).",
    )
    p.add_argument(
        "--wip",
        action="store_true",
        help="Mark change(s) work-in-progress (magic ref %%wip; sent on push for every reviewer strategy).",
    )
    p.add_argument(
        "--private",
        action="store_true",
        help="Mark change(s) private (magic ref %%private; sent on push for every reviewer strategy).",
    )
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and push steps to stderr.",
    )
    p.add_argument(
        "until",
        nargs="?",
        default=None,
        metavar="REV",
        help="Push only through this commit.",
    )
    return p


def _handle_vanilla_push(cwd: Path, args: argparse.Namespace, *, branch: str) -> int:
    """Handle the vanilla (non-Gerrit) ``git push`` flow and return an exit code."""
    if (
        args.until
        or args.all_
        or args.reviewers
        or args.ignore_pattern
        or args.topic is not None
        or args.wip
        or args.private
    ):
        print(
            "warning: --until, --all, --reviewers, --ignore-pattern, --topic, --wip, and --private "
            "apply only to Gerrit push; ignoring.",
            file=sys.stderr,
        )
    parsed = resolve_upstream_parsed(cwd, branch)
    checked_out = checked_out_branch_name(cwd)
    if args.branch is not None and checked_out != branch:
        if parsed:
            remote_name, _rest = parsed
            cmd_vanilla = ["git", "push", remote_name, branch]
        else:
            cmd_vanilla = ["git", "push", branch]
    else:
        cmd_vanilla = ["git", "push"]
    logger.debug("gpush vanilla: %s", cmd_vanilla)
    if args.dry_run:
        print(color_text(" ".join(cmd_vanilla), ANSI_DIM_GRAY))
        print("[dry-run] not executing push", file=sys.stderr)
        return 0
    if not sys.stdin.isatty() and not args.yes:
        print(
            "error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt",
            file=sys.stderr,
        )
        return 1
    if not args.yes:
        print(color_text(" ".join(cmd_vanilla), ANSI_DIM_GRAY))
        print()
        if not _confirm_push_interactive(vanilla=True):
            print("Push cancelled.", file=sys.stderr)
            return 0
    logger.debug("gpush vanilla executing: %s (cwd=%s)", " ".join(cmd_vanilla), cwd)
    proc = _run_git_push(cmd_vanilla, cwd)
    return proc.returncode


def _build_gerrit_context(  # pylint: disable=too-many-arguments
    cwd: Path,
    branch: str,
    args: argparse.Namespace,
    gdef: dict[str, bool],
    remote_policy: str,
    fp: bool,
) -> GerritPushContext | int:
    """Resolve all Gerrit push parameters and return a context, or an exit code on early failure."""
    eff = effective_gerrit_destination_branch(cwd, branch)
    if not eff:
        raise GitError("Internal error: Gerrit push mode without effective destination.")
    push_branch = refs_for_push_branch_name(cwd, eff)
    target = eff

    rc_early = _maybe_check_rebased_onto_remote(
        cwd,
        branch,
        head=branch,
        policy=remote_policy,
        no_rebase_check=bool(args.no_rebase_check),
    )
    if rc_early is not None:
        return rc_early

    interactive_state: PushLineState | None = None
    if args.i:
        res = _prompt_interactive_reviewers(cwd, branch, change_id_hint=_change_id_for_rev(cwd, branch))
        if (
            res.state.reviewers
            or res.state.topic
            or res.state.wip
            or res.state.private
            or res.state.strategy != ReviewerStrategy.PUSH.value
        ):
            interactive_state = res.state
            reviewers = list(res.state.reviewers)
        else:
            reviewers = _resolve_push_reviewers(cwd, branch, list(args.reviewers))
        if _prompt_save_reviewers():
            set_branch_config(cwd, branch, gerrit_reviewers=",".join(reviewers))
    else:
        reviewers = _resolve_push_reviewers(cwd, branch, list(args.reviewers))

    selected_strategy = (
        interactive_state.strategy
        if interactive_state is not None
        else (args.reviewer_strategy or ReviewerStrategy.PUSH.value)
    )
    if interactive_state is not None:
        eff_topic = interactive_state.topic
        eff_wip = interactive_state.wip
        eff_private = interactive_state.private
    else:
        eff_topic = args.topic
        eff_wip = bool(args.wip)
        eff_private = bool(args.private)
    plan = GerritPushReviewers(
        reviewers=list(reviewers),
        strategy=ReviewerStrategy(selected_strategy),
        topic=eff_topic,
        wip=eff_wip,
        private=eff_private,
    )

    r = compute_ready(
        cwd,
        branch=branch,
        head=branch,
        all_commits=args.all_,
        ignore_patterns=args.ignore_pattern or None,
        until=args.until,
        first_parent=fp,
        stop_patterns=stop_patterns(cwd),
    )
    logger.debug(
        "gpush ready tip=%s range=%s boundary=%s",
        r.push_tip_sha,
        r.push_range,
        r.boundary_reason,
    )

    _fork, _, target_tip = merge_base_with_target(cwd, branch, head=branch)
    rows = change_id_rows_for_range(cwd, target_tip, head=branch, first_parent=fp)
    items = list(rows)
    _, cid_exit = classify_issues(items, strict=True)
    logger.debug("gpush change_id check exit=%d commits=%d", cid_exit, len(items))
    if cid_exit >= 2:
        print(
            "error: Change-Id check failed; inspect with `ger change-id --check-duplicates` "
            "or auto-fix with `ger change-id --fix`",
            file=sys.stderr,
        )
        return 2

    remote = gerrit_remote(cwd)
    if not r.push_tip_sha:
        print("error: nothing to push (empty ready prefix)", file=sys.stderr)
        return 1

    update_last_pushed = gdef["last_pushed_branch"]
    logger.debug(
        "gpush show_attributes=%s update_last_pushed=%s",
        gdef["show_attributes"],
        update_last_pushed,
    )

    return GerritPushContext(
        branch=branch,
        push_branch=push_branch,
        target=target,
        remote=remote,
        ready=r,
        plan=plan,
        first_parent=fp,
        show_attributes=gdef["show_attributes"],
        update_last_pushed=update_last_pushed,
    )


def _execute_gerrit_push(  # pylint: disable=too-many-branches,too-many-statements
    cwd: Path,
    ctx: GerritPushContext,
    args: argparse.Namespace,
    summary_highlighter: SummaryHighlighter,
) -> int:
    """Run the interactive approval loop, execute the push, and handle post-push steps."""
    tip = ctx.ready.push_tip_sha
    assert tip is not None  # guaranteed by _build_gerrit_context
    cmd: list[str] = []
    stack_printed = False
    while True:
        refspec = _refs_for_spec(tip, ctx.push_branch, ctx.plan.to_state(), ctx.plan.strategy)
        cmd = ["git", "push", ctx.remote, refspec]
        logger.debug(
            "gpush resolved: remote=%r gerrit_target=%r push_branch=%r reviewers=%s strategy=%s refspec=%r",
            ctx.remote,
            ctx.target,
            ctx.push_branch,
            ctx.plan.reviewers,
            ctx.plan.strategy,
            refspec,
        )

        try:
            commit_lines = _commit_lines_for_preview(
                cwd,
                ctx.ready,
                summary_highlighter=summary_highlighter,
                show_attributes=ctx.show_attributes,
                merged_reviewers=ctx.plan.reviewers,
                first_parent=ctx.first_parent,
            )
        except (ValueError, GerritApiError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        if not stack_printed:
            _print_gpush_preview(
                cwd,
                ctx.ready,
                commit_lines,
                head=ctx.branch,
                summary_highlighter=summary_highlighter,
            )
            stack_printed = True

        if args.dry_run:
            print()
            _print_gpush_confirm_status_line(
                ctx.branch,
                ctx.push_branch,
                ctx.plan.reviewers,
                topic=ctx.plan.topic,
                wip=ctx.plan.wip,
                private=ctx.plan.private,
                strategy=ctx.plan.strategy,
            )
            print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
            if _needs_rest_assignment(ctx.plan.strategy, ctx.plan.reviewers):
                print(
                    "[dry-run] after a successful push would apply reviewers via "
                    f"{ctx.plan.strategy.value} ({_strategy_status_label(ctx.plan.strategy)})",
                    file=sys.stderr,
                )
            print("[dry-run] not executing push", file=sys.stderr)
            return 0

        if not sys.stdin.isatty() and not args.yes:
            print(
                "error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt",
                file=sys.stderr,
            )
            return 1

        if args.yes:
            err = _validate_rest_plan(cwd, ctx.plan)
            if err:
                print(err, file=sys.stderr)
                return 1
            break

        print()
        _print_gpush_confirm_status_line(
            ctx.branch,
            ctx.push_branch,
            ctx.plan.reviewers,
            topic=ctx.plan.topic,
            wip=ctx.plan.wip,
            private=ctx.plan.private,
            strategy=ctx.plan.strategy,
        )
        print()
        act = _prompt_gerrit_push_confirm_action()
        if act == "cancel":
            print("Push cancelled.", file=sys.stderr)
            return 0
        if act == "reviewers":
            res = _prompt_reviewers_line_ptk(cwd, ctx.branch, change_id_hint=_change_id_for_rev(cwd, tip))
            if not res.valid_for_apply:
                print("Invalid push options; nothing changed.", file=sys.stderr)
                continue
            new_state = res.state
            if not (
                new_state.reviewers
                or new_state.topic
                or new_state.wip
                or new_state.private
                or new_state.strategy != ReviewerStrategy.PUSH.value
            ):
                print("No push options entered; nothing changed.")
                continue
            ctx.plan.replace_from_state(new_state)
            continue

        err = _validate_rest_plan(cwd, ctx.plan)
        if err:
            print(err, file=sys.stderr)
            continue
        break

    logger.debug("gpush executing: %s (cwd=%s)", " ".join(cmd), cwd)
    proc = _run_git_push(cmd, cwd)
    push_ok = _push_succeeded_for_reviewer_rest(
        proc,
        strategy=ctx.plan.strategy,
        reviewers=ctx.plan.reviewers,
    )
    logger.debug(
        "gpush push finished: rc=%s push_ok=%s no_new_changes=%s",
        proc.returncode,
        push_ok,
        _git_push_rejected_no_new_changes(proc),
    )
    if not push_ok:
        return proc.returncode
    if _needs_rest_assignment(ctx.plan.strategy, ctx.plan.reviewers):
        try:
            resolve_gerrit_web_base(cwd)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        service = _service_from_cwd(cwd)
        rc_rest = _apply_reviewer_strategy_after_push(
            cwd,
            service,
            ctx.plan.strategy,
            ctx.plan.reviewers,
            ctx.ready,
            ctx.first_parent,
            ctx.plan,
        )
        if rc_rest != 0:
            return rc_rest
    if ctx.update_last_pushed:
        marker = f"lastPush/{ctx.branch}"
        try:
            git("branch", "-f", marker, tip, cwd=cwd)
        except GitError as e:
            print(f"warning: could not update {marker}: {e}", file=sys.stderr)
    return 0


def _resolve_push_branch(cwd: Path, branch_arg: str | None) -> str:
    """Return the local branch ``ger push`` operates on."""
    if branch_arg:
        try:
            git_out("rev-parse", "--verify", branch_arg, cwd=cwd)
        except GitError as e:
            raise GitError(f"branch {branch_arg!r} not found") from e
        return branch_arg
    b = resolve_push_context_branch(cwd)
    if b is None:
        raise GitError(
            "ger push requires a branch (detached HEAD with no local branch at this commit). "
            "Check out a branch first, or pass --branch."
        )
    return b


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger push``: compute ready range, validate Change-Ids, and push to Gerrit."""
    args = _build_arg_parser().parse_args(argv)
    cwd, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)
    gdef = ger_push_defaults(cwd)
    remote_policy = gerrit_push_remote_policy(cwd)
    fp = not args.follow_merges

    logger.debug(
        "gpush cwd=%s branch=%s dry_run=%s yes=%s all=%s until=%s i=%s remote_policy=%s "
        "no_rebase_check=%s follow_merges=%s reviewer_strategy=%s",
        cwd,
        args.branch,
        args.dry_run,
        args.yes,
        args.all_,
        args.until,
        args.i,
        remote_policy,
        args.no_rebase_check,
        args.follow_merges,
        args.reviewer_strategy,
    )

    if args.i and args.yes:
        print("error: -i cannot be used with --yes (-y)", file=sys.stderr)
        return 1
    if args.i and not sys.stdin.isatty():
        print("error: -i requires an interactive terminal (stdin is not a TTY)", file=sys.stderr)
        return 1

    try:
        detached = is_detached_head(cwd)
        b = _resolve_push_branch(cwd, args.branch)
        mode = ger_push_mode(cwd, b)
        if not args.yes and mode in ("gerrit", None) and (args.branch or not detached):
            if not require_branch_upstream(cwd, b):
                return 1
            mode = ger_push_mode(cwd, b)
        if mode is None:
            raise GitError(
                "No push destination: set upstream to your Gerrit remote (`gerrit.remote`, often `origin`; "
                "run `git fetch` first if the tracking branch is missing)."
            )
        if args.i and mode == "vanilla":
            print("error: -i applies only to Gerrit push (upstream on gerrit.remote)", file=sys.stderr)
            return 1

        if mode == "vanilla":
            return _handle_vanilla_push(cwd, args, branch=b)

        ctx_or_rc = _build_gerrit_context(cwd, b, args, gdef, remote_policy, fp)
        if isinstance(ctx_or_rc, int):
            return ctx_or_rc
        return _execute_gerrit_push(cwd, ctx_or_rc, args, summary_highlighter)
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
