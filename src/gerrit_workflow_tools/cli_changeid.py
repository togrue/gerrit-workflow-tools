"""CLI for inspecting and validating commit Change-Ids."""

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
# With ``--start-at-remote`` or ``--check-duplicates``, log ``upstream_tip..END``
# (same stack base as ``ger log`` default: :func:`stack.rev_spec_stack_base_to_end`).
# ``--check-duplicates`` exits 0 if all footers are valid and unique, 1 if a footer is missing, 2 on duplicates.

import argparse
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.cli_style import color_short_sha, init_color_mode
from gerrit_workflow_tools.core.change_id import (
    CHANGE_ID_LAST_LINE_FOOTER_RE,
    extract_change_id_from_msg,
    is_change_id_token,
)
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.stack import (
    git_log_sha_body,
    parse_git_log_sha_body_rs,
    rev_spec_target_tip_to_end,
)

# Re-export for tests and backwards compatibility.

CHANGE_ID_RE = CHANGE_ID_LAST_LINE_FOOTER_RE

_parse_sha_body_rs = parse_git_log_sha_body_rs


def resolve_gcid_user_arg(cwd: Path | str, arg: str) -> str:
    """Return *arg* as a ``git log`` revision/range with minimal normalization.

    We intentionally avoid pre-parsing revsets here and defer interpretation to
    ``git log`` itself so behavior matches normal Git as closely as possible.
    """
    del cwd  # kept for API compatibility
    s = arg.strip()
    if not s:
        raise GitError(
            "git log failed: empty revision",
            stderr="",
            returncode=-1,
        )
    return s


def _gcid_log_single_commit(rev_spec: str) -> bool:
    """True if *rev_spec* is a single revision (one ``git log -1``), not a range."""
    if "..." in rev_spec:
        return False
    return ".." not in rev_spec


class ChangeIdError(Exception):
    """Custom exception for Change-Id processing errors."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def check_duplicate_change_ids(cwd, input_arg) -> None:
    """Raise :class:`ChangeIdError` when duplicate Change-Ids are found in a commit selection."""

    if is_change_id_token(input_arg):
        raise ChangeIdError("error: --check-duplicates needs a commit or range, not a Change-Id", code=2)

    resolved = resolve_gcid_user_arg(cwd, input_arg)
    rev_spec = rev_spec_target_tip_to_end(cwd, resolved)
    raw = git_log_sha_body(cwd, rev_spec, single=False)

    pairs = parse_git_log_sha_body_rs(raw)
    seen: dict[str, str] = {}
    for sha, msg in pairs:
        cid = extract_change_id_from_msg(msg)
        if not cid:
            raise ChangeIdError(f"error: no Change-Id found in commit {color_short_sha(sha)}", code=1)
        if cid in seen:
            short = sha[:8]
            first = seen[cid][:8]
            raise ChangeIdError(
                f"error: duplicate Change-Id {cid} (commit {color_short_sha(short)}, also on {color_short_sha(first)})",
                code=2,
            )
        seen[cid] = sha


def print_change_ids_for_range(cwd, input_arg, use_remote: bool) -> None:
    """Print Change-Ids for one commit or a revision range resolved from user input."""

    resolved = resolve_gcid_user_arg(cwd, input_arg)
    if use_remote:
        rev_spec = rev_spec_target_tip_to_end(cwd, resolved)
        raw = git_log_sha_body(cwd, rev_spec, single=False)
    else:
        raw = git_log_sha_body(cwd, resolved, single=_gcid_log_single_commit(resolved))

    pairs = parse_git_log_sha_body_rs(raw)
    for sha, msg in pairs:
        cid = extract_change_id_from_msg(msg)
        if cid:
            print(cid)
        else:
            raise ChangeIdError(f"error: no Change-Id found in commit {color_short_sha(sha)}", code=1)


def main(argv: list[str] | None = None) -> int:
    """CLI for `ger change-id`.

    Prints or validates Change-Ids for commits or ranges, with optional duplicate checking.
    """
    p = argparse.ArgumentParser(prog="ger change-id")
    p.add_argument(
        "rev_or_range",
        nargs="?",
        default=None,
        metavar="REV_OR_RANGE",
        help=(
            "Revision (anything git rev-parse --verify accepts), Change-Id (I…, passthrough), "
            "or range (rev1..rev2 or rev1...rev2). Defaults to HEAD."
        ),
    )
    add_verbose_and_debug_log_args(p)
    p.add_argument(
        "--start-at-remote",
        action="store_true",
        help=(
            "Use upstream_tip..END (same stack window as default `ger log`) instead of the default revision resolution."
        ),
    )
    p.add_argument(
        "--check-duplicates",
        action="store_true",
        help="Check for duplicate Change-Ids across upstream_tip..END (same range as --start-at-remote).",
    )
    add_color_args(p)
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    init_color_mode(color=args.color)
    cwd = cwd_from_env()

    input_arg = args.rev_or_range or "HEAD"

    try:
        if args.check_duplicates:
            check_duplicate_change_ids(cwd, input_arg)
            return 0

        if is_change_id_token(input_arg):
            print(input_arg)
            return 0

        print_change_ids_for_range(cwd, input_arg, args.start_at_remote)
        return 0

    except ChangeIdError as err:
        print(str(err), file=sys.stderr)
        return getattr(err, "code", 1)
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
