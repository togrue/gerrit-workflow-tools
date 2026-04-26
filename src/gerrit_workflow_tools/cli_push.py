"""CLI for pushing one commit or a stack to Gerrit."""

from __future__ import annotations

import argparse
import contextlib
import logging
import re
import subprocess
import sys
from pathlib import Path

from gerrit_workflow_tools.change_id import classify_issues
from gerrit_workflow_tools.cli_common import (
    add_color_args,
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
from gerrit_workflow_tools.config import (
    branch_gerrit_reviewers,
    effective_gerrit_destination_branch,
    ger_push_mode,
    gerrit_password,
    gerrit_push_remote_policy,
    gerrit_remote,
    gerrit_token,
    gerrit_user,
    gpush_defaults,
    head_is_linear_on_remote_gerrit_target,
    refs_for_push_branch_name,
    set_branch_config,
)
from gerrit_workflow_tools.gerrit_change_status import batch_load_change_details, norm_change_id
from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError, git, git_out
from gerrit_workflow_tools.ready_calc import ReadyResult, change_id_rows_for_range, compute_ready
from gerrit_workflow_tools.stack import commits_in_range, merge_base_with_target
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

logger = logging.getLogger(__name__)

_REBASE_ONTO_REMOTE_HINT = (
    "Hint: run `ger restack --onto-remote` to replay your commits on top of the latest target branch."
)


def _run_git_push(cmd: list[str], cwd: Path | str | None) -> subprocess.CompletedProcess[bytes]:
    """Run ``git push`` (separate hook so tests can monkeypatch without affecting other subprocess use)."""
    return subprocess.run(cmd, cwd=cwd, check=False)


def _merge_reviewers(
    cwd: Path,
    branch: str,
    reviewer_flag_segments: list[str],
    *,
    interactive: str | None = None,
) -> list[str]:
    """Merge branch config, ``--reviewers``, then optional ``-i`` input; dedupe preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    cfg = branch_gerrit_reviewers(cwd, branch)
    if cfg:
        for part in cfg.split(","):
            s = part.strip()
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
    for seg in reviewer_flag_segments:
        for part in seg.split(","):
            s = part.strip()
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
    if interactive:
        for part in interactive.split(","):
            s = part.strip()
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
    return ordered


def _gerrit_credentials_configured(cwd: Path) -> bool:
    u = gerrit_user(cwd)
    secret = gerrit_token(cwd) or gerrit_password(cwd)
    return bool(u and secret is not None)


def _account_slug_from_gerrit(account: dict[str, object]) -> str | None:
    u = account.get("username")
    if isinstance(u, str) and u.strip():
        return u.strip()
    email = account.get("email")
    if isinstance(email, str) and "@" in email:
        return email.split("@", 1)[0].strip()
    name = account.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _reviewer_accounts_from_change_info(detail: dict[str, object]) -> list[str]:
    """Return reviewer account slugs in Gerrit API order (REVIEWER and CC entries)."""
    out: list[str] = []
    revs = detail.get("reviewers")
    if not isinstance(revs, list):
        return out
    for entry in revs:
        if not isinstance(entry, dict):
            continue
        st = entry.get("state")
        if st not in ("REVIEWER", "CC"):
            continue
        acc = entry.get("account")
        if isinstance(acc, dict):
            slug = _account_slug_from_gerrit(acc)
            if slug:
                out.append(slug)
    return out


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


def _prompt_interactive_reviewers() -> str:
    return input("Reviewers (comma-separated; empty keeps branch/CLI defaults): ")


def _prompt_save_reviewers() -> bool:
    ans = input("Save reviewers to branch config? [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def _refs_for_spec(tip: str, push_branch: str, reviewers: list[str]) -> str:
    ref = f"{tip}:refs/for/{push_branch}"
    for r in reviewers:
        ref += f"%r={r}"
    return ref


def _commit_lines_for_preview(
    cwd: Path,
    r: ReadyResult,
    *,
    summary_highlighter: SummaryHighlighter,
    show_attributes: bool,
    merged_reviewers: list[str],
) -> list[str]:
    if not r.push_range:
        return []
    rows = commits_in_range(cwd, r.push_range)
    details_by_cid: dict[str, dict[str, object]] | None = None
    if show_attributes:
        ids: list[str] = []
        for c in rows:
            if c.change_id:
                ids.append(c.change_id)
        if ids:
            try:
                web_base = resolve_gerrit_web_base(cwd)
            except ValueError as e:
                raise ValueError(str(e)) from e
            if not _gerrit_credentials_configured(cwd):
                raise ValueError(
                    "Gerrit credentials are not configured; set gerrit.user and "
                    "gerrit.token (or gerrit.password) for REST access."
                )
            client = GerritClient(web_base, cwd=str(cwd))
            details_by_cid = batch_load_change_details(client, ids)
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


def _remaining_not_ready_count(cwd: Path, boundary_sha: str | None) -> int:
    if not boundary_sha:
        return 0
    try:
        return int(git_out("rev-list", "--count", f"{boundary_sha}..HEAD", cwd=cwd))
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


def _print_gpush_preview(
    cwd: Path,
    cmd: list[str],
    r: ReadyResult,
    commit_lines: list[str],
    *,
    summary_highlighter: SummaryHighlighter,
    show_push_command: bool,
) -> None:
    if show_push_command:
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print()
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
            remain = _remaining_not_ready_count(cwd, r.boundary_sha)
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
) -> None:
    """One-line summary in ``ger branch show`` colors before the push confirmation prompt."""
    branch_v = color_text(local_branch, f"{ANSI_BOLD}{ANSI_CYAN}")
    target_v = color_text(gerrit_target, ANSI_GREEN)
    rev_v = color_text(", ".join(reviewers), ANSI_LIGHT_GREEN) if reviewers else color_text("(none)", ANSI_DIM)
    sep = color_text("  ·  ", ANSI_DIM)
    line = (
        f"{color_text('Branch', ANSI_DIM)} {branch_v}"
        f"{sep}"
        f"{color_text('Target', ANSI_DIM)} {target_v}"
        f"{sep}"
        f"{color_text('Reviewers', ANSI_DIM)} {rev_v}"
    )
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
        ok, onto = head_is_linear_on_remote_gerrit_target(cwd, branch)
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
        fork = git_out("merge-base", "HEAD", onto, cwd=cwd)
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


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-return-statements
    """CLI entry for ``ger push``: compute ready range, validate Change-Ids, and push to Gerrit."""
    p = argparse.ArgumentParser(prog="ger push")
    p.add_argument(
        "-i",
        action="store_true",
        help="Prompt for reviewers (TTY only; merged after branch config and --reviewers; cannot be used with --yes).",
    )
    p.add_argument(
        "--update-last-pushed",
        action="store_true",
        help=(
            "After a successful push, move local branch lastPush/<current-branch> "
            "to the pushed tip. Default: gerrit.lastPushedBranch."
        ),
    )
    p.add_argument(
        "--no-update-last-pushed",
        action="store_true",
        help="Do not update lastPush/<current-branch> after push (overrides gerrit.lastPushedBranch).",
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
    p.add_argument(
        "--reviewers",
        action="append",
        default=[],
        metavar="ACCOUNTS",
        help="Comma-separated Gerrit reviewer accounts (repeat to merge). Appended as ref options %%r=…",
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
    args = p.parse_args(argv)
    cwd, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)
    gdef = gpush_defaults(cwd)
    remote_policy = gerrit_push_remote_policy(cwd)
    show_attributes = gdef["show_attributes"]
    update_last_pushed = (
        bool(args.update_last_pushed) or gdef["last_pushed_branch"]
    ) and not args.no_update_last_pushed

    logger.debug(
        "gpush cwd=%s dry_run=%s yes=%s all=%s until=%s show_attributes=%s "
        "update_last_pushed=%s i=%s remote_policy=%s no_rebase_check=%s",
        cwd,
        args.dry_run,
        args.yes,
        args.all_,
        args.until,
        show_attributes,
        update_last_pushed,
        args.i,
        remote_policy,
        args.no_rebase_check,
    )

    if args.i and args.yes:
        print("error: -i cannot be used with --yes (-y)", file=sys.stderr)
        return 1
    if args.i and not sys.stdin.isatty():
        print("error: -i requires an interactive terminal (stdin is not a TTY)", file=sys.stderr)
        return 1

    try:
        b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if b == "HEAD":
            raise GitError("ger push requires a branch (detached HEAD). Check out a branch first.")
        mode = ger_push_mode(cwd, b)
        if mode is None:
            raise GitError(
                "No push destination: set upstream to your Gerrit remote (`gerrit.remote`, often `origin`; "
                "try `ger branch infer-upstream` after `git fetch`). "
                "Optional Gerrit destination overrides: see `ger branch --help`."
            )
        if args.i and mode == "vanilla":
            print("error: -i applies only to Gerrit push (upstream on gerrit.remote)", file=sys.stderr)
            return 1

        if mode == "vanilla":
            if args.until or args.all_ or args.reviewers or args.ignore_pattern:
                print(
                    "warning: --until, --all, --reviewers, and --ignore-pattern apply only to Gerrit push; ignoring.",
                    file=sys.stderr,
                )
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

        eff = effective_gerrit_destination_branch(cwd, b)
        if not eff:
            raise GitError("Internal error: Gerrit push mode without effective destination.")
        push_branch = refs_for_push_branch_name(cwd, eff)
        target = eff

        rc_early = _maybe_check_rebased_onto_remote(
            cwd,
            b,
            policy=remote_policy,
            no_rebase_check=bool(args.no_rebase_check),
        )
        if rc_early is not None:
            return rc_early

        interactive: str | None = None
        if args.i:
            interactive = _prompt_interactive_reviewers().strip()
            reviewers = _merge_reviewers(cwd, b, list(args.reviewers), interactive=interactive or None)
            if _prompt_save_reviewers():
                set_branch_config(cwd, b, gerrit_reviewers=",".join(reviewers))
        else:
            reviewers = _merge_reviewers(cwd, b, list(args.reviewers))

        r = compute_ready(
            cwd,
            branch=None,
            all_commits=args.all_,
            ignore_patterns=args.ignore_pattern or None,
            until=args.until,
        )
        logger.debug(
            "gpush ready tip=%s range=%s boundary=%s",
            r.push_tip_sha,
            r.push_range,
            r.boundary_reason,
        )

        _fork, _, target_tip = merge_base_with_target(cwd)
        rows = change_id_rows_for_range(cwd, target_tip)
        items = list(rows)
        _, cid_exit = classify_issues(items, strict=True)
        logger.debug("gpush change_id check exit=%d commits=%d", cid_exit, len(items))
        if cid_exit >= 2:
            print(
                "error: Change-Id check failed; fix with ger change-id --check-duplicates",
                file=sys.stderr,
            )
            return 2

        remote = gerrit_remote(cwd)
        tip = r.push_tip_sha
        if not tip:
            print("error: nothing to push (empty ready prefix)", file=sys.stderr)
            return 1

        refspec = _refs_for_spec(tip, push_branch, reviewers)
        cmd = ["git", "push", remote, refspec]
        logger.debug(
            "gpush resolved: remote=%r gerrit_target=%r push_branch=%r reviewers=%s refspec=%r",
            remote,
            target,
            push_branch,
            reviewers,
            refspec,
        )

        try:
            commit_lines = _commit_lines_for_preview(
                cwd,
                r,
                summary_highlighter=summary_highlighter,
                show_attributes=show_attributes,
                merged_reviewers=reviewers,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except GerritApiError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        _print_gpush_preview(
            cwd,
            cmd,
            r,
            commit_lines,
            summary_highlighter=summary_highlighter,
            show_push_command=True,
        )

        if args.dry_run:
            print("[dry-run] not executing push", file=sys.stderr)
            return 0

        if not sys.stdin.isatty() and not args.yes:
            print(
                "error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt",
                file=sys.stderr,
            )
            return 1

        if not args.yes:
            print()
            _print_gpush_confirm_status_line(b, push_branch, reviewers)
            print()
            if not _confirm_push_interactive():
                print("Push cancelled.", file=sys.stderr)
                return 0

        logger.debug("gpush executing: %s (cwd=%s)", " ".join(cmd), cwd)
        proc = _run_git_push(cmd, cwd)
        logger.debug(
            "gpush push finished: rc=%s (push output is not captured; see terminal)",
            proc.returncode,
        )
        if proc.returncode == 0 and update_last_pushed:
            marker = f"lastPush/{b}"
            try:
                git("branch", "-f", marker, tip, cwd=cwd)
            except GitError as e:
                print(f"warning: could not update {marker}: {e}", file=sys.stderr)
        return proc.returncode
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
