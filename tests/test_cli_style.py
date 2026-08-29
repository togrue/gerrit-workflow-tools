"""Tests for OSC 8 hyperlinks and ANSI stripping in ``cli_style``."""

from __future__ import annotations

import pytest

from gerrit_workflow_tools.cli_style import (
    ANSI_DIM,
    GERRIT_LINK_LABEL,
    color_text,
    format_link,
    init_hyperlink_mode,
    set_color_mode,
    set_hyperlink_mode,
    strip_ansi,
    terminal_supports_hyperlinks,
    visible_len,
)


_HYPERLINK_ENV_KEYS = (
    "WT_SESSION",
    "TERM_PROGRAM",
    "VTE_VERSION",
    "KITTY_WINDOW_ID",
    "ALACRITTY_SOCKET",
    "KONSOLE_VERSION",
    "DOMTERM",
    "TERM",
    "FORCE_HYPERLINK",
    "NO_HYPERLINKS",
)


def _clear_hyperlink_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _HYPERLINK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"WT_SESSION": "1"}, True),
        ({"TERM_PROGRAM": "iTerm.app"}, True),
        ({"TERM_PROGRAM": "WezTerm"}, True),
        ({"TERM_PROGRAM": "ghostty"}, True),
        ({"TERM_PROGRAM": "vscode"}, True),
        ({"VTE_VERSION": "5000"}, True),
        ({"VTE_VERSION": "6800"}, True),
        ({"VTE_VERSION": "4999"}, False),
        ({"KITTY_WINDOW_ID": "1"}, True),
        ({"TERM": "xterm-kitty"}, True),
        ({"ALACRITTY_SOCKET": "/tmp/a"}, True),
        ({"TERM": "alacritty"}, True),
        ({"KONSOLE_VERSION": "220400"}, True),
        ({"DOMTERM": "1"}, True),
        ({"TERM": "dumb"}, False),
        ({"TERM": "dumb", "WT_SESSION": "1"}, False),
        ({"TERM": "xterm-256color"}, False),
        ({}, False),
    ],
)
def test_terminal_supports_hyperlinks(environ: dict[str, str], expected: bool) -> None:
    assert terminal_supports_hyperlinks(environ) is expected


def test_format_link_off_returns_raw_url() -> None:
    set_hyperlink_mode(False)
    url = "https://g.example/c/testproj/+/42"
    assert format_link(url) == url


def test_format_link_on_wraps_label() -> None:
    set_hyperlink_mode(True)
    url = "https://g.example/c/testproj/+/42"
    wrapped = format_link(url)
    assert wrapped.startswith(f"\x1b]8;;{url}\x1b\\")
    assert wrapped.endswith("\x1b]8;;\x1b\\")
    assert GERRIT_LINK_LABEL in wrapped
    assert strip_ansi(wrapped) == GERRIT_LINK_LABEL
    assert visible_len(wrapped) == len(GERRIT_LINK_LABEL)


def test_format_link_empty_url() -> None:
    set_hyperlink_mode(True)
    assert format_link("") == ""


def test_format_link_rejects_osc_in_url() -> None:
    set_hyperlink_mode(True)
    url = "https://g.example/c/1\x1b]8;;evil"
    assert format_link(url) == url


def test_visible_len_strips_osc8_and_sgr() -> None:
    set_hyperlink_mode(True)
    set_color_mode(True)
    try:
        url = "https://g.example/c/testproj/+/42"
        text = color_text(format_link(url), ANSI_DIM)
        assert visible_len(text) == len(GERRIT_LINK_LABEL)
        assert "https://" not in strip_ansi(text)
    finally:
        set_color_mode(False)


def test_init_hyperlink_mode_always_and_never(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: False)
    assert init_hyperlink_mode(hyperlinks="always") is True
    assert init_hyperlink_mode(hyperlinks="never") is False


def test_init_hyperlink_mode_auto_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: False)
    assert init_hyperlink_mode(hyperlinks="auto") is False


def test_init_hyperlink_mode_auto_tty_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: True)
    assert init_hyperlink_mode(hyperlinks="auto") is True


def test_init_hyperlink_mode_auto_tty_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: True)
    assert init_hyperlink_mode(hyperlinks="auto") is False


def test_init_hyperlink_mode_force_hyperlink(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: True)
    assert init_hyperlink_mode(hyperlinks="auto") is True
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: False)
    assert init_hyperlink_mode(hyperlinks="auto") is False


def test_init_hyperlink_mode_no_hyperlinks(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setenv("NO_HYPERLINKS", "1")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: True)
    assert init_hyperlink_mode(hyperlinks="auto") is False


def test_init_hyperlink_mode_force_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hyperlink_env(monkeypatch)
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setenv("FORCE_HYPERLINK", "0")
    monkeypatch.setattr("gerrit_workflow_tools.cli_style._stdout_is_tty", lambda: True)
    assert init_hyperlink_mode(hyperlinks="auto") is False
