"""Generate bash completion for ``ger`` from live argparse definitions."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field

from gerrit_workflow_tools import (
    cli_bash_completion,
    cli_cache,
    cli_changeid,
    cli_edit,
    cli_fetch_api,
    cli_fix,
    cli_log,
    cli_push,
    cli_rebase,
    cli_sha,
    cli_show,
)
from gerrit_workflow_tools.cli_ger import _ALIASES, _COMMANDS

_NO_REF_FALLBACK_COMMANDS = {"bash-completion", "fetch-api"}


@dataclass
class CompletionSpec:
    """Completion metadata derived from an argparse parser."""

    flags: list[str] = field(default_factory=list)
    prev_choices: dict[str, list[str]] = field(default_factory=dict)
    has_ref_fallback: bool = False
    subcommands: list[str] = field(default_factory=list)
    subcommand_specs: dict[str, CompletionSpec] = field(default_factory=dict)


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0


def _has_positional_args(parser: argparse.ArgumentParser) -> bool:
    return any(not action.option_strings and action.dest != argparse.SUPPRESS for action in parser._actions)


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _collect_flags_and_choices(parser: argparse.ArgumentParser) -> tuple[list[str], dict[str, list[str]]]:
    flags: list[str] = []
    prev_choices: dict[str, list[str]] = {}
    for action in parser._actions:
        if not action.option_strings:
            continue
        _append_unique(flags, list(action.option_strings))
        if action.choices is None or not _takes_value(action):
            continue
        choices = [str(choice) for choice in action.choices]
        for option in action.option_strings:
            prev_choices[option] = choices
    return flags, prev_choices


def _spec_from_parser(parser: argparse.ArgumentParser, *, command_name: str) -> CompletionSpec:
    flags, prev_choices = _collect_flags_and_choices(parser)
    spec = CompletionSpec(
        flags=flags,
        prev_choices=prev_choices,
        has_ref_fallback=_has_positional_args(parser) and command_name not in _NO_REF_FALLBACK_COMMANDS,
    )
    sub = _subparsers_action(parser)
    if sub is None:
        return spec
    spec.subcommands = sorted(sub.choices.keys())
    for sub_name in spec.subcommands:
        child = sub.choices[sub_name]
        child_flags, child_choices = _collect_flags_and_choices(child)
        spec.subcommand_specs[sub_name] = CompletionSpec(flags=child_flags, prev_choices=child_choices)
    return spec


def _parser_builders() -> dict[str, Callable[[], argparse.ArgumentParser]]:
    return {
        "bash-completion": cli_bash_completion._build_parser,
        "cache": cli_cache._build_parser,
        "change-id": cli_changeid._build_parser,
        "edit": cli_edit._build_parser_edit,
        "fetch-api": cli_fetch_api._build_parser,
        "fix": cli_fix._build_parser,
        "log": cli_log._build_parser,
        "push": cli_push._build_arg_parser,
        "rebase": cli_rebase._build_parser,
        "reword": cli_edit._build_parser_reword,
        "sha": cli_sha._build_parser,
        "show": cli_show._build_parser,
    }


def _shell_function_name(command_name: str) -> str:
    return f"_ger_{command_name.replace('-', '_')}"


def _fmt_flags(values: list[str]) -> str:
    return " ".join(values) if values else "--help"


def _render_choice_case(prev_choices: dict[str, list[str]], *, indent: str) -> list[str]:
    if not prev_choices:
        return []
    lines = [f'{indent}case "$prev" in']
    for option in sorted(prev_choices):
        choices = " ".join(prev_choices[option])
        lines.append(f"{indent}    {option})")
        lines.append(f'{indent}        __gwt_flags "$cur" {choices}')
        lines.append(f"{indent}        return")
        lines.append(f"{indent}        ;;")
    lines.append(f"{indent}esac")
    return lines


def _render_simple_command(command_name: str, spec: CompletionSpec) -> list[str]:
    fn = _shell_function_name(command_name)
    lines = [f"{fn}() {{", '    local cur="${COMP_WORDS[COMP_CWORD]}"', '    local prev="${COMP_WORDS[COMP_CWORD-1]}"']
    lines.append('    if [[ "$cur" == -* ]]; then')
    lines.append(f'        __gwt_flags "$cur" {_fmt_flags(spec.flags)}')
    lines.append("        return")
    lines.append("    fi")
    lines.extend(_render_choice_case(spec.prev_choices, indent="    "))
    if spec.has_ref_fallback:
        lines.extend(
            [
                "    if declare -F __git_complete_refs >/dev/null 2>&1; then",
                "        __git_complete_refs",
                "    fi",
            ]
        )
    lines.append("}")
    return lines


def _render_subcommand_command(command_name: str, spec: CompletionSpec) -> list[str]:
    fn = _shell_function_name(command_name)
    lines = [
        f"{fn}() {{",
        '    local cur="${COMP_WORDS[COMP_CWORD]}"',
        '    local prev="${COMP_WORDS[COMP_CWORD-1]}"',
        '    if [ "${COMP_CWORD:-0}" -eq 2 ]; then',
        '        if [[ "$cur" == -* ]]; then',
        f'            __gwt_flags "$cur" {_fmt_flags(spec.flags)}',
        "        else",
        f'            __gwt_flags "$cur" {" ".join(spec.subcommands)}',
        "        fi",
        "        return",
        "    fi",
        '    local sub="${COMP_WORDS[2]}"',
    ]
    lines.extend(_render_choice_case(spec.prev_choices, indent="    "))
    lines.append('    if [[ "$cur" == -* ]]; then')
    lines.append('        case "$sub" in')
    for sub_name in spec.subcommands:
        child = spec.subcommand_specs[sub_name]
        lines.append(f"            {sub_name})")
        lines.append(f'                __gwt_flags "$cur" {_fmt_flags(child.flags)}')
        lines.append("                ;;")
    lines.append("        esac")
    lines.append("        return")
    lines.append("    fi")
    lines.append('    case "$sub" in')
    for sub_name in spec.subcommands:
        child = spec.subcommand_specs[sub_name]
        choice_lines = _render_choice_case(child.prev_choices, indent="        ")
        if not choice_lines:
            continue
        lines.append(f"        {sub_name})")
        lines.extend(choice_lines)
        lines.append("            ;;")
    lines.append("    esac")
    lines.append("}")
    return lines


def _render_command(command_name: str, spec: CompletionSpec) -> list[str]:
    if spec.subcommands:
        return _render_subcommand_command(command_name, spec)
    return _render_simple_command(command_name, spec)


def _render_top_level_dispatch() -> list[str]:
    names = sorted(list(_COMMANDS) + list(_ALIASES))
    lines = [
        "_ger() {",
        '    local cur="${COMP_WORDS[COMP_CWORD]}"',
        '    if [ "${COMP_CWORD:-0}" -eq 1 ]; then',
        f'        __gwt_flags "$cur" {" ".join(names)}',
        "        return",
        "    fi",
        '    local sub="${COMP_WORDS[1]}"',
        '    case "$sub" in',
    ]
    reverse_aliases: dict[str, list[str]] = {}
    for alias, canonical in _ALIASES.items():
        reverse_aliases.setdefault(canonical, []).append(alias)
    for command_name in sorted(_COMMANDS):
        patterns = [command_name, *sorted(reverse_aliases.get(command_name, []))]
        fn = _shell_function_name(command_name)
        lines.append(f"        {'|'.join(patterns)}) {fn} ;;")
    lines.extend(["    esac", "}", "", "complete -o default -F _ger ger"])
    return lines


def render_bash_completion_script() -> str:
    """Render ``ger`` bash completion from argparse parsers."""
    header = [
        "# Bash completion for the unified `ger` CLI (ger push, ger log, …).",
        "# shellcheck disable=SC2207,SC2154",
        "#",
        "# Install: source this file from ~/.bashrc, or see docu/Completion.md",
        "#",
        "# Optional: Git's bash completion for __git_complete_refs on revision arguments.",
        "",
        "__gwt_flags() {",
        "    local cur=$1",
        "    shift",
        '    COMPREPLY=( $(compgen -W "$*" -- "$cur") )',
        "}",
        "",
    ]
    body: list[str] = []
    for command_name, parser_builder in sorted(_parser_builders().items()):
        parser = parser_builder()
        spec = _spec_from_parser(parser, command_name=command_name)
        body.extend(_render_command(command_name, spec))
        body.append("")
    lines = header + body + _render_top_level_dispatch()
    return "\n".join(lines).rstrip() + "\n"
