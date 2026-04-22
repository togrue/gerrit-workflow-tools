from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    color_text,
    init_color_mode,
)
from gerrit_workflow_tools.config import (
    branch_gerrit_reviewers,
    branch_gerrit_target,
    current_branch,
    effective_gerrit_destination_branch,
    ger_push_mode,
    refs_for_push_branch_name,
    set_branch_config,
)
from gerrit_workflow_tools.git_run import GitError

logger = logging.getLogger(__name__)

# Label column width for ``show`` (longest label: "Reviewers").
_BRANCH_SHOW_LABEL_W = 12


def _branch_show_row(label: str, value_styled: str) -> None:
    lab = label.ljust(_BRANCH_SHOW_LABEL_W)
    print(f"  {color_text(lab, ANSI_DIM)}{value_styled}")


def _cmd_show(cwd: Path) -> int:
    b = current_branch(cwd)
    if b == "HEAD":
        print("error: ger branch show requires a branch (detached HEAD).", file=sys.stderr)
        return 1
    override = branch_gerrit_target(cwd, b)
    r = branch_gerrit_reviewers(cwd, b)
    mode = ger_push_mode(cwd, b)
    inferred = effective_gerrit_destination_branch(cwd, b) if not override else None
    push_segment = refs_for_push_branch_name(cwd, inferred) if inferred else None
    print(color_text("Branch configuration", f"{ANSI_BOLD}{ANSI_CYAN}"))
    print()
    _branch_show_row("Branch", color_text(b, f"{ANSI_BOLD}{ANSI_CYAN}"))
    if override:
        _branch_show_row(
            "Target (override)",
            color_text(override, ANSI_GREEN),
        )
    elif inferred and push_segment:
        _branch_show_row(
            "Inferred target",
            color_text(f"{inferred} → {push_segment}", ANSI_GREEN),
        )
    else:
        _branch_show_row("Target", color_text("(not set)", ANSI_DIM))
    mode_s = (
        "Gerrit (refs/for/…)"
        if mode == "gerrit"
        else ("plain git push" if mode == "vanilla" else "(need upstream or override)")
    )
    _branch_show_row("Push mode", color_text(mode_s, ANSI_DIM))
    _branch_show_row(
        "Reviewers",
        color_text(r, ANSI_LIGHT_GREEN) if r else color_text("(none)", ANSI_DIM),
    )
    return 0


def _cmd_init(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    if b == "HEAD":
        print("error: ger branch init requires a branch (detached HEAD).", file=sys.stderr)
        return 1
    if not ns.target and not ns.reviewers:
        print(
            "Nothing to set: use --target and/or --reviewers, or rely on upstream for Gerrit push.",
            file=sys.stderr,
        )
        return 0
    set_branch_config(
        cwd,
        b,
        gerrit_target=ns.target,
        gerrit_reviewers=ns.reviewers,
    )
    parts: list[str] = []
    if ns.target:
        parts.append(f"target={ns.target!r}")
    if ns.reviewers:
        parts.append(f"reviewers={ns.reviewers!r}")
    print(f"Configured branch {b!r}: {', '.join(parts)}", file=sys.stderr)
    return 0


def _cmd_set_target(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    set_branch_config(cwd, b, gerrit_target=ns.value)
    return 0


def _cmd_set_reviewers(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    set_branch_config(cwd, b, gerrit_reviewers=ns.value)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger branch``: show or set branch-local Gerrit target and reviewers."""
    p = argparse.ArgumentParser(prog="ger branch")
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and config writes to stderr.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="Show Gerrit metadata for the current branch.")

    ip = sub.add_parser("init", help="Set branch-local Gerrit targets (non-interactive).")
    ip.add_argument(
        "--target",
        help=(
            "Optional override for the Gerrit destination branch (e.g. main, dev). "
            "If omitted, `ger push` uses upstream when it tracks `gerrit.remote`."
        ),
    )
    ip.add_argument(
        "--reviewers",
        default=None,
        metavar="REVIEWERS",
        help="Comma-separated Gerrit reviewer accounts.",
    )

    st = sub.add_parser("set-target", help="Set gerritTarget (Gerrit destination branch name) for the current branch.")
    st.add_argument(
        "value",
        metavar="BRANCH",
        help="Destination branch on Gerrit (e.g. main, dev). Run `git fetch` on gerrit.remote if rev-parse fails.",
    )

    sr = sub.add_parser("set-reviewers", help="Set gerritReviewers for the current branch.")
    sr.add_argument("value", metavar="REVIEWERS")

    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    init_color_mode(color=args.color)
    cwd = cwd_from_env()
    logger.debug("gbranch cmd=%s cwd=%s", args.cmd, cwd)

    try:
        if args.cmd == "show":
            return _cmd_show(cwd)
        if args.cmd == "init":
            return _cmd_init(args, cwd)
        if args.cmd == "set-target":
            return _cmd_set_target(args, cwd)
        if args.cmd == "set-reviewers":
            return _cmd_set_reviewers(args, cwd)
    except GitError as e:
        return handle_git_error(e)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
