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
import json
import os
import shlex
import sys
import tempfile
import threading
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
    extract_valid_change_id,
    parse_change_id_footer,
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


def _ensure_clean_tree_for_fix(cwd: Path) -> None:
    status = git("status", "--porcelain", cwd=cwd, check=False)
    if status.returncode != 0:
        raise GitError("git status failed", stderr=status.stderr, returncode=status.returncode)
    if status.stdout.strip():
        raise ChangeIdError("error: --fix requires a clean working tree", code=int(ExitCode.USAGE))

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


def _msg_filter_script() -> str:
    return (
        "from pathlib import Path\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from gerrit_workflow_tools.core.change_id import (\n"
        "    append_change_id_footer,\n"
        "    extract_valid_change_id,\n"
        "    generate_change_id_for_commit,\n"
        "    strip_change_id_lines,\n"
        ")\n"
        "msg = sys.stdin.read()\n"
        "targets = {s for s in os.environ.get('GER_CID_TARGETS', '').split(',') if s}\n"
        "commit = os.environ['GIT_COMMIT']\n"
        "if commit not in targets:\n"
        "    sys.stdout.write(msg)\n"
        "    raise SystemExit(0)\n"
        "if extract_valid_change_id(msg):\n"
        "    sys.stdout.write(msg)\n"
        "    raise SystemExit(0)\n"
        "progress_path = os.environ.get('GER_CID_PROGRESS')\n"
        "log_path = os.environ.get('GER_CID_PROGRESS_LOG')\n"
        "if progress_path:\n"
        "    try:\n"
        "        progress = json.loads(Path(progress_path).read_text(encoding='utf-8'))\n"
        "        entry = progress.get('by_sha', {}).get(commit)\n"
        "        if entry:\n"
        "            idx, total, subject = entry['idx'], entry['total'], entry['subject']\n"
        "            line = f'{idx}/{total} Fixed \"{subject}\"\\n'\n"
        "            if log_path:\n"
        "                with open(log_path, 'a', encoding='utf-8') as log:\n"
        "                    log.write(line)\n"
        "            print(line, end='', file=sys.stderr, flush=True)\n"
        "    except OSError:\n"
        "        pass\n"
        "base = strip_change_id_lines(msg)\n"
        "cwd = Path(os.environ['GER_CID_REPO'])\n"
        "cid = generate_change_id_for_commit(cwd, commit, base)\n"
        "sys.stdout.write(append_change_id_footer(base, cid))\n"
    )


def _emit_progress_log_lines(log_path: Path, *, start: int = 0) -> int:
    """Print new progress lines from *log_path* to stderr; return new file size."""
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return start
    if len(text) <= start:
        return start
    for line in text[start:].splitlines():
        if line.strip():
            print(line, file=sys.stderr, flush=True)
    return len(text)


def _tail_progress_log(log_path: Path, stop: threading.Event) -> None:
    """Forward progress log lines to stderr while filter-branch runs."""
    offset = 0
    while True:
        offset = _emit_progress_log_lines(log_path, start=offset)
        if stop.is_set():
            break
        stop.wait(0.05)


def fix_change_ids_for_stack(cwd: Path, input_arg: str) -> None:
    """Rewrite stack commit messages, assigning Change-Ids where missing on the last line."""
    if is_change_id(input_arg):
        raise ChangeIdError("error: --fix needs a commit or range, not a Change-Id", code=int(ExitCode.USAGE))

    _ensure_clean_tree_for_fix(cwd)
    resolved = resolve_gcid_user_arg(cwd, input_arg)
    rev_spec = rev_spec_target_tip_to_end(cwd, resolved)
    rows = commits_in_range(cwd, rev_spec)
    if not rows:
        return

    needs_fix = [c for c in rows if not extract_valid_change_id(c.body)]
    if not needs_fix:
        return

    total = len(needs_fix)
    progress_payload = {
        "by_sha": {
            c.sha: {"idx": i, "total": total, "subject": c.subject}
            for i, c in enumerate(needs_fix, start=1)
        }
    }

    base, _end = rev_spec.split("..", 1)
    head_ref_proc = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if head_ref_proc.returncode != 0:
        raise GitError(
            "git rev-parse --abbrev-ref HEAD failed",
            stderr=head_ref_proc.stderr,
            returncode=head_ref_proc.returncode,
        )
    head_ref = head_ref_proc.stdout.strip()

    src_path = Path(__file__).parent.parent
    py_path = os.environ.get("PYTHONPATH")
    env = {
        "FILTER_BRANCH_SQUELCH_WARNING": "1",
        "GER_CID_REPO": str(cwd),
        "GER_CID_TARGETS": ",".join(c.sha for c in rows),
        "PYTHONPATH": f"{src_path}{os.pathsep}{py_path}" if py_path else str(src_path),
    }
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8") as f:
        f.write(_msg_filter_script())
        script_path = Path(f.name)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
        json.dump(progress_payload, f)
        progress_path = Path(f.name)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log", encoding="utf-8") as f:
        progress_log_path = Path(f.name)
    try:
        env["GER_CID_PROGRESS"] = str(progress_path)
        env["GER_CID_PROGRESS_LOG"] = str(progress_log_path)
        cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"
        stop_tail = threading.Event()
        tail_thread = threading.Thread(
            target=_tail_progress_log,
            args=(progress_log_path, stop_tail),
            daemon=True,
        )
        tail_thread.start()
        try:
            p = git(
                "filter-branch",
                "-f",
                "--msg-filter",
                cmd,
                "--",
                head_ref,
                "--not",
                base,
                cwd=cwd,
                env=env,
                check=False,
            )
        finally:
            stop_tail.set()
            tail_thread.join(timeout=2.0)
        if p.returncode != 0:
            raise ChangeIdError(f"error: --fix failed: {p.stderr.strip() or p.stdout.strip()}", code=int(ExitCode.GIT))
    finally:
        script_path.unlink(missing_ok=True)
        progress_path.unlink(missing_ok=True)
        progress_log_path.unlink(missing_ok=True)


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
