from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

from gerrit_workflow_tools.change_id import classify_issues
from gerrit_workflow_tools.cli_common import (
    add_stop_pattern_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.config import (
    branch_gerrit_reviewers,
    branch_gerrit_target,
    gerrit_password,
    gerrit_remote,
    gerrit_token,
    gerrit_user,
    gpush_defaults,
    refs_for_push_branch_name,
    set_branch_config,
)
from gerrit_workflow_tools.gerrit_change_status import batch_load_change_details, norm_change_id
from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.ready_calc import ReadyResult, change_id_rows_for_range, compute_ready
from gerrit_workflow_tools.stack import merge_base_with_target, parse_change_id, stack_commits_metadata_one_log

logger = logging.getLogger(__name__)


def _run_git_push(cmd: list[str], cwd: Path | str | None) -> subprocess.CompletedProcess[bytes]:
    """Run ``git push`` (separate hook so tests can monkeypatch without affecting other subprocess use)."""
    return subprocess.run(cmd, cwd=cwd)


# Dim yellow foreground, reset (used when stdout is a TTY).
_ANSI_DIM_YELLOW = "\033[2;33m"
_ANSI_RESET = "\033[0m"

_SUBJECT_MARKER_RE = re.compile(r"\b(todo|dropme)\b", re.IGNORECASE)


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
    """Append `` - `…` `` or `` - `…` -> `…` `` for ``--show-attributes`` lines."""
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


def _format_subject_line(subject: str, *, tty: bool) -> str:
    if not tty:
        return subject

    def _sub(m: re.Match[str]) -> str:
        return f"{_ANSI_DIM_YELLOW}{m.group(0)}{_ANSI_RESET}"

    return _SUBJECT_MARKER_RE.sub(_sub, subject)


def _commit_lines_for_preview(
    cwd: Path,
    r: ReadyResult,
    *,
    tty_out: bool,
    show_attributes: bool,
    merged_reviewers: list[str],
) -> list[str]:
    if not r.push_range:
        return []
    rows = stack_commits_metadata_one_log(cwd, r.push_range)
    details_by_cid: dict[str, dict[str, object]] | None = None
    if show_attributes:
        ids: list[str] = []
        for _f, _s, _sub, raw in rows:
            cid = parse_change_id(raw)
            if cid:
                ids.append(cid)
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
    for _full, short_sha, subj, raw in rows:
        disp = _format_subject_line(subj, tty=tty_out)
        line = f"    {short_sha} # {disp}"
        if show_attributes and details_by_cid is not None:
            cid = parse_change_id(raw)
            if cid:
                detail = details_by_cid.get(norm_change_id(cid))
                line += _gpush_attribute_suffix(detail if isinstance(detail, dict) else None, merged_reviewers)
        lines.append(line)
    return lines


def _print_gpush_preview(cmd: list[str], r: ReadyResult, commit_lines: list[str]) -> None:
    print(" ".join(cmd))
    print()
    print(f"ready reason: {r.boundary_reason}")
    print("Updated commits:")
    for ln in commit_lines:
        print(ln)


def _parse_confirm_answer(raw: str) -> bool | None:
    """Return True to push, False to cancel, None if user should be asked again."""
    s = raw.strip().lower()
    if s in ("n", "no"):
        return False
    if s in ("", "y", "yes"):
        return True
    return None


def _confirm_push_interactive() -> bool:
    while True:
        ans = input("Do you want to push these commits? [Y/n]: ")
        parsed = _parse_confirm_answer(ans)
        if parsed is not None:
            return parsed


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gpush``: compute ready range, validate Change-Ids, and push to Gerrit."""
    p = argparse.ArgumentParser(prog="git gpush")
    p.add_argument(
        "-i",
        action="store_true",
        help="Prompt for reviewers (TTY only; merged after branch config and --reviewers; cannot be used with --yes).",
    )
    p.add_argument(
        "--show-attributes",
        action="store_true",
        help=(
            "Show per-commit Gerrit attributes vs this push (reviewers, wip, private); needs gerrit.webUrl "
            "and credentials. Default: gerrit.gpushShowAttributes."
        ),
    )
    p.add_argument(
        "--no-show-attributes",
        action="store_true",
        help="Override gerrit.gpushShowAttributes when set.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions only; do not push.")
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
    p.add_argument("--target", metavar="BRANCH", help="Gerrit target branch for this push.")
    p.add_argument("--save-target", action="store_true", help="Store --target for this branch.")
    p.add_argument(
        "--force-boundary",
        action="store_true",
        help="Deprecated: same as --all (prefer --all).",
    )
    add_stop_pattern_args(p)
    p.add_argument(
        "--reviewers",
        action="append",
        default=[],
        metavar="ACCOUNTS",
        help="Comma-separated Gerrit reviewer accounts (repeat to merge). Appended as ref options %%r=…",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log git commands and push steps to stderr.",
    )
    p.add_argument(
        "until",
        nargs="?",
        default=None,
        metavar="REV",
        help="Push only through this commit.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()
    gdef = gpush_defaults(cwd)
    show_attributes = (bool(args.show_attributes) or gdef["show_attributes"]) and not args.no_show_attributes

    logger.debug(
        "gpush cwd=%s dry_run=%s yes=%s all=%s until=%s target=%s save_target=%s show_attributes=%s i=%s",
        cwd,
        args.dry_run,
        args.yes,
        args.all_,
        args.until,
        args.target,
        args.save_target,
        show_attributes,
        args.i,
    )

    if args.i and args.yes:
        print("error: -i cannot be used with --yes (-y)", file=sys.stderr)
        return 1
    if args.i and not sys.stdin.isatty():
        print("error: -i requires an interactive terminal (stdin is not a TTY)", file=sys.stderr)
        return 1

    try:
        b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if args.target and args.save_target:
            set_branch_config(cwd, b, gerrit_target=args.target)

        target = args.target or branch_gerrit_target(cwd, b)
        if not target:
            raise GitError(
                "No Gerrit target: run `git gbranch init --target <branch>` or `git gpush --target <branch>`."
            )
        push_branch = refs_for_push_branch_name(cwd, target)

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
            all_commits=args.all_ or args.force_boundary,
            no_config_patterns=args.no_config_patterns,
            ignore_patterns=args.ignore_pattern or None,
            until=args.until,
        )
        logger.debug(
            "gpush ready tip=%s range=%s boundary=%s",
            r.push_tip_sha,
            r.push_range,
            r.boundary_reason,
        )

        mb, _, _ = merge_base_with_target(cwd)
        rows = change_id_rows_for_range(cwd, mb)
        items = [(a, b, c) for a, b, c in rows]
        _, cid_exit = classify_issues(items, strict=True)
        logger.debug("gpush change_id check exit=%d commits=%d", cid_exit, len(items))
        if cid_exit >= 2:
            print(
                "error: Change-Id check failed; fix with git gcid --check-duplicates",
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

        tty_out = sys.stdout.isatty()
        try:
            commit_lines = _commit_lines_for_preview(
                cwd,
                r,
                tty_out=tty_out,
                show_attributes=show_attributes,
                merged_reviewers=reviewers,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except GerritApiError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        _print_gpush_preview(cmd, r, commit_lines)

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
            if not _confirm_push_interactive():
                print("Push cancelled.", file=sys.stderr)
                return 0

        logger.debug("gpush executing: %s", " ".join(cmd))
        proc = _run_git_push(cmd, cwd)
        logger.debug("gpush push finished with return code %s", proc.returncode)
        return proc.returncode
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
