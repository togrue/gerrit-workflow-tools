"""CLI for branch-level Gerrit configuration management."""

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
    clear_gerrit_git_config_cache,
    current_branch,
    effective_gerrit_destination_branch,
    ger_push_mode,
    infer_nearest_remote_tracking_branch,
    refs_for_push_branch_name,
    set_branch_config,
)
from gerrit_workflow_tools.git_run import GitError, git

logger = logging.getLogger(__name__)

_GER_BRANCH_EPILOG = """\
Optional Gerrit destination (when not inferred from upstream on gerrit.remote):
  ger branch init --target <branch> [--reviewers LIST]   write gerritTarget / gerritReviewers
  ger branch set-target <branch>                        update branch.<name>.gerritTarget
  git config branch.<name>.gerritTarget <branch>         same key as set-target
"""

# ``ger branch show`` row labels (each string literal appears once).
_BRANCH_SHOW_LOCAL = "Local branch"
_BRANCH_SHOW_UPSTREAM = "upstream"
_BRANCH_SHOW_GERRIT_TARGET = "gerritTarget"
_BRANCH_SHOW_REVIEWERS = "reviewers"
_BRANCH_SHOW_PUSH_MODE = "Push mode"
_BRANCH_SHOW_ALL_LABELS = (
    _BRANCH_SHOW_LOCAL,
    _BRANCH_SHOW_UPSTREAM,
    _BRANCH_SHOW_GERRIT_TARGET,
    _BRANCH_SHOW_REVIEWERS,
    _BRANCH_SHOW_PUSH_MODE,
)


def _branch_show_row(label: str, value_styled: str, *, label_width: int) -> None:
    lab = label.ljust(label_width)
    print(f"  {color_text(lab, ANSI_DIM)}{value_styled}")


def _cmd_show(cwd: Path) -> int:
    b = current_branch(cwd)
    if b == "HEAD":
        print("error: ger branch show requires a branch (detached HEAD).", file=sys.stderr)
        return 1
    label_w = max(len(s) for s in _BRANCH_SHOW_ALL_LABELS) + 1
    r = branch_gerrit_reviewers(cwd, b)
    mode = ger_push_mode(cwd, b)
    eff = effective_gerrit_destination_branch(cwd, b)
    push_branch = refs_for_push_branch_name(cwd, eff) if eff else None

    up_p = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    upstream_ref = up_p.stdout.strip() if up_p.returncode == 0 else None

    print(color_text("Branch configuration", f"{ANSI_BOLD}{ANSI_CYAN}"))
    print()
    _branch_show_row(
        _BRANCH_SHOW_LOCAL,
        color_text(b, f"{ANSI_BOLD}{ANSI_CYAN}"),
        label_width=label_w,
    )
    _branch_show_row(
        _BRANCH_SHOW_UPSTREAM,
        color_text(upstream_ref, ANSI_GREEN) if upstream_ref else color_text("(none)", ANSI_DIM),
        label_width=label_w,
    )
    # Same branch name ``ger push`` uses for ``refs/for/<branch>`` (not the raw upstream ref).
    if push_branch:
        _branch_show_row(
            _BRANCH_SHOW_GERRIT_TARGET,
            color_text(push_branch, ANSI_GREEN),
            label_width=label_w,
        )
    else:
        _branch_show_row(
            _BRANCH_SHOW_GERRIT_TARGET,
            color_text("(not set)", ANSI_DIM),
            label_width=label_w,
        )
    _branch_show_row(
        _BRANCH_SHOW_REVIEWERS,
        color_text(r, ANSI_LIGHT_GREEN) if r else color_text("(none)", ANSI_DIM),
        label_width=label_w,
    )
    mode_s = (
        "Gerrit (refs/for/…)"
        if mode == "gerrit"
        else ("plain git push" if mode == "vanilla" else "(need upstream or override; try `ger branch infer-upstream`)")
    )
    _branch_show_row(_BRANCH_SHOW_PUSH_MODE, color_text(mode_s, ANSI_DIM), label_width=label_w)
    return 0


def _cmd_init(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    if b == "HEAD":
        print("error: ger branch init requires a branch (detached HEAD).", file=sys.stderr)
        return 1
    if not ns.target and not ns.reviewers:
        print(
            "Nothing to set: use --target and/or --reviewers, run `ger branch infer-upstream` to set upstream, "
            "or rely on an existing upstream for Gerrit push.",
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


def _cmd_infer_upstream(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    if b == "HEAD":
        print(
            "error: ger branch infer-upstream requires a named branch (not detached HEAD).",
            file=sys.stderr,
        )
        return 1
    inferred = infer_nearest_remote_tracking_branch(cwd, "HEAD")
    if not inferred:
        print(
            "error: no remote-tracking branches under refs/remotes/ (fetch remotes first).",
            file=sys.stderr,
        )
        return 1
    abbrev, sym, ahead, behind = inferred
    summary = (
        f"Inferred remote-tracking branch {abbrev!r} "
        f"(symmetric divergence {sym}: {ahead} commit(s) on HEAD, {behind} on remote tip since merge-base)."
    )
    if not ns.yes:
        if not sys.stdin.isatty():
            print(
                "error: not a terminal; use --yes to set upstream without a prompt.",
                file=sys.stderr,
            )
            return 1
        print(summary, file=sys.stderr)
        ans = input(f"Set upstream of {b!r} to {abbrev!r}? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 1
    else:
        print(f"{summary} Applying (--yes).", file=sys.stderr)

    git("branch", "--set-upstream-to", abbrev, b, cwd=cwd)
    clear_gerrit_git_config_cache()
    print(f"Upstream for {b!r} set to {abbrev}.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger branch``: show or set branch-local Gerrit target and reviewers."""
    p = argparse.ArgumentParser(
        prog="ger branch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_GER_BRANCH_EPILOG,
    )
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

    iu = sub.add_parser(
        "infer-upstream",
        help="Set upstream to the remote-tracking branch closest to HEAD (minimum symmetric divergence).",
    )
    iu.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Set upstream without confirmation (required when stdin is not a terminal).",
    )

    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    init_color_mode(color=args.color)
    cwd = cwd_from_env()
    logger.debug("gbranch cmd=%s cwd=%s", args.cmd, cwd)

    retcode = 1
    try:
        if args.cmd == "show":
            retcode = _cmd_show(cwd)
        if args.cmd == "init":
            retcode = _cmd_init(args, cwd)
        if args.cmd == "set-target":
            retcode = _cmd_set_target(args, cwd)
        if args.cmd == "set-reviewers":
            retcode = _cmd_set_reviewers(args, cwd)
        if args.cmd == "infer-upstream":
            retcode = _cmd_infer_upstream(args, cwd)
    except GitError as e:
        retcode = handle_git_error(e)
    return retcode


if __name__ == "__main__":
    raise SystemExit(main())
