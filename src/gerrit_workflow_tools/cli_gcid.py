# gerrit change-id command (gcid)
# Return a Change-Id for a commit or range of commits.
# The Change-Id is taken from the last non-empty line when it matches "Change-Id: I…".

# Implementation
# Identify if the supplied argument is a valid Change-Id.
# If it is, output the Change-Id.
# Else normalize the revision with ``git rev-parse --verify`` (each side of ``..`` / ``...``), then run one
# ``git log`` over the revision or range (RS-delimited %H / %B: :func:`stack.git_log_sha_body`).
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

from gerrit_workflow_tools.git_run import GitError, git_out

from gerrit_workflow_tools.stack import (
    git_log_sha_body,
    parse_git_log_sha_body_rs,
    rev_spec_merge_base_to_end,
)


# Re-export for tests and backwards compatibility.

CHANGE_ID_RE = CHANGE_ID_LAST_LINE_FOOTER_RE

is_change_id = is_change_id_token
_parse_sha_body_rs = parse_git_log_sha_body_rs


def resolve_gcid_user_arg(cwd: str, arg: str) -> str:
    """Resolve *arg* to a form ``git log`` accepts: each ref via ``git rev-parse --verify``.

    Supports the same revisions as ``git rev-parse --verify`` (branches, tags, SHAs, ``HEAD~n``, etc.),
    plus two- and three-dot ranges. Does not handle Change-Ids; callers must treat those separately.
    """
    s = arg.strip()

    def one(ref: str) -> str:
        r = ref.strip()
        if not r:
            raise GitError(
                "git rev-parse --verify failed: empty revision",
                stderr="",
                returncode=-1,
            )
        # Use ``--`` only so refs starting with ``-`` are not parsed as options (plain ``--``
        # before a normal ref breaks ``git rev-parse`` on Git for Windows).
        if r.startswith("-"):
            return git_out("rev-parse", "--verify", "--", r, cwd=cwd)
        return git_out("rev-parse", "--verify", r, cwd=cwd)

    if "..." in s:
        left, _, right = s.partition("...")
        if not left.strip() or not right.strip():
            raise GitError(
                f"git rev-parse --verify failed: invalid symmetric range {arg!r}",
                stderr="",
                returncode=-1,
            )
        return f"{one(left)}...{one(right)}"
    if ".." in s:
        i = s.find("..")
        left, right = s[:i], s[i + 2 :]
        ls, rs = left.strip(), right.strip()
        if ls and rs:
            return f"{one(ls)}..{one(rs)}"
        if rs:
            return f"..{one(rs)}"
        if ls:
            return f"{one(ls)}.."
        raise GitError(
            f"git rev-parse --verify failed: invalid range {arg!r}",
            stderr="",
            returncode=-1,
        )
    return one(s)


def _gcid_log_single_commit(rev_spec: str) -> bool:
    """True if *rev_spec* is a single revision (one ``git log -1``), not a range."""
    if "..." in rev_spec:
        return False
    return ".." not in rev_spec


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gcid``: print or validate Change-Ids for commits or ranges (optional duplicate check)."""
    p = argparse.ArgumentParser(prog="git gcid")
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        help=(
            "Revision (anything git rev-parse --verify accepts), Change-Id (I…, passthrough), "
            "or range (rev1..rev2 or rev1...rev2). Defaults to HEAD."
        ),
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
            resolved = resolve_gcid_user_arg(cwd, input_arg)
            rev_spec = rev_spec_merge_base_to_end(cwd, resolved)
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
        resolved = resolve_gcid_user_arg(cwd, input_arg)
        if args.start_at_remote:
            rev_spec = rev_spec_merge_base_to_end(cwd, resolved)
            raw = git_log_sha_body(cwd, rev_spec, single=False)
        else:
            raw = git_log_sha_body(
                cwd, resolved, single=_gcid_log_single_commit(resolved)
            )
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
