"""CLI parity guardrails: what the extension and the docs promise, the CLI must do.

- ``docu/flows.md § Extension command parity`` lists exactly the commands in
  ``vscode/package.json``, each with its CLI equivalent, *UI only*, or a **gap**.
- Every ``ger <command> --flag`` named in ``docu/flows.md`` exists: the command is registered
  and its ``--help`` lists the flag.
- Every flag in an options table of a command spec (``docu/spec/commands/``), and every flag
  in a ``ger <command> ...`` example in the docs, is in that command's ``--help``.
- ``docu/spec/commands/serve.md`` documents exactly the routes ``ger serve`` serves.

The gaps and the UI-only commands are ratchets, like the known exceptions in
``tests/test_architecture.py``: the lists only shrink. Closing a gap means deleting its entry
here and naming the command in the table. Adding one is a design decision for the human.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
from functools import cache
from pathlib import Path

from gerrit_workflow_tools.cli_ger import _COMMANDS


REPO = Path(__file__).resolve().parents[1]
FLOWS = REPO / "docu" / "flows.md"
SPECS = REPO / "docu" / "spec" / "commands"

# Extension commands with no CLI equivalent yet. Each names the work that closes it in flows.md.
KNOWN_GAPS = frozenset(
    {
        "ger.markSeen",
        "ger.stack.moveUp",
        "ger.stack.moveDown",
        "ger.stack.moveUpBlocked",
        "ger.stack.moveDownBlocked",
        "ger.stack.squash",
        "ger.stack.squashTakeMessage",
        "ger.agentWalk",
        "ger.hold",
        "ger.release",
    }
)

# Extension commands that only make sense in an editor.
UI_ONLY = frozenset({"ger.showLog", "ger.openDiff", "ger.openLocation", "ger.nextComment", "ger.prevComment"})

# Spec files that do not describe exactly the command of their name.
_SPEC_COMMANDS = {"sha-change-id": ("sha", "change-id"), "edit": ("edit", "reword")}

_GER_SPAN = re.compile(r"`ger ([a-z][a-z-]*)([^`]*)`")
_FLAG = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")


@cache
def _help(command: str) -> str:
    module_name, func_name = _COMMANDS[command][1].split(":")
    main = getattr(importlib.import_module(module_name), func_name)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main(["--help"])
    return out.getvalue()


def _has_flag(command: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", _help(command)) is not None


def _parity_rows() -> dict[str, str]:
    text = FLOWS.read_text(encoding="utf-8")
    m = re.search(r"^## Extension command parity\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, "docu/flows.md has no '## Extension command parity' section"
    return {cmd: cli.strip() for cmd, cli in re.findall(r"^\|\s*`(ger\.[\w.]+)`\s*\|([^|]*)\|", m.group(1), re.M)}


def _extension_commands() -> set[str]:
    package = json.loads((REPO / "vscode" / "package.json").read_text(encoding="utf-8"))
    return {c["command"] for c in package["contributes"]["commands"]}


def _bad_ger_spans(text: str, where: str) -> list[str]:
    bad: list[str] = []
    for command, rest in _GER_SPAN.findall(text):
        if command not in _COMMANDS:
            bad.append(f"{where}: `ger {command}` is not a registered command")
            continue
        bad += [
            f"{where}: `ger {command}` has no {flag}" for flag in _FLAG.findall(rest) if not _has_flag(command, flag)
        ]
    return bad


def test_parity_table_lists_every_extension_command() -> None:
    listed, actual = set(_parity_rows()), _extension_commands()
    assert listed == actual, (
        "docu/flows.md § Extension command parity must list every command in vscode/package.json.\n"
        f"  in package.json, not listed: {sorted(actual - listed)}\n"
        f"  listed, not in package.json: {sorted(listed - actual)}"
    )


def test_parity_gaps_only_shrink() -> None:
    rows = _parity_rows()
    gaps = {cmd for cmd, cli in rows.items() if "gap" in cli.lower()}
    ui_only = {cmd for cmd, cli in rows.items() if cli.lower().startswith("ui only")}
    no_cli = {cmd for cmd, cli in rows.items() if not _GER_SPAN.search(cli)} - gaps - ui_only
    assert not no_cli, f"Give these a `ger ...` equivalent, or mark them **gap** or UI only: {sorted(no_cli)}"
    assert gaps <= KNOWN_GAPS, (
        f"New extension commands without a CLI equivalent: {sorted(gaps - KNOWN_GAPS)}. "
        "Build the CLI side first (agents use the CLI), or propose the gap to the human."
    )
    assert gaps >= KNOWN_GAPS, f"Gap closed, delete it from KNOWN_GAPS: {sorted(KNOWN_GAPS - gaps)}"
    assert ui_only == UI_ONLY, f"UI-only commands changed: now {sorted(ui_only)}, pinned {sorted(UI_ONLY)}"


def test_flows_name_real_commands_and_flags() -> None:
    bad = _bad_ger_spans(FLOWS.read_text(encoding="utf-8"), "docu/flows.md")
    assert not bad, "docu/flows.md names commands or flags that do not exist:\n" + "\n".join(bad)


def test_spec_option_tables_match_help() -> None:
    bad: list[str] = []
    for spec in sorted(SPECS.glob("*.md")):
        commands = [c for c in _SPEC_COMMANDS.get(spec.stem, (spec.stem,)) if c in _COMMANDS]
        if not commands:
            continue  # a planned command
        for cell in re.findall(r"^\|\s*([^|]*?)\s*\|", spec.read_text(encoding="utf-8"), re.M):
            for span in re.findall(r"`([^`]+)`", cell):
                for flag in _FLAG.findall(span) if span.startswith("-") else ():
                    if not any(_has_flag(c, flag) for c in commands):
                        bad.append(f"docu/spec/commands/{spec.name}: {flag} is not in `ger {commands[0]} --help`")
    assert not bad, "Command specs list options the command does not have. Fix the spec, or the parser:\n" + "\n".join(
        sorted(set(bad))
    )


def test_serve_spec_documents_every_route() -> None:
    from gerrit_workflow_tools.server.api import ROUTES

    spec = (SPECS / "serve.md").read_text(encoding="utf-8")
    reads = {path.split("?")[0] for path in re.findall(r"^### `GET (/[^`\s]*)", spec, re.M)}
    actions = set(re.findall(r"^\|\s*`(/[^`]+)`\s*\|", spec, re.M))
    documented = {("GET", re.sub(r"<[^>]+>", "*", p)) for p in reads} | {("POST", p) for p in actions}
    served = {(method, path.replace("([^/]+)", "*")) for method, path, _ in ROUTES}
    assert documented == served, (
        "docu/spec/commands/serve.md must document exactly the routes in server/api.py ROUTES.\n"
        f"  served, not documented: {sorted(served - documented)}\n"
        f"  documented, not served: {sorted(documented - served)}"
    )


_FENCE = re.compile(r"^(```|~~~)[^\n]*\n(.*?)^\1", re.M | re.S)
_EXAMPLE = re.compile(r"^\s*ger ([a-z][a-z-]*)(.*)$")
# What follows on an example line is a description, not arguments.
_COMMENT = re.compile(r"\s(#|->|→|\(TTY)|\s{2,}\S")


def test_doc_examples_use_real_flags() -> None:
    docs = [REPO / "README.md", *sorted((REPO / "docu").glob("*.md")), *sorted((REPO / "docu" / "spec").rglob("*.md"))]
    bad: list[str] = []
    for doc in docs:
        for _, block in _FENCE.findall(doc.read_text(encoding="utf-8")):
            for line in block.splitlines():
                m = _EXAMPLE.match(line)
                if not m or m.group(1) not in _COMMANDS:
                    continue
                args = _COMMENT.split(m.group(2), maxsplit=1)[0]
                bad += [
                    f"{doc.relative_to(REPO).as_posix()}: `ger {m.group(1)}` has no {flag}  ({line.strip()})"
                    for flag in _FLAG.findall(args)
                    if not _has_flag(m.group(1), flag)
                ]
    assert not bad, "Examples in the docs use flags the command does not have:\n" + "\n".join(bad)
