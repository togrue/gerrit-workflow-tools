"""Unified CLI entry: ``ger <command>`` dispatches to the workflow tool CLIs."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from gerrit_workflow_tools.cli_style import ensure_utf8_stdio


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.rest import GerritRest


_Handler = Callable[[list[str] | None], int]

# Lazy import paths: only the invoked command module is loaded.
_COMMANDS: dict[str, tuple[str, str]] = {
    "bash-completion": (
        "Print or install bash tab-completion for ger.",
        "gerrit_workflow_tools.cli_bash_completion:main",
    ),
    "ci": (
        "Fetch the CI logs behind failed CI links and list why CI failed.",
        "gerrit_workflow_tools.cli_ci:main",
    ),
    "cache": ("Inspect or clear the local Gerrit API cache.", "gerrit_workflow_tools.cli_cache:main"),
    "comment": (
        "Start a comment chain on a line or range of a change, as a draft.",
        "gerrit_workflow_tools.cli_comment:main",
    ),
    "config": (
        "Full-screen editor for ger settings, grouped by command.",
        "gerrit_workflow_tools.cli_config:main",
    ),
    "change-id": ("Print or validate Change-Ids for commits or ranges.", "gerrit_workflow_tools.cli_changeid:main"),
    "edit": ("Interactive rebase: edit, reword, or drop a stack commit.", "gerrit_workflow_tools.cli_edit:main"),
    "reword": (
        "Interactive rebase: reword, edit, or drop a stack commit.",
        "gerrit_workflow_tools.cli_edit:main_reword",
    ),
    "fetch-api": ("GET a Gerrit REST path with configured user and token.", "gerrit_workflow_tools.cli_fetch_api:main"),
    "fix": ("Create a git fixup commit for a ref or Gerrit change.", "gerrit_workflow_tools.cli_fix:main"),
    "inbox": (
        "Open review chains waiting on you (unreviewed age, last activity, Gerrit link).",
        "gerrit_workflow_tools.cli_inbox:main",
    ),
    "log": (
        "Overview of the local commit chain vs Gerrit (CI, votes, comments).",
        "gerrit_workflow_tools.cli_log:main",
    ),
    "push": ("Push the ready prefix or full stack to Gerrit.", "gerrit_workflow_tools.cli_push:main"),
    "rebase": ("Interactive rebase with Gerrit status annotations.", "gerrit_workflow_tools.cli_rebase:main"),
    "reply": (
        "Answer a comment chain with a draft reply (published with ger push).",
        "gerrit_workflow_tools.cli_reply:main",
    ),
    "review": (
        "Check a change out to review it (diff staged), walk its chain, vote.",
        "gerrit_workflow_tools.cli_review:main",
    ),
    "resolve": (
        "Resolve a changeish to a local commit and/or Gerrit change.",
        "gerrit_workflow_tools.cli_resolve:main",
    ),
    "serve": (
        "Serve review state as JSON over local HTTP, for editor integrations.",
        "gerrit_workflow_tools.cli_serve:main",
    ),
    "setup": (
        "Interactively configure gerrit.webUrl, user, and token in git config.",
        "gerrit_workflow_tools.cli_setup:main",
    ),
    "sha": ("Resolve a Change-Id to a commit SHA.", "gerrit_workflow_tools.cli_sha:main"),
    "show": ("One commit vs Gerrit (status and unresolved comments).", "gerrit_workflow_tools.cli_show:main"),
}

# Alternate spellings; not listed in ``ger --help``.
_ALIASES: dict[str, str] = {
    "changeid": "change-id",
    "restack": "rebase",
    "stack": "rebase",
}

_HANDLER_CACHE: dict[str, _Handler] = {}


def run_show(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Run ``ger show`` for another command, passing the same Gerrit seam through."""
    from gerrit_workflow_tools.cli_show import main as show_main

    return show_main(argv, gerrit=gerrit)


def _load_handler(import_path: str) -> _Handler:
    module_name, _, attr = import_path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"invalid handler import path: {import_path!r}")
    module = importlib.import_module(module_name)
    handler = getattr(module, attr)
    if not callable(handler):
        raise TypeError(f"{import_path} is not callable")
    return handler  # type: ignore[return-value]


def _usage() -> str:
    lines = [
        "usage: ger <command> [options]",
        "",
        "Gerrit workflow tools for stacked reviews.",
        "",
        "commands:",
    ]
    for name, (desc, _) in sorted(_COMMANDS.items()):
        lines.append(f"  {name:12} {desc}")
    lines.extend(
        [
            "",
            "Run ger <command> --help for command-specific options.",
        ]
    )
    return "\n".join(lines)


def _did_you_mean(cmd: str) -> str:
    import difflib

    close = difflib.get_close_matches(cmd, [*_COMMANDS, *_ALIASES], n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger``: dispatch ``ger <command>`` to the matching tool."""
    try:
        # Before any command's argparse: `--help` prints before init_cli_runtime runs.
        ensure_utf8_stdio()
        argv = list(sys.argv[1:] if argv is None else argv)
        refresh = False
        while "--refresh" in argv:
            argv.remove("--refresh")
            refresh = True
        if not argv:
            print(_usage())
            return 2
        if argv[0] in ("-h", "--help"):
            print(_usage())
            return 0
        cmd = _ALIASES.get(argv[0], argv[0])
        if cmd not in _COMMANDS:
            print(f"error: unknown command {cmd!r}{_did_you_mean(cmd)}", file=sys.stderr)
            print("Run `ger --help` for a list of commands.", file=sys.stderr)
            return 2  # ExitCode.USAGE; cli_common is not imported here, to keep dispatch light
        if refresh:
            os.environ["GER_CACHE_REFRESH"] = "1"
        _, import_path = _COMMANDS[cmd]
        handler = _HANDLER_CACHE.get(cmd)
        if handler is None:
            handler = _load_handler(import_path)
            _HANDLER_CACHE[cmd] = handler
        return handler(argv[1:])
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
