"""Interactive helpers for configuring branch upstream when missing."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter

from gerrit_workflow_tools.core.config import (
    clear_gerrit_git_config_cache,
    infer_nearest_remote_tracking_branch,
    is_detached_head,
)
from gerrit_workflow_tools.core.git_run import git

logger = logging.getLogger(__name__)

_RECENT_UPSTREAM_KEY = "gerrit.recentUpstreamAbbrev"
_RECENT_UPSTREAM_LIMIT = 20


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def branch_has_upstream(cwd: Path | str | None, branch: str) -> bool:
    p = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd, check=False)
    return p.returncode == 0


def read_recent_upstream_abbrevs(cwd: Path | str | None) -> list[str]:
    p = git("config", "--global", "--get-all", _RECENT_UPSTREAM_KEY, cwd=cwd, check=False)
    if p.returncode != 0:
        return []
    return _dedupe_preserve_order((p.stdout or "").splitlines())


def _write_recent_upstream_abbrevs(cwd: Path | str | None, values: list[str]) -> None:
    current = _dedupe_preserve_order(values)[:_RECENT_UPSTREAM_LIMIT]
    git("config", "--global", "--unset-all", _RECENT_UPSTREAM_KEY, cwd=cwd, check=False)
    for value in current:
        git("config", "--global", "--add", _RECENT_UPSTREAM_KEY, value, cwd=cwd)


def append_recent_upstream_abbrev(cwd: Path | str | None, abbrev: str) -> None:
    candidate = abbrev.strip()
    if not candidate:
        return
    resolved = git("rev-parse", "--verify", candidate, cwd=cwd, check=False)
    if resolved.returncode != 0:
        logger.debug("skip unresolved recent upstream candidate: %r", candidate)
        return
    recent = read_recent_upstream_abbrevs(cwd)
    _write_recent_upstream_abbrevs(cwd, [candidate, *recent])


def _remote_tracking_ref_abbrevs(cwd: Path | str | None) -> list[str]:
    p = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/", cwd=cwd, check=False)
    if p.returncode != 0:
        return []
    return _dedupe_preserve_order(
        [line.strip() for line in (p.stdout or "").splitlines() if line.strip() and not line.strip().endswith("/HEAD")]
    )


def _upstream_choice_candidates(cwd: Path | str | None) -> tuple[list[str], str]:
    recent = read_recent_upstream_abbrevs(cwd)
    inferred = infer_nearest_remote_tracking_branch(cwd, "HEAD")
    inferred_ref = inferred[0] if inferred else ""
    refs = _remote_tracking_ref_abbrevs(cwd)
    ordered = _dedupe_preserve_order([*recent, inferred_ref, *refs])
    if len(ordered) > 200:
        keep = _dedupe_preserve_order([*recent, inferred_ref])
        overflow = [item for item in ordered if item not in set(keep)]
        ordered = [*keep, *overflow[: max(0, 200 - len(keep))]]
    return ordered, inferred_ref


def prompt_upstream_abbrev_interactive(cwd: Path | str | None, branch: str) -> str | None:
    candidates, inferred_ref = _upstream_choice_candidates(cwd)
    default = inferred_ref or (candidates[0] if candidates else "")
    completer = WordCompleter(candidates, ignore_case=False, sentence=True)
    session = PromptSession()
    print(
        f"No upstream configured for branch {branch!r}.",
        file=sys.stderr,
    )
    print("Set upstream (TAB to complete, Enter on empty line to abort).", file=sys.stderr)
    try:
        choice = session.prompt(
            f"Set upstream for {branch!r}: ",
            default=default,
            completer=completer,
            complete_while_typing=True,
        )
    except (EOFError, KeyboardInterrupt):
        print("Aborted.", file=sys.stderr)
        return None
    chosen = choice.strip()
    if not chosen:
        print("Aborted.", file=sys.stderr)
        return None
    p = git("rev-parse", "--verify", chosen, cwd=cwd, check=False)
    if p.returncode != 0:
        print(f"error: {chosen!r} is not a known local ref. Run `git fetch` and try again.", file=sys.stderr)
        return None
    return chosen


def ensure_branch_upstream_interactive(cwd: Path | str | None, branch: str) -> bool:
    if is_detached_head(cwd):
        return False
    if branch_has_upstream(cwd, branch):
        return True
    if not sys.stdin.isatty():
        return False
    choice = prompt_upstream_abbrev_interactive(cwd, branch)
    if not choice:
        return False
    git("branch", "--set-upstream-to", choice, branch, cwd=cwd)
    clear_gerrit_git_config_cache()
    append_recent_upstream_abbrev(cwd, choice)
    print(f"Upstream for {branch!r} set to {choice}.", file=sys.stderr)
    return True
