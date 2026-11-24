"""Asking for a missing upstream at a terminal: the CLI side of ``core/upstream.py``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_terminal import prompt_toolkit_can_run
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import is_detached_head
from gerrit_workflow_tools.core.upstream import (
    NeedsUpstreamError,
    branch_has_upstream,
    local_branch_exists,
    missing_upstream_error,
    set_branch_upstream,
    upstream_choice,
)


logger = logging.getLogger(__name__)


def prompt_upstream_abbrev_interactive(cwd: Path | str | None, branch: str, *, settings: Settings) -> str | None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter

    choice_ = upstream_choice(cwd, branch, settings=settings)
    candidates = choice_.candidates
    default = choice_.suggested or (candidates[0] if candidates else "")
    completer = WordCompleter(candidates, ignore_case=False, sentence=True)
    session: PromptSession[str] = PromptSession()
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
    p = git("rev-parse", "--verify", "--quiet", f"refs/remotes/{chosen}", cwd=cwd, check=False)
    if p.returncode != 0:
        print(f"error: {chosen!r} is not a remote-tracking branch. Run `git fetch` and try again.", file=sys.stderr)
        return None
    return chosen


def ensure_branch_upstream_interactive(cwd: Path | str | None, branch: str, *, settings: Settings) -> bool:
    if is_detached_head(cwd):
        return False
    if branch_has_upstream(cwd, branch):
        return True
    if not prompt_toolkit_can_run():
        return False
    return ask_branch_upstream(cwd, branch, settings=settings)


def ask_branch_upstream(cwd: Path | str | None, branch: str, *, settings: Settings) -> bool:
    """Ask for *branch*'s upstream and set it; ``False`` when the user aborts or names no remote branch."""
    choice = prompt_upstream_abbrev_interactive(cwd, branch, settings=settings)
    if not choice:
        return False
    set_branch_upstream(cwd, branch, choice)
    print(f"Upstream for {branch!r} set to {choice}.", file=sys.stderr)
    return True


def require_branch_upstream(
    cwd: Path | str | None, branch: str, *, settings: Settings, interactive: bool = True
) -> bool:
    """Return True when *branch* has upstream (possibly set via TTY prompt).

    ``False`` means the user declined the prompt. With no terminal to prompt on, or with
    *interactive* off, raise :class:`ConfigError` with the setup guidance every command shares.
    Non-existent local branches are ignored so downstream git commands can report invalid refs.
    """
    if not local_branch_exists(cwd, branch):
        return True
    if branch_has_upstream(cwd, branch):
        return True
    if not interactive:
        raise missing_upstream_error(branch)
    if ensure_branch_upstream_interactive(cwd, branch, settings=settings):
        return True
    if not prompt_toolkit_can_run():
        raise missing_upstream_error(branch)
    return False


def ask_for_needed_upstream(error: NeedsUpstreamError, asked: set[str]) -> bool | None:
    """Ask for the upstream *error* lacks: ``True`` once set, ``False`` when declined, ``None`` when no one can ask.

    Asks once per branch (*asked* collects them), for an existing local branch still without an
    upstream, when the error allows it and a prompt can run. The caller decides whether the
    command may ask at all (``cli_common.run_cli_command``).
    """
    branch = error.branch
    if not error.askable or branch in asked or not prompt_toolkit_can_run():
        return None
    cwd = Path.cwd()
    if not local_branch_exists(cwd, branch) or branch_has_upstream(cwd, branch):
        return None
    asked.add(branch)
    sys.stdout.flush()  # What the command showed before it stopped comes before the question.
    return ask_branch_upstream(cwd, branch, settings=Settings.from_cwd(cwd))
