"""CLI for ``ger fix``: create a ``git commit --fixup`` targeting a ref or Gerrit change."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    run_cli_command,
)
from gerrit_workflow_tools.core.config import gerrit_remote
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    classify_changeish,
    format_resolution_note,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    HttpGerritRest,
    resolve_gerrit_web_base,
)
from gerrit_workflow_tools.core.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)

_REFS_CHANGES = re.compile(r"^refs/changes/\d+/\d+/\d+$")


def _refs_changes_ref(arg: str) -> str | None:
    token = arg.strip()
    return token if _REFS_CHANGES.match(token) else None


def _revision_fetch_ref(change: dict[str, Any], sha: str) -> str:
    """Return a ``refs/changes/…`` ref suitable for ``git fetch <remote> <ref>``."""
    revs = change.get("revisions")
    if isinstance(revs, dict):
        info = revs.get(sha)
        if isinstance(info, dict):
            ref = info.get("ref")
            if isinstance(ref, str) and ref.startswith("refs/changes/"):
                return ref
    num = change.get("_number")
    if not isinstance(num, int):
        raise GitError(
            "Gerrit change has no usable refs/changes ref (missing revisions.ref and _number)",
            stderr="",
            returncode=1,
        )
    ps = 1
    if isinstance(revs, dict):
        info = revs.get(sha)
        if isinstance(info, dict):
            ps_n = info.get("_number")
            if isinstance(ps_n, int):
                ps = ps_n
    mod = num % 100
    return f"refs/changes/{mod:02d}/{num}/{ps}"


def _commit_object_exists(cwd: Path, sha: str) -> bool:
    p = git("rev-parse", "-q", "--verify", f"{sha}^{{commit}}", cwd=cwd, check=False)
    return p.returncode == 0


def _resolve_fixup_sha_refs_changes(cwd: Path, ref: str) -> str:
    if _commit_object_exists(cwd, ref):
        return git_out("rev-parse", ref, cwd=cwd)
    remote = gerrit_remote(cwd)
    fp = git("fetch", remote, ref, cwd=cwd, check=False)
    if fp.returncode != 0:
        raise GitError(
            f"could not resolve {ref!r} locally and `git fetch {remote} {ref}` failed: "
            f"{fp.stderr.strip() or fp.stdout.strip()}",
            stderr=fp.stderr,
            returncode=fp.returncode,
        )
    return git_out("rev-parse", "FETCH_HEAD", cwd=cwd)


def _resolve_fixup_sha_from_change_row(cwd: Path, change: dict[str, Any]) -> str:
    sha = change.get("current_revision")
    if not isinstance(sha, str) or not sha.strip():
        raise GitError("Gerrit change has no current_revision", stderr="", returncode=1)
    sha = sha.strip()
    if _commit_object_exists(cwd, sha):
        return git_out("rev-parse", sha, cwd=cwd)
    fetch_ref = _revision_fetch_ref(change, sha)
    remote = gerrit_remote(cwd)
    fp = git("fetch", remote, fetch_ref, cwd=cwd, check=False)
    if fp.returncode != 0:
        raise GitError(
            f"Gerrit revision {sha[:12]}… is not present locally; "
            f"`git fetch {remote} {fetch_ref}` failed: {fp.stderr.strip() or fp.stdout.strip()}",
            stderr=fp.stderr,
            returncode=fp.returncode,
        )
    got = git_out("rev-parse", "FETCH_HEAD", cwd=cwd)
    if not _commit_object_exists(cwd, got):
        raise GitError("fetch did not yield a valid commit", stderr="", returncode=1)
    return got


def _resolve_fixup_sha_gerrit(cwd: Path, client: GerritRest, arg: str) -> tuple[str, Resolution]:
    resolution = resolve_changeish(arg.strip(), client=client, cwd=cwd, explicit_target=True)
    if resolution.selected is None:
        raise ChangeResolutionError(f"Gerrit change not found for {arg.strip()!r}")
    change = client.get_change(resolution.selected.triplet)
    fixup_sha = _resolve_fixup_sha_from_change_row(cwd, change)
    return fixup_sha, resolution


def _resolve_fixup_sha_git(cwd: Path, arg: str, *, gerrit: GerritRest | None = None) -> tuple[str, Resolution | None]:
    token = arg.strip()
    p = git("rev-parse", "--verify", f"{token}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        raise GitError(
            f"not a valid commit-ish: {token!r} ({p.stderr.strip() or p.stdout.strip()})",
            stderr=p.stderr,
            returncode=p.returncode,
        )
    sha = git_out("rev-parse", token, cwd=cwd)
    resolution: Resolution | None = None
    try:
        client = gerrit if gerrit is not None else HttpGerritRest.from_cwd(resolve_gerrit_web_base(cwd), cwd)
        resolution = resolve_changeish(token, client=client, cwd=cwd, explicit_target=True)
    except (ValueError, ChangeResolutionError, GerritApiError):
        resolution = None
    return sha, resolution


def _index_has_staged_changes(cwd: Path) -> bool:
    d = git("diff", "--cached", "--quiet", cwd=cwd, check=False)
    return d.returncode != 0


def _worktree_has_unstaged_tracked_changes(cwd: Path) -> bool:
    """True when tracked files have unstaged modifications (``git diff``)."""
    d = git("diff", "--quiet", cwd=cwd, check=False)
    return d.returncode != 0


def _stage_tracked_modifications(cwd: Path) -> None:
    """Stage all modifications/deletions to tracked files (``git add -u``)."""
    git("add", "-u", cwd=cwd)


def _print_unstaged_diff(cwd: Path) -> None:
    """Print the unstaged tracked-file diff to stderr."""
    cp = git("diff", cwd=cwd, check=False)
    text = (cp.stdout or "").rstrip("\n")
    if text:
        print(text, file=sys.stderr)
    else:
        print("(no unstaged diff)", file=sys.stderr)


def _prompt_stage_modified_changes(cwd: Path) -> bool:
    """Interactively offer to stage unstaged tracked modifications.

    Returns True if the user accepted and files were staged. Only runs when
    stdin is a TTY and there are unstaged tracked changes.
    """
    if not sys.stdin.isatty():
        return False
    if not _worktree_has_unstaged_tracked_changes(cwd):
        return False

    print(
        "No staged changes. Stage all modifications to tracked files for this fixup?",
        file=sys.stderr,
    )
    while True:
        try:
            print("[y/n/d] (d = show diff): ", end="", file=sys.stderr, flush=True)
            raw = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return False
        if raw in ("y", "yes"):
            _stage_tracked_modifications(cwd)
            return True
        if raw in ("n", "no", ""):
            return False
        if raw in ("d", "diff"):
            _print_unstaged_diff(cwd)
            continue
        print("Please answer y, n, or d.", file=sys.stderr)


def _gerrit_changeish_kind(arg: str) -> str | None:
    kind = classify_changeish(arg)
    if kind == "git-rev":
        return None
    return kind


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger fix``."""
    p = argparse.ArgumentParser(
        prog="ger fix",
        description=(
            "Create a fixup commit (``git commit --fixup``) targeting a local ref, a ``refs/changes/…`` ref, "
            "or a Gerrit change (numeric id or Change-Id). "
            "By default only **staged** changes are committed; use ``-a`` to include all modifications to "
            "tracked files. When the index is empty and stdin is a TTY, you are prompted to stage "
            "tracked modifications (with an option to show the diff)."
        ),
    )
    p.add_argument(
        "target",
        metavar="REF_OR_CHANGE",
        help=(
            "Commit-ish (branch, SHA, HEAD~n, …), a Gerrit ``refs/changes/AA/NNNNN/PS`` ref, "
            "``change:<n>`` for a numeric change id, or a Change-Id (I…)."
        ),
    )
    p.add_argument(
        "-a",
        "--all",
        action="store_true",
        dest="commit_all",
        help="Pass ``-a`` to ``git commit`` (stage all modifications to tracked files, then commit).",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="Bypass pre-commit and commit-msg hooks (passed through to ``git commit``).",
    )
    p.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    add_verbose_and_debug_log_args(p, debug_log_help="Log resolution steps to stderr.")
    return p


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Create a fixup commit with ``git commit --fixup=<target>``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    raw = args.target
    resolution: Resolution | None = None
    rc_ref = _refs_changes_ref(raw)
    if rc_ref is not None:
        fixup_sha = _resolve_fixup_sha_refs_changes(cwd, rc_ref)
    elif _gerrit_changeish_kind(raw) is not None:
        client = gerrit if gerrit is not None else HttpGerritRest.from_cwd(resolve_gerrit_web_base(cwd), cwd)
        fixup_sha, resolution = _resolve_fixup_sha_gerrit(cwd, client, raw)
    else:
        fixup_sha, resolution = _resolve_fixup_sha_git(cwd, raw, gerrit=gerrit)

    logger.info("fixup target commit: %s", fixup_sha)

    if resolution is not None:
        note = format_resolution_note(resolution)
        if note:
            print(note, file=sys.stderr)

    if not args.commit_all and not _index_has_staged_changes(cwd):
        if not _prompt_stage_modified_changes(cwd) or not _index_has_staged_changes(cwd):
            print(
                "error: no staged changes (index empty). Stage edits with `git add`, "
                "or use `ger fix -a …` to commit all changes to tracked files.",
                file=sys.stderr,
            )
            return 1

    cmd: list[str] = ["-c", "core.editor=true", "commit"]
    if args.no_verify:
        cmd.append("--no-verify")
    if args.commit_all:
        cmd.append("-a")
    cmd.extend(["--fixup", fixup_sha])

    cp = git(*cmd, cwd=cwd, check=False)
    if cp.returncode != 0:
        print(cp.stderr.strip() or cp.stdout.strip() or "git commit failed", file=sys.stderr)
        return cp.returncode or 1

    if args.json_:
        payload: dict[str, object] = {"fixup_sha": fixup_sha}
        if resolution is not None:
            payload["resolution"] = resolution.to_json_dict()
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
