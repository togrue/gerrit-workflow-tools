# gerrit change-id command (gcid)
# Return a Change-Id for a commit or range of commits.
# The Change-Id is taken from the last non-empty line when it matches "Change-Id: I…".

# Implementation
# Identify if the supplied argument is a valid Change-Id.
# If it is, output the Change-Id.
# Else use git-rev-parse to resolve the commit(s) or range of commits to the Change-Id.
# For that parse the commit message of each commit (last non-empty line must be the Change-Id).
# If the Change-Id is not found, output an error message.
# If the Change-Id is found, output the Change-Id.

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

CHANGE_ID_RE = re.compile(r"^Change-Id:\s*(I[a-f0-9]{40})$", re.MULTILINE)

# Record separator in `git log --format` (ASCII RS). Same as stack.py; avoids NUL in argv on Windows.
_RS = "\x1e"
# Stay under typical Windows ~8191 char command-line limits when passing many full SHAs.
_LOG_CHUNK = 80


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


def _change_ids_one_per_commit(commits: list[str], cwd: Path) -> dict[str, str | None]:
    ids: dict[str, str | None] = {}
    for c in commits:
        try:
            msg = git_out("log", "-1", "--format=%B", c, cwd=cwd)
            ids[c] = extract_change_id_from_msg(msg)
        except GitError:
            ids[c] = None
    return ids


def _change_ids_batch(commits: list[str], cwd: Path) -> dict[str, str | None] | None:
    """Parse one ``git log --no-walk`` batch. Returns None if git failed (caller may fall back)."""
    fmt = f"%H{_RS}%B{_RS}"
    try:
        raw = git_out(
            "log",
            "--no-walk=unsorted",
            f"--format={fmt}",
            *commits,
            cwd=cwd,
        )
    except GitError:
        return None
    parts = raw.split(_RS)
    while parts and parts[-1] == "":
        parts.pop()
    ids: dict[str, str | None] = {}
    for i in range(0, len(parts), 2):
        if i + 1 >= len(parts):
            break
        sha, msg = parts[i].strip(), parts[i + 1]
        ids[sha] = extract_change_id_from_msg(msg)
    return ids


def change_ids_for_commits(commits: list[str], cwd: Path) -> dict[str, str | None]:
    """Map each commit ref to Change-Id or None. Uses one ``git log`` per chunk when possible."""
    if not commits:
        return {}
    merged: dict[str, str | None] = {}
    for off in range(0, len(commits), _LOG_CHUNK):
        chunk = commits[off : off + _LOG_CHUNK]
        ids = _change_ids_batch(chunk, cwd)
        if ids is None:
            merged.update(_change_ids_one_per_commit(chunk, cwd))
        else:
            for c in chunk:
                merged[c] = ids.get(c)
    return merged


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gcid")
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        help="Commit SHA, Change-Id (I…), or range (sha1..sha2). Defaults to HEAD if not given.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="log git commands to stderr"
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()

    input_arg = args.arg or "HEAD"
    # If arg looks like a Change-Id, just output it
    if is_change_id(input_arg):
        print(input_arg)
        return 0

    try:
        # If it's a range, expand to all shas (inclusive)
        if ".." in input_arg:
            # git rev-list expands ranges inclusive of left, exclusive of right
            shas = git_out("rev-list", input_arg, cwd=cwd).splitlines()
        else:
            # Single commit, resolve sha
            sha = git_out("rev-parse", input_arg, cwd=cwd).strip()
            shas = [sha]
    except GitError as e:
        return handle_git_error(e)

    results = change_ids_for_commits(shas, cwd)
    for ref in shas:
        cid = results.get(ref)
        if cid:
            print(cid)
        else:
            print(f"error: no Change-Id found in commit {ref}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
