"""CLI entry: ``ger rebase`` — interactive rebase with Gerrit status annotations."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys

from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.stack import merge_base_with_target, resolve_stack_commit

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger rebase``: interactive rebase with Gerrit status annotations.

    Sets ``GIT_SEQUENCE_EDITOR`` to the enricher wrapper
    (:mod:`gerrit_workflow_tools.rebase_enricher`) which annotates each pick line with
    the commit's Gerrit verified/CR/comments status before opening the real editor.

    The real editor is resolved by the enricher from ``GIT_EDITOR``, ``core.editor``,
    ``VISUAL``, or ``EDITOR`` — no extra configuration needed.
    """
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
            "(default: merge base with the target branch)."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log git commands and enricher steps to stderr.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()

    try:
        if args.rev:
            # resolve_stack_commit handles both Change-Id (I…) and plain git refs.
            base = resolve_stack_commit(cwd, args.rev.strip())
        else:
            base, _, _ = merge_base_with_target(cwd)
    except GitError as e:
        return handle_git_error(e)

    env = os.environ.copy()
    # Point GIT_SEQUENCE_EDITOR at the enricher.  The enricher reads GIT_EDITOR /
    # core.editor / EDITOR from the environment to find the actual editor to open.
    env["GIT_SEQUENCE_EDITOR"] = (
        f"{shlex.quote(sys.executable)} -m gerrit_workflow_tools.rebase_enricher"
    )
    if args.verbose:
        env["GREBASE_VERBOSE"] = "1"

    logger.debug("ger rebase: base=%s", base[:8])
    r = subprocess.run(["git", "rebase", "-i", base], cwd=cwd, env=env)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
