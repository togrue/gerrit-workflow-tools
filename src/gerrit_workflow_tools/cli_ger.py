"""Unified CLI entry: ``ger <command>`` dispatches to the workflow tool CLIs."""

from __future__ import annotations

import sys
from collections.abc import Callable

from gerrit_workflow_tools.cli_bash_completion import main as main_bash_completion
from gerrit_workflow_tools.cli_branch import main as main_branch
from gerrit_workflow_tools.cli_cid import main as main_cid
from gerrit_workflow_tools.cli_edit import main as main_edit
from gerrit_workflow_tools.cli_edit import main_reword as main_reword
from gerrit_workflow_tools.cli_fetch_api import main as main_fetch_api
from gerrit_workflow_tools.cli_log import main as main_log
from gerrit_workflow_tools.cli_push import main as main_push
from gerrit_workflow_tools.cli_restack import main as main_restack
from gerrit_workflow_tools.cli_sha import main as main_sha
from gerrit_workflow_tools.cli_show import main as main_show

_Handler = Callable[[list[str] | None], int]

_COMMANDS: dict[str, tuple[str, _Handler]] = {
    "bash-completion": ("Print or install bash tab-completion for ger.", main_bash_completion),
    "branch": ("Branch-local Gerrit target and reviewers.", main_branch),
    "cid": ("Print or validate Change-Ids for commits or ranges.", main_cid),
    "edit": ("Interactive rebase: edit, reword, or drop a stack commit.", main_edit),
    "reword": ("Interactive rebase: reword, edit, or drop a stack commit.", main_reword),
    "fetch-api": ("GET a Gerrit REST path with configured user and token.", main_fetch_api),
    "log": ("Overview of the local commit chain vs Gerrit (CI, votes, comments).", main_log),
    "push": ("Push the ready prefix or full stack to Gerrit.", main_push),
    "restack": ("Interactive rebase with Gerrit status annotations.", main_restack),
    "sha": ("Resolve a Change-Id to a commit SHA.", main_sha),
    "show": ("One commit vs Gerrit (status and unresolved comments).", main_show),
}


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


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger``: dispatch ``ger <command>`` to the matching tool."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(_usage())
        return 2
    if argv[0] in ("-h", "--help"):
        print(_usage())
        return 0
    cmd = argv[0]
    if cmd not in _COMMANDS:
        print(f"ger: unknown command {cmd!r}", file=sys.stderr)
        print("Run `ger --help` for a list of commands.", file=sys.stderr)
        return 1
    return _COMMANDS[cmd][1](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
