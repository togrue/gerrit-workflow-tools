"""The ger config screen: sectioned, editable from the start, no write yet."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import RadioList, TextArea

from gerrit_workflow_tools.cli_config import ConfigScreen, build_config_screen
from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.core.config import EDIT_NEXT_SHOW_KEY, Settings
from gerrit_workflow_tools.core.config_form import resolve_config_form
from tests.conftest import run_cli


def _screen(settings: Settings, *, branch: str | None) -> ConfigScreen:
    return build_config_screen(
        resolve_config_form(settings, branch=branch),
        input=DummyInput(),
        output=DummyOutput(),
    )


def test_screen_sections_and_editors_start_editable() -> None:
    settings = Settings.from_map({EDIT_NEXT_SHOW_KEY: "yes", "gerrit.remote": "gerrit"})
    screen = _screen(settings, branch="feature")
    assert screen.section_titles[:3] == ("General", "ger show", "ger log")
    assert screen.section_titles[-1] == "ger edit"

    by_key = {field.resolved.spec.key: field for field in screen.fields}
    remote = by_key["gerrit.remote"]
    assert isinstance(remote.editor, TextArea)
    assert remote.editor.text == "gerrit"
    assert remote.editor.read_only is False

    edit_next = by_key[EDIT_NEXT_SHOW_KEY]
    assert edit_next.resolved.spec.section == "ger edit"
    assert isinstance(edit_next.editor, RadioList)
    assert edit_next.editor.current_value == "true"
    assert edit_next.resolved.value == "true"
    assert edit_next.resolved.stored == "yes"

    assert all(field.editor.read_only is False for field in screen.fields if isinstance(field.editor, TextArea))
    assert screen.application.layout is not None


def test_edit_next_show_row_shows_default_when_unset() -> None:
    screen = _screen(Settings.from_map({}), branch=None)
    field = next(field for field in screen.fields if field.resolved.spec.key == EDIT_NEXT_SHOW_KEY)
    assert isinstance(field.editor, RadioList)
    assert field.editor.current_value == "false"
    assert field.resolved.stored is None
    from gerrit_workflow_tools.core.config_form import format_setting_detail

    detail = format_setting_detail(field.resolved)
    assert "default: false" in detail
    assert "allowed: true, false" in detail
    assert "ger show" in detail


def test_ger_config_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, ger_main, ["config", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger config" in out
    assert "default" in out


def test_ger_config_refuses_a_non_tty(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, ger_main, ["config"], monkeypatch)
    assert code == 1
    assert "interactive terminal" in err
