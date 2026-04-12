# gerrit change-id command (gcid)
# Return a Change-Id for a commit or range of commits.
# The Change-Id is taken from the last non-empty line when it matches "Change-Id: I…".

# Implementation
# Identify if the supplied argument is a valid Change-Id.
# If it is, output the Change-Id.
# Else use one ``git log`` over the revision or range (RS-delimited %H / %B: :func:`stack.git_log_sha_body`).
# Parse each commit message (last non-empty line must be the Change-Id: :func:`change_id.extract_change_id_from_msg`).
# If the Change-Id is not found, output an error message.
# If the Change-Id is found, output the Change-Id.
# With ``--start-at-remote`` or ``--check-duplicates``, log ``merge_base..END``
# (same merge-base resolution as ``stack``, :func:`stack.rev_spec_merge_base_to_end`).
# ``--check-duplicates`` exits 0 if all footers are valid and unique, 1 if a footer is missing, 2 on duplicates.

import argparse
import sys

from gerrit_workflow_tools.change_id import (
    CHANGE_ID_LAST_LINE_FOOTER_RE,
    extract_change_id_from_msg,
    is_change_id_token,
)

from gerrit_workflow_tools.cli_common import (
    configure_logging,
    cwd_from_env,
    handle_git_error,
)

from gerrit_workflow_tools.git_run import GitError

from gerrit_workflow_tools.stack import (
    git_log_sha_body,
    parse_git_log_sha_body_rs,
    rev_spec_merge_base_to_end,
)


# Re-export for tests and backwards compatibility.

CHANGE_ID_RE = CHANGE_ID_LAST_LINE_FOOTER_RE

is_change_id = is_change_id_token
_parse_sha_body_rs = parse_git_log_sha_body_rs


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gcid``: print or validate Change-Ids for commits or ranges (optional duplicate check)."""
    p = argparse.ArgumentParser(prog="git gcid")
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        help="Commit SHA, Change-Id (I…), or range (sha1..sha2). Defaults to HEAD if not given.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase stderr verbosity (-v: INFO; -vv: log each git subprocess).",
    )
    p.add_argument(
        "--start-at-remote",
        action="store_true",
        help="Start at the merged remote branch, where the current commit is based on.",
    )
    p.add_argument(
        "--check-duplicates",
        action="store_true",
        help="Check for duplicate Change-Ids in the current chain. Start at the merged remote branch, where the current commit is based on.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()

    input_arg = args.arg or "HEAD"

    if args.check_duplicates:
        if is_change_id_token(input_arg):
            print(
                "error: --check-duplicates needs a commit or range, not a Change-Id",
                file=sys.stderr,
            )
            return 2
        try:
            rev_spec = rev_spec_merge_base_to_end(cwd, input_arg)
            raw = git_log_sha_body(cwd, rev_spec, single=False)
        except GitError as e:
            return handle_git_error(e)
        pairs = parse_git_log_sha_body_rs(raw)
        seen: dict[str, str] = {}
        for sha, msg in pairs:
            cid = extract_change_id_from_msg(msg)
            if not cid:
                print(
                    f"error: no Change-Id found in commit {sha}",
                    file=sys.stderr,
                )
                return 1
            if cid in seen:
                short = sha[:8]
                first = seen[cid][:8]
                print(
                    f"error: duplicate Change-Id {cid} "
                    f"(commit {short}, also on {first})",
                    file=sys.stderr,
                )
                return 2
            seen[cid] = sha
        return 0

    # If arg looks like a Change-Id, just output it
    if is_change_id_token(input_arg):
        print(input_arg)
        return 0

    try:
        if args.start_at_remote:
            rev_spec = rev_spec_merge_base_to_end(cwd, input_arg)
            raw = git_log_sha_body(cwd, rev_spec, single=False)
        else:
            single = ".." not in input_arg
            raw = git_log_sha_body(cwd, input_arg, single=single)
    except GitError as e:
        return handle_git_error(e)

    pairs = parse_git_log_sha_body_rs(raw)
    for sha, msg in pairs:
        cid = extract_change_id_from_msg(msg)
        if cid:
            print(cid)
        else:
            print(f"error: no Change-Id found in commit {sha}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
