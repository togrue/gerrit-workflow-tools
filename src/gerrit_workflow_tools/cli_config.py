"""``ger config`` — full-screen editor for git config settings, grouped by command.

Every field is editable when the screen opens. There is no read-only mode. Values still
at their default are filled in from that default when git config has no entry. This
screen does not write git config: save-on-blur and batch confirm are not implemented.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.output import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, RadioList, TextArea

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.core.config_form import (
    ConfigSection,
    ResolvedSetting,
    SettingKind,
    format_setting_detail,
    resolve_config_form,
)
from gerrit_workflow_tools.core.git_state import resolve_working_branch


_STATUS = "Edits stay on this screen. Git config is not written yet. Ctrl-C exits."

_STYLE = Style.from_dict(
    {
        "setting-key": "bold",
        "setting-meta": "#888888",
        "frame.label": "bold",
        "status-bar": "reverse",
    }
)


@dataclass(frozen=True)
class ConfigField:
    """One on-screen editor and the resolved setting it shows."""

    resolved: ResolvedSetting
    editor: TextArea | RadioList


@dataclass(frozen=True)
class ConfigScreen:
    """The prompt_toolkit screen for one resolved config form."""

    application: Application
    section_titles: tuple[str, ...]
    fields: tuple[ConfigField, ...]


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger config``."""
    parser = argparse.ArgumentParser(
        prog="ger config",
        description=(
            "Show ger settings in sections, one per command, with every field editable. "
            "A setting left at its default is shown even when git config has no value for it."
        ),
    )
    add_color_args(parser)
    add_verbose_and_debug_log_args(parser, debug_log_help="Log git config reads to stderr.")
    return parser


def _choice_pairs(resolved: ResolvedSetting) -> list[tuple[str, str]]:
    choices = list(resolved.spec.choices)
    if resolved.value not in choices:
        choices.insert(0, resolved.value)
    return [(choice, choice) for choice in choices]


def _editor(resolved: ResolvedSetting) -> TextArea | RadioList:
    """An editable control. Nothing on this screen is read-only."""
    if resolved.spec.kind in (SettingKind.BOOL, SettingKind.CHOICE):
        return RadioList(_choice_pairs(resolved), default=resolved.value, show_scrollbar=False)
    return TextArea(
        text=resolved.value,
        height=1,
        multiline=False,
        wrap_lines=False,
        read_only=False,
        focusable=True,
    )


def _setting_block(field: ConfigField) -> HSplit:
    detail = format_setting_detail(field.resolved)
    return HSplit(
        [
            Window(
                FormattedTextControl(field.resolved.spec.key),
                height=1,
                dont_extend_height=True,
                style="class:setting-key",
            ),
            field.editor,
            Window(
                FormattedTextControl(detail),
                height=Dimension(min=detail.count("\n") + 1, max=detail.count("\n") + 3),
                dont_extend_height=True,
                wrap_lines=True,
                style="class:setting-meta",
            ),
        ]
    )


def build_config_screen(
    sections: tuple[ConfigSection, ...],
    *,
    input: Input | None = None,
    output: Output | None = None,
) -> ConfigScreen:
    """Build the sectioned prompt_toolkit layout. Does not read or write git config.

    Pass ``input`` and ``output`` to build the screen without a real console (tests).
    """
    fields: list[ConfigField] = []
    frames: list[Frame] = []
    for section in sections:
        blocks: list[HSplit] = []
        for resolved in section.settings:
            field = ConfigField(resolved, _editor(resolved))
            fields.append(field)
            blocks.append(_setting_block(field))
        frames.append(Frame(HSplit(blocks, padding=1), title=section.title))

    bindings = KeyBindings()

    @bindings.add("c-c", eager=True)
    def _quit(event) -> None:
        event.app.exit(result=0)

    body = HSplit(
        [
            ScrollablePane(HSplit(frames, padding=1)),
            Window(
                FormattedTextControl(_STATUS),
                height=1,
                dont_extend_height=True,
                style="class:status-bar",
            ),
        ]
    )
    application: Application = Application(
        layout=Layout(body, focused_element=fields[0].editor),
        key_bindings=bindings,
        style=_STYLE,
        full_screen=True,
        mouse_support=False,
        erase_when_done=True,
        input=input,
        output=output,
    )
    return ConfigScreen(application, tuple(section.title for section in sections), tuple(fields))


def _stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    return bool(isatty())


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(
        debug_log=args.debug_log,
        color=args.color,
        hyperlinks=args.hyperlinks,
    )
    if not _stream_is_tty(sys.stdin) or not _stream_is_tty(sys.stdout):
        print("error: ger config requires an interactive terminal.", file=sys.stderr)
        return int(ExitCode.ATTENTION)

    branch = resolve_working_branch(cwd, settings=settings)
    screen = build_config_screen(resolve_config_form(settings, branch=branch))
    code = screen.application.run()
    return int(code) if isinstance(code, int) else int(ExitCode.OK)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger config``."""
    return run_cli_command(lambda: _run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
