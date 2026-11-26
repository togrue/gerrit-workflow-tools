"""``ger config`` — edit ger git config settings via a temporary key=value file."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from gerrit_workflow_tools.cli_args import add_color_args, add_verbose_and_debug_log_args
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_editor_file import (
    ParseError,
    parse_config_editor_text,
    planned_editor_writes,
    render_config_editor_text,
)
from gerrit_workflow_tools.core.config_form import resolve_config_form
from gerrit_workflow_tools.core.config_write import apply_config_write, migrate_legacy_keys, scope_unscoped_secrets
from gerrit_workflow_tools.core.git_editor import run_git_editor
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger config``."""
    parser = argparse.ArgumentParser(
        prog="ger config",
        description=(
            "Open ger settings in your git editor as key=value pairs. "
            "Uncomment a line to set it (local); comment it out to unset. "
            "Delete all content and save to abort."
        ),
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Move every old key name (gerrit.*, inbox.*, branch.<name>.gerrit*) to its ger.* name, "
        "and an unscoped ger.token or ger.password to the host ger.webUrl names (ger.<webUrl>.token), "
        "in local and global config, and exit. Needs no terminal.",
    )
    add_color_args(parser)
    add_verbose_and_debug_log_args(parser, refresh=False)
    return parser


def _stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    return bool(isatty())


def _format_parse_errors(errors: tuple[ParseError, ...]) -> str:
    parts: list[str] = []
    for err in errors:
        if err.line is None:
            parts.append(f"error: {err.message}")
        else:
            parts.append(f"error: line {err.line}: {err.message}")
    return "\n".join(parts)


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    run = init_cli_runtime(args, repository=False)
    cwd, settings = run.cwd, run.settings
    if args.migrate:
        moved = migrate_legacy_keys(cwd)
        web_url = Settings.from_cwd(cwd).gerrit_web_url  # read after the move: no old name answers it now
        # Old names first: a gerrit.token is ger.token by now, and moves on to its host with the others.
        moved += scope_unscoped_secrets(cwd, web_url) if web_url else []
        for key in moved:
            how = "removed; the new name was set already" if key.dropped else f"-> {key.new}"
            print(f"{key.old} {how} ({key.scope.value})")
        print(
            f"moved {counted(len(moved), 'key')}"
            if moved
            else "nothing to migrate: no old key names, no unscoped secret"
        )
        return int(ExitCode.OK)
    if not _stream_is_tty(sys.stdin) or not _stream_is_tty(sys.stdout):
        raise UsageError("ger config requires an interactive terminal")

    branch = run.repo.working_branch
    sections = resolve_config_form(settings, branch=branch)
    text = render_config_editor_text(sections)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".gerconfig",
        prefix="ger-config-",
        delete=False,
    ) as handle:
        path = Path(handle.name)
        handle.write(text)

    try:
        try:
            editor_rc = run_git_editor(path, cwd, settings=settings)
        except FileNotFoundError as exc:
            print(f"error: editor not found: {exc}", file=sys.stderr)
            return int(ExitCode.GIT)
        if editor_rc != 0:
            print(f"error: editor exited with status {editor_rc}", file=sys.stderr)
            return int(ExitCode.REFUSED)

        edited = path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)

    if not edited.strip():
        print("Aborted: empty config file.", file=sys.stderr)
        return int(ExitCode.REFUSED)

    parsed, parse_errors = parse_config_editor_text(edited, sections)
    if parse_errors:
        print(_format_parse_errors(parse_errors), file=sys.stderr)
        return int(ExitCode.USAGE)

    writes, write_errors = planned_editor_writes(sections, parsed)
    if write_errors:
        print(_format_parse_errors(write_errors), file=sys.stderr)
        return int(ExitCode.USAGE)

    if not writes:
        return int(ExitCode.OK)

    for write in writes:
        apply_config_write(cwd, write)
        if write.action == "unset":
            print(f"unset {write.key} ({write.scope.value})")
        else:
            print(f"set {write.key}={write.value} ({write.scope.value})")
    return int(ExitCode.OK)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger config``."""
    return run_cli_command(lambda: _run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
