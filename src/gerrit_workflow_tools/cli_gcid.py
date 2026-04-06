# gerrit change-id command (gcid)
# Return a Change-Id for a commit or range of commits.
# The Change-Id is taken from the last non-empty line when it matches "Change-Id: I…".

# Implementation
# Identify if the supplied argument is a valid Change-Id.
# If it is, output the Change-Id.
# Else use one ``git log`` over the revision or range (same RS-delimited %H / %B pattern as stack.py).
# Parse each commit message (last non-empty line must be the Change-Id).
# If the Change-Id is not found, output an error message.
# If the Change-Id is found, output the Change-Id.
# With ``--start-at-remote`` or ``--check-duplicates``, log ``merge_base..END``
# (same merge-base resolution as ``stack`` / ``gchangeid-check``).
# ``--check-duplicates`` exits 0 if all footers are valid and unique, 1 if a footer is missing, 2 on duplicates.

import argparse
import re
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import merge_base_with_target

CHANGE_ID_RE = re.compile(r"^Change-Id:\s*(I[a-f0-9]{40})$", re.MULTILINE)

# Field separator in `git log --format` (ASCII RS). Same as stack.py; %x1e in --format avoids NUL in argv on Windows.
_RS = "\x1e"
# Git expands %x1e in --format; keep in sync with stack_commits_metadata_one_log style.
_LOG_SHA_BODY_FMT = "%H%x1e%B%x1e"


def is_change_id(s: str) -> bool:
    return (
        s.startswith("I")
        and len(s) == 41
        and all(c in "0123456789abcdef" for c in s[1:])
    )


def extract_change_id_from_msg(msg: str) -> str | None:
    """
    Extract the Change-Id only if it is present in the last non-empty line of the commit message.
    """
    s = msg.rstrip("\n")
    i = s.rfind("\n")
    line = (s[i + 1 :] if i >= 0 else s).strip()
    if line:
        m = CHANGE_ID_RE.match(line)
        return m.group(1) if m else None
    return None


def _git_log_sha_body(cwd: Path, rev_spec: str, *, single: bool) -> str:
    """One ``git log``; stdout is RS-delimited %H / %B records (see ``_parse_sha_body_rs``)."""
    args: list[str] = ["log", f"--format={_LOG_SHA_BODY_FMT}"]
    if single:
        args.extend(["-1", rev_spec])
    else:
        args.append(rev_spec)
    return git_out(*args, cwd=cwd)


def _parse_sha_body_rs(raw: str) -> list[tuple[str, str]]:
    parts = raw.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        if i + 1 >= len(parts):
            break
        sha, msg = parts[i].strip(), parts[i + 1]
        out.append((sha, msg))
    return out


def _rev_spec_start_at_remote(cwd: Path, input_arg: str) -> str:
    """``merge_base..END`` where END is ``input_arg`` or the right side of ``left..right``."""
    mb, _, _ = merge_base_with_target(cwd)
    if ".." not in input_arg:
        end = git_out("rev-parse", input_arg, cwd=cwd)
        return f"{mb}..{end}"
    idx = input_arg.index("..")
    right = input_arg[idx + 2 :].strip() or "HEAD"
    end = git_out("rev-parse", right, cwd=cwd)
    return f"{mb}..{end}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gcid")
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        help="Commit SHA, Change-Id (I…), or range (sha1..sha2). Defaults to HEAD if not given.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Log git commands to stderr."
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
        if is_change_id(input_arg):
            print(
                "error: --check-duplicates needs a commit or range, not a Change-Id",
                file=sys.stderr,
            )
            return 2
        try:
            rev_spec = _rev_spec_start_at_remote(cwd, input_arg)
            raw = _git_log_sha_body(cwd, rev_spec, single=False)
        except GitError as e:
            return handle_git_error(e)
        pairs = _parse_sha_body_rs(raw)
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
    if is_change_id(input_arg):
        print(input_arg)
        return 0

    try:
        if args.start_at_remote:
            rev_spec = _rev_spec_start_at_remote(cwd, input_arg)
            raw = _git_log_sha_body(cwd, rev_spec, single=False)
        else:
            single = ".." not in input_arg
            raw = _git_log_sha_body(cwd, input_arg, single=single)
    except GitError as e:
        return handle_git_error(e)

    pairs = _parse_sha_body_rs(raw)
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
