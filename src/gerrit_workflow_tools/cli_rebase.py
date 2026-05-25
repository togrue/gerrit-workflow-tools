"""CLI entry: ``ger rebase`` — interactive rebase with Gerrit status annotations."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys

from gerrit_workflow_tools.cli_common import (
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.core.config import rebase_defaults, resolve_rebase_onto_remote_ref, resolve_working_branch
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.stack import merge_base_with_target, resolve_stack_commit
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, ensure_branch_upstream_interactive

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger rebase``."""
    p = argparse.ArgumentParser(
        prog="ger rebase",
        description=(
            "Start an interactive rebase with Gerrit status annotations in the sequence editor.\n\n"
            "Each pick line is enriched with the commit's patchset status, Verified and Code-Review\n"
            "labels, unresolved comment count, and an actionable attention note."
        ),
    )
    p.add_argument(
        "rev",
        metavar="REV",
        nargs="?",
        default=None,
        help=(
            "Base commit, Change-Id (I…), or git ref to rebase from "
            "(default: merge base with the target branch). Not with --onto-remote."
        ),
    )
    p.add_argument(
        "--onto-remote",
        action="store_true",
        help=(
            "Rebase the current branch onto the fetched remote-tracking tip of the upstream "
            "target on gerrit.remote. Default: gerrit.rebaseOntoRemote."
        ),
    )
    p.add_argument(
        "--no-onto-remote",
        action="store_true",
        help="Override gerrit.rebaseOntoRemote: use merge-base with the target (default behavior).",
    )
    p.add_argument(
        "--drop-merged-equivalent",
        action="store_true",
        help=(
            "Turn merged commits that are provably equivalent to the merged Gerrit revision into "
            "drop lines in the todo. Default: gerrit.rebaseDropMergedEquivalent."
        ),
    )
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and enricher steps to stderr.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger rebase``: interactive rebase with Gerrit status annotations.

    Sets ``GIT_SEQUENCE_EDITOR`` to the enricher wrapper
    (:mod:`gerrit_workflow_tools.rebase_enricher`) which annotates each pick line with
    the commit's Gerrit verified/CR/comments status before opening the real editor.

    The real editor is resolved by the enricher from ``GIT_EDITOR``, ``core.editor``,
    ``VISUAL``, or ``EDITOR`` — no extra configuration needed.
    """
    p = _build_parser()
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    if args.onto_remote and args.rev:
        p.error("cannot combine --onto-remote with a REV argument")

    rdef = rebase_defaults(cwd)
    if args.no_onto_remote:
        use_onto_remote = False
    elif args.onto_remote:
        use_onto_remote = True
    elif args.rev:
        use_onto_remote = False
    else:
        use_onto_remote = rdef["onto_remote"]

    drop_merged = bool(args.drop_merged_equivalent or rdef["drop_merged_equivalent"])

    try:
        branch = resolve_working_branch(cwd)
        if use_onto_remote:
            base = resolve_rebase_onto_remote_ref(cwd, branch)
        elif args.rev:
            # resolve_stack_commit handles both Change-Id (I…) and plain git refs.
            base = resolve_stack_commit(cwd, args.rev.strip(), branch=branch)
        else:
            if (
                branch is not None
                and not branch_has_upstream(cwd, branch)
                and not ensure_branch_upstream_interactive(cwd, branch)
                and sys.stdin.isatty()
            ):
                return 1
            base, _, _ = merge_base_with_target(cwd, branch)
    except GitError as e:
        return handle_git_error(e)

    env = os.environ.copy()
    # Point GIT_SEQUENCE_EDITOR at the enricher.  The enricher reads GIT_EDITOR /
    # core.editor / EDITOR from the environment to find the actual editor to open.
    env["GIT_SEQUENCE_EDITOR"] = f"{shlex.quote(sys.executable)} -m gerrit_workflow_tools.rebase_enricher"
    if args.debug_log:
        env["GREBASE_DEBUG_LOG"] = "1"
    if drop_merged:
        env["GREBASE_DROP_MERGED_EQUIVALENT"] = "1"

    logger.debug("ger rebase: base=%s onto_remote=%s", base[:8], use_onto_remote)
    cmd = ["git", "rebase", "-i", base]
    logger.debug("run: %s (cwd=%s)", " ".join(cmd), cwd)
    r = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    logger.debug("ger rebase: git rebase finished rc=%s", r.returncode)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
