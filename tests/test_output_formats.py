# Spec: docu/SPEC.md § Output formats

"""Agreement between commands on output formats: every command with ``--json`` takes ``--ai``.

Both come from one registration, ``cli_common.add_output_format_args``, so ``--json``, ``--format``
and ``--ai`` exclude each other and mean the same thing on every command. A command that grew its
own ``--json`` beside the shared group would pass a per-command test and drift; this one fails it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gerrit_workflow_tools.bash_completion_generator import _parser_builders
from gerrit_workflow_tools.cli_common import output_format
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.cli_sha import main as sha_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.render.style import is_color_enabled, is_hyperlink_enabled, strip_ansi
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


# Commands whose --ai is a Markdown view of their own (``--format markdown`` too).
MARKDOWN_VIEWS = {"ci", "show"}

# What --format's help says 'ai' is: a view of the command's own, else its human output, uncoloured.
AI_VIEWS = {"ci": "the Markdown view", "show": "the Markdown view", "log": "one plain line per commit"}
AI_VIEWS["inbox"] = "one plain line per chain"
HUMAN_WITHOUT_COLOUR = "the human output without colour or hyperlinks"

PARSERS = {name: build() for name, build in sorted(_parser_builders().items())}


def _option(parser: argparse.ArgumentParser, flag: str) -> argparse.Action | None:
    return next((a for a in parser._actions if flag in a.option_strings), None)


def _group_of(parser: argparse.ArgumentParser, action: argparse.Action) -> argparse._MutuallyExclusiveGroup | None:
    return next((g for g in parser._mutually_exclusive_groups if action in g._group_actions), None)


def test_every_command_with_json_takes_ai_from_the_shared_group() -> None:
    with_json = [name for name, parser in PARSERS.items() if _option(parser, "--json")]
    assert {"log", "inbox", "show", "ci", "sha", "comment", "fix", "resolve", "review", "reply"} <= set(with_json)
    bad: list[str] = []
    for name in with_json:
        parser = PARSERS[name]
        json_, fmt, ai = (_option(parser, flag) for flag in ("--json", "--format", "--ai"))
        if fmt is None or ai is None:
            bad.append(f"ger {name}: --json without --format/--ai")
            continue
        group = _group_of(parser, json_)  # type: ignore[arg-type]
        if group is None or fmt not in group._group_actions or ai not in group._group_actions:
            bad.append(f"ger {name}: --json, --format and --ai are not one mutually exclusive group")
        markdown = name in MARKDOWN_VIEWS
        expected = ["human", "markdown", "ai"] if markdown else ["human", "ai"]
        if list(fmt.choices or ()) != expected:
            bad.append(f"ger {name}: --format offers {fmt.choices}, expected {expected}")
        ai_view = "markdown" if markdown else "ai"
        if parser.get_default("ai_view_") != ai_view:
            bad.append(f"ger {name}: --ai is not its {ai_view} view")
        said = AI_VIEWS.get(name, HUMAN_WITHOUT_COLOUR)
        if f"'ai': {said}," not in (fmt.help or ""):
            bad.append(f"ger {name}: --format help does not say 'ai' is {said!r}: {fmt.help!r}")
    assert not bad, "Register output formats with cli_common.add_output_format_args:\n" + "\n".join(bad)


def test_no_command_takes_ai_without_json() -> None:
    """--ai is a choice of output format, offered only where there is a choice (beside --json)."""
    stray = [name for name, parser in PARSERS.items() if _option(parser, "--ai") and not _option(parser, "--json")]
    assert not stray


@pytest.mark.parametrize(
    ("command", "argv", "expected"),
    [
        ("log", [], "human"),
        ("log", ["--json"], "json"),
        ("log", ["--ai"], "ai"),
        ("log", ["--format", "ai"], "ai"),
        ("show", ["--ai"], "markdown"),
        ("show", ["--format", "ai"], "markdown"),
        ("show", ["--format", "markdown"], "markdown"),
    ],
)
def test_output_format_reads_back_what_was_asked(command: str, argv: list[str], expected: str) -> None:
    assert output_format(PARSERS[command].parse_args(argv)) == expected


@pytest.mark.parametrize("argv", [["--json", "--ai"], ["--ai", "--format", "human"], ["--format", "markdown"]])
def test_log_refuses_mixed_or_foreign_formats(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        PARSERS["log"].parse_args(argv)


def test_short_output_with_ai_is_the_human_text_without_colour(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command with no compact view of its own prints its human output under --ai, colour forced off."""
    store = ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(stack_repo)))
    always = ["--color", "always", "--hyperlinks", "always"]
    code, ai, err = run_cli(stack_repo, resolve_main, ["--ai", *always, "HEAD"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert not is_color_enabled() and not is_hyperlink_enabled()
    _code, human, _err = run_cli(stack_repo, resolve_main, ["--color", "never", "HEAD"], monkeypatch, gerrit=store)
    assert ai == human
    assert "Gerrit change: c103" in ai


def test_show_ai_turns_colour_off_on_stderr_too(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--ai maps to Markdown on show, and still means no colour and no links anywhere, stderr included."""
    store = ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(stack_repo)))
    always = ["--color", "always", "--hyperlinks", "always"]
    code, out, err = run_cli(stack_repo, show_main, ["--ai", *always, "HEAD"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert not is_color_enabled() and not is_hyperlink_enabled()
    assert (out, err) == (strip_ansi(out), strip_ansi(err))
    run_cli(stack_repo, show_main, ["--format", "markdown", *always, "HEAD"], monkeypatch, gerrit=store)
    assert is_color_enabled()  # --format markdown asked for nothing but Markdown


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (["--ai"], "sha"),
        (["--short", "--ai"], "short"),
        (["--subject", "--ai", "--color", "always"], "subject"),
        (["--short", "--format", "ai"], "short"),
    ],
)
def test_sha_ai_combines_with_short_and_subject(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], line: str
) -> None:
    tip = stack_rows_mb_to_head(stack_repo)[-1]
    assert tip.change_id
    code, out, err = run_cli(stack_repo, sha_main, [*argv, tip.change_id], monkeypatch)
    assert code == 0, err
    expected = {"sha": tip.sha, "short": tip.short_sha, "subject": f"{tip.short_sha} {tip.subject}"}[line]
    assert out == f"{expected}\n"


@pytest.mark.parametrize(
    "argv", [["--short", "--json"], ["--subject", "--json"], ["--json", "--ai"], ["--short", "--subject"]]
)
def test_sha_refuses_what_does_not_combine(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    tip = stack_rows_mb_to_head(stack_repo)[-1]
    assert tip.change_id
    code, _out, _err = run_cli(stack_repo, sha_main, [*argv, tip.change_id], monkeypatch, catch_sys_exit=True)
    assert code == 2
