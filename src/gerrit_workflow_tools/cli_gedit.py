from __future__ import annotations

import argparse
import os
import subprocess
import sys

from gerrit_workflow_tools.cli_common import cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import commit_in_stack, merge_base_with_target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gedit")
    p.add_argument("commit", help="commit to edit/reword/drop (in current stack)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--reword", action="store_true", help="reword commit message")
    g.add_argument("--drop", action="store_true", help="drop commit")
    args = p.parse_args(argv)
    cwd = cwd_from_env()

    action = "reword" if args.reword else "drop" if args.drop else "edit"

    try:
        full = git_out("rev-parse", args.commit.strip(), cwd=cwd)
        if not commit_in_stack(cwd, full):
            raise GitError(f"commit {args.commit} is not in the current local stack")
        mb, _, _ = merge_base_with_target(cwd)
        short = git_out("rev-parse", "--short", full, cwd=cwd)
    except GitError as e:
        return handle_git_error(e)

    env = os.environ.copy()
    env["GEDIT_FULL_SHA"] = full
    env["GEDIT_SHORT_SHA"] = short
    env["GEDIT_ACTION"] = action
    env["GIT_SEQUENCE_EDITOR"] = (
        f"{sys.executable} -m gerrit_workflow_tools.rebase_sequence_editor"
    )

    r = subprocess.run(
        ["git", "rebase", "-i", mb],
        cwd=cwd,
        env=env,
    )
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
