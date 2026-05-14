"""CLI for ``ger fix``: create a ``git commit --fixup`` targeting a ref or Gerrit change."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.cli_common import (
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.core.change_id import CHANGE_ID_VALUE_RE, is_change_id_token
from gerrit_workflow_tools.core.config import gerrit_remote
from gerrit_workflow_tools.core.gerrit_client import (
    GerritApiError,
    GerritClient,
    resolve_gerrit_change,
    resolve_gerrit_web_base,
)
from gerrit_workflow_tools.core.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)

_REFS_CHANGES = re.compile(r"^refs/changes/\d+/\d+/\d+$")


def _wants_gerrit_resolution(arg: str) -> bool:
    token = arg.strip()
    if not token:
        return False
    if is_change_id_token(token):
        return True
    if CHANGE_ID_VALUE_RE.match(token):
        return True
    return token.isdigit()


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


def _resolve_fixup_sha_gerrit(cwd: Path, client: GerritClient, arg: str) -> str:
    change = resolve_gerrit_change(client, change_arg=arg.strip(), local_change_id=None)
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


def _resolve_fixup_sha_git(cwd: Path, arg: str) -> str:
    token = arg.strip()
    p = git("rev-parse", "--verify", f"{token}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        raise GitError(
            f"not a valid commit-ish: {token!r} ({p.stderr.strip() or p.stdout.strip()})",
            stderr=p.stderr,
            returncode=p.returncode,
        )
    return git_out("rev-parse", token, cwd=cwd)


def _index_has_staged_changes(cwd: Path) -> bool:
    d = git("diff", "--cached", "--quiet", cwd=cwd, check=False)
    return d.returncode != 0


def main(argv: list[str] | None = None) -> int:
    """Create a fixup commit with ``git commit --fixup=<target>``."""
    p = argparse.ArgumentParser(
        prog="ger fix",
        description=(
            "Create a fixup commit (``git commit --fixup``) targeting a local ref, a ``refs/changes/…`` ref, "
            "or a Gerrit change (numeric id or Change-Id). "
            "By default only **staged** changes are committed; use ``-a`` to include all modifications to "
            "tracked files."
        ),
    )
    p.add_argument(
        "target",
        metavar="REF_OR_CHANGE",
        help=(
            "Commit-ish (branch, SHA, HEAD~n, …), a Gerrit ``refs/changes/AA/NNNNN/PS`` ref, "
            "a numeric change number, or a Change-Id (I…)."
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
    add_verbose_and_debug_log_args(p, debug_log_help="Log resolution steps to stderr.")
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    try:
        raw = args.target
        rc_ref = _refs_changes_ref(raw)
        if rc_ref is not None:
            fixup_sha = _resolve_fixup_sha_refs_changes(cwd, rc_ref)
        elif _wants_gerrit_resolution(raw):
            web_base = resolve_gerrit_web_base(cwd)
            client = GerritClient(web_base, cwd=str(cwd))
            fixup_sha = _resolve_fixup_sha_gerrit(cwd, client, raw)
        else:
            fixup_sha = _resolve_fixup_sha_git(cwd, raw)

        logger.info("fixup target commit: %s", fixup_sha)

        if not args.commit_all and not _index_has_staged_changes(cwd):
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
        return 0

    except GerritApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
