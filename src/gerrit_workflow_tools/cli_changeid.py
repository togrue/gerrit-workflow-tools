"""CLI for inspecting and validating commit Change-Ids."""

# gerrit change-id command (gcid)
# Return a Change-Id for a commit or range of commits.
# The Change-Id is taken from the last non-empty line when it matches "Change-Id: I…".

# Implementation
# Identify if the supplied argument is a valid Change-Id.
# If it is, output the Change-Id.
# Else normalize the revision with ``git rev-parse --verify`` (each side of ``..`` / ``...``), then run one
# ``git log`` over the revision or range (RS-delimited %H / %B: :func:`stack.git_log_sha_body`).
# Parse each commit message (last non-empty line must be the Change-Id footer; see
# :func:`change_id.parse_change_id_footer` for extraction and `validate_change_id_value` for the verdict).
# If the Change-Id is not found, output an error message.
# If the Change-Id is found, output the Change-Id.
# With ``--start-at-remote``, log ``upstream_tip..END``
# (same stack base as ``ger log`` default: :func:`stack.rev_spec_stack_base_to_end`).
# ``--check`` always scans the full current stack (``upstream_tip..HEAD``): exits 0 if all footers
# are valid and unique, ``MISSING_CHANGE_ID`` if a footer is missing/invalid, ``DUPLICATE_CHANGE_ID``
# on duplicates. A ``REV_OR_RANGE`` with ``--check`` is a usage error.

import argparse
import sys
import tempfile
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.cli_style import color_short_sha, init_color_mode, init_hyperlink_mode
from gerrit_workflow_tools.core.change_id import (
    CHANGE_ID_FOOTER_RE,
    append_change_id_footer,
    extract_valid_change_id,
    generate_change_id_for_commit,
    parse_change_id_footer,
    strip_change_id_lines,
    validate_change_id_value,
)
from gerrit_workflow_tools.core.changeish import is_change_id
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.stack import (
    commits_in_range,
    git_log_sha_body,
    parse_git_log_sha_body_rs,
    rev_spec_target_tip_to_end,
)
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream

# Re-export for tests and backwards compatibility.

CHANGE_ID_RE = CHANGE_ID_FOOTER_RE

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


def check_stack_change_ids(cwd) -> None:
    """Raise :class:`ChangeIdError` when the current stack has missing/invalid/duplicate Change-Ids.

    Always scans ``upstream_tip..HEAD`` (the full local stack).
    """
    rev_spec = rev_spec_target_tip_to_end(cwd, "HEAD")
    raw = git_log_sha_body(cwd, rev_spec, single=False)

    pairs = parse_git_log_sha_body_rs(raw)
    seen: dict[str, str] = {}
    for sha, msg in pairs:
        cid = _require_change_id(sha, msg)
        if cid in seen:
            short = sha[:8]
            first = seen[cid][:8]
            raise ChangeIdError(
                f"error: duplicate Change-Id {cid} (commit {color_short_sha(short)}, also on {color_short_sha(first)})",
                code=int(ExitCode.DUPLICATE_CHANGE_ID),
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
        print(_require_change_id(sha, msg))


def _require_change_id(sha: str, msg: str) -> str:
    """Return the commit's Change-Id, distinguishing a malformed footer from a missing one."""
    raw = parse_change_id_footer(msg)
    valid, malformed = validate_change_id_value(raw)
    if valid:
        assert raw is not None
        return raw
    if malformed:
        # Same reason as missing -- no usable Change-Id -- but say which, since a garbage
        # footer needs a different fix from an absent one.
        raise ChangeIdError(
            f"error: invalid Change-Id {raw!r} in commit {color_short_sha(sha)}",
            code=int(ExitCode.MISSING_CHANGE_ID),
        )
    raise ChangeIdError(
        f"error: no Change-Id found in commit {color_short_sha(sha)}",
        code=int(ExitCode.MISSING_CHANGE_ID),
    )


def _ensure_safe_for_fix(cwd: Path) -> None:
    """Block --fix during merge/rebase; uncommitted changes are fine (message-only rewrite)."""
    merge_head = git("rev-parse", "--verify", "MERGE_HEAD", cwd=cwd, check=False)
    if merge_head.returncode == 0:
        raise ChangeIdError("error: --fix cannot run during an in-progress merge", code=int(ExitCode.USAGE))

    git_dir = git("rev-parse", "--git-dir", cwd=cwd, check=False)
    if git_dir.returncode != 0:
        raise GitError("git rev-parse --git-dir failed", stderr=git_dir.stderr, returncode=git_dir.returncode)
    git_dir_path = Path(git_dir.stdout.strip())
    if not git_dir_path.is_absolute():
        git_dir_path = cwd / git_dir_path
    if (git_dir_path / "rebase-merge").exists() or (git_dir_path / "rebase-apply").exists():
        raise ChangeIdError("error: --fix cannot run during an in-progress rebase", code=int(ExitCode.USAGE))


def _commit_author_env(cwd: Path, sha: str) -> dict[str, str]:
    """Return GIT_AUTHOR_* / GIT_COMMITTER_* env vars copied from *sha*."""
    p = git(
        "log",
        "-1",
        "--format=%an%x1e%ae%x1e%ai%x1e%cn%x1e%ce%x1e%ci",
        sha,
        cwd=cwd,
        check=False,
    )
    if p.returncode != 0:
        raise GitError("git log failed", stderr=p.stderr, returncode=p.returncode)
    parts = p.stdout.rstrip("\n").split("\x1e")
    if len(parts) != 6:
        raise GitError("git log returned unexpected author format", stderr=p.stdout, returncode=1)
    an, ae, ad, cn, ce, cd = parts
    return {
        "GIT_AUTHOR_NAME": an,
        "GIT_AUTHOR_EMAIL": ae,
        "GIT_AUTHOR_DATE": ad,
        "GIT_COMMITTER_NAME": cn,
        "GIT_COMMITTER_EMAIL": ce,
        "GIT_COMMITTER_DATE": cd,
    }


def _commit_parents(cwd: Path, sha: str) -> list[str]:
    """Return parent SHAs for *sha* (empty for root commits)."""
    p = git("rev-list", "--parents", "-n", "1", sha, cwd=cwd, check=False)
    if p.returncode != 0:
        raise GitError("git rev-list failed", stderr=p.stderr, returncode=p.returncode)
    fields = p.stdout.split()
    return fields[1:]


def _commit_tree(cwd: Path, tree: str, parents: list[str], message: str, ident_env: dict[str, str]) -> str:
    """Create a commit with *message* and return its SHA."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".msg", encoding="utf-8") as f:
        f.write(message)
        msg_path = Path(f.name)
    try:
        args = ["commit-tree", tree, "-F", str(msg_path)]
        for parent in parents:
            args.extend(["-p", parent])
        p = git(*args, cwd=cwd, env=ident_env, check=False)
        if p.returncode != 0:
            raise GitError(
                "git commit-tree failed",
                stderr=p.stderr.strip() or p.stdout.strip(),
                returncode=p.returncode,
            )
        return p.stdout.strip()
    finally:
        msg_path.unlink(missing_ok=True)


def fix_change_ids_for_stack(cwd: Path, input_arg: str) -> None:
    """Rewrite stack commit messages, assigning Change-Ids where missing on the last line."""
    if is_change_id(input_arg):
        raise ChangeIdError("error: --fix needs a commit or range, not a Change-Id", code=int(ExitCode.USAGE))

    _ensure_safe_for_fix(cwd)
    resolved = resolve_gcid_user_arg(cwd, input_arg)
    rev_spec = rev_spec_target_tip_to_end(cwd, resolved)
    rows = commits_in_range(cwd, rev_spec)
    if not rows:
        return

    needs_fix = [c for c in rows if not extract_valid_change_id(c.body)]
    if not needs_fix:
        return

    total = len(needs_fix)
    fix_progress = {c.sha: (i, c.subject) for i, c in enumerate(needs_fix, start=1)}

    head_ref_proc = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if head_ref_proc.returncode != 0:
        raise GitError(
            "git rev-parse --abbrev-ref HEAD failed",
            stderr=head_ref_proc.stderr,
            returncode=head_ref_proc.returncode,
        )
    head_ref = head_ref_proc.stdout.strip()

    sha_map: dict[str, str] = {}
    new_head: str | None = None
    for commit in rows:
        tree_p = git("rev-parse", f"{commit.sha}^{{tree}}", cwd=cwd, check=False)
        if tree_p.returncode != 0:
            raise GitError("git rev-parse tree failed", stderr=tree_p.stderr, returncode=tree_p.returncode)
        tree = tree_p.stdout.strip()

        parents = _commit_parents(cwd, commit.sha)
        new_parents = [sha_map.get(p, p) for p in parents]

        if commit.sha in fix_progress:
            idx, subject = fix_progress[commit.sha]
            print(f'{idx}/{total} Fixed "{subject}"', file=sys.stderr, flush=True)
            base_msg = strip_change_id_lines(commit.body)
            cid = generate_change_id_for_commit(cwd, commit.sha, base_msg)
            message = append_change_id_footer(base_msg, cid)
        else:
            message = commit.body

        ident_env = _commit_author_env(cwd, commit.sha)
        new_sha = _commit_tree(cwd, tree, new_parents, message, ident_env)
        sha_map[commit.sha] = new_sha
        new_head = new_sha

    assert new_head is not None
    if head_ref == "HEAD":
        ref = "HEAD"
    else:
        ref = f"refs/heads/{head_ref}"
    update = git("update-ref", ref, new_head, cwd=cwd, check=False)
    if update.returncode != 0:
        raise ChangeIdError(
            f"error: --fix failed: {update.stderr.strip() or update.stdout.strip()}",
            code=int(ExitCode.GIT),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI for `ger change-id`.

    Prints or validates Change-Ids for commits or ranges, with optional stack checking.
    """
    p = _build_parser()
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    init_color_mode(color=args.color)
    init_hyperlink_mode(hyperlinks=args.hyperlinks)
    cwd = cwd_from_env()
    settings = Settings.from_cwd(cwd)

    input_arg = args.rev_or_range or "HEAD"

    try:
        needs_stack_upstream = bool(args.start_at_remote or args.check or args.fix)
        if needs_stack_upstream:
            head_ref_proc = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
            if head_ref_proc.returncode == 0:
                branch = head_ref_proc.stdout.strip()
                if branch != "HEAD" and not require_branch_upstream(cwd, branch, settings=settings):
                    return 1

        if args.fix and args.check:
            raise ChangeIdError("error: --fix cannot be combined with --check", code=int(ExitCode.USAGE))

        if args.fix:
            fix_change_ids_for_stack(cwd, input_arg)
            return 0

        if args.check:
            if args.rev_or_range is not None:
                raise ChangeIdError(
                    "error: --check always scans the full current stack; do not pass REV_OR_RANGE",
                    code=int(ExitCode.USAGE),
                )
            check_stack_change_ids(cwd)
            return 0

        if is_change_id(input_arg):
            print(input_arg)
            return 0

        print_change_ids_for_range(cwd, input_arg, args.start_at_remote)
        return 0

    except ChangeIdError as err:
        print(str(err), file=sys.stderr)
        return getattr(err, "code", 1)
    except GitError as e:
        return handle_git_error(e)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger change-id``."""
    p = argparse.ArgumentParser(prog="ger change-id")
    p.add_argument(
        "rev_or_range",
        nargs="?",
        default=None,
        metavar="REV_OR_RANGE",
        help=(
            "Revision (anything git rev-parse --verify accepts), Change-Id (I…, passthrough), "
            "or range (rev1..rev2 or rev1...rev2). Defaults to HEAD. Not allowed with --check."
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
        "--check",
        action="store_true",
        help=(
            "Validate all commits in the current local stack (upstream_tip..HEAD): "
            "every commit needs a valid unique Change-Id."
        ),
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Assign Change-Ids for commits in upstream_tip..END when the last non-empty message line has no Change-Id."
        ),
    )
    add_color_args(p)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
