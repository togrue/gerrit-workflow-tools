"""Tests for push-options prompt history and navigation helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools import push_input_prompt as pip


@pytest.fixture
def history_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pip.Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_load_push_options_history_empty(history_home: Path) -> None:
    assert pip.load_push_options_history() == []


def test_prepend_push_options_history_newest_first(history_home: Path) -> None:
    pip.prepend_push_options_history("r=alice")
    pip.prepend_push_options_history("r=bob")
    assert pip.load_push_options_history() == ["r=bob", "r=alice"]


def test_prepend_push_options_history_dedupes(history_home: Path) -> None:
    pip.prepend_push_options_history("r=alice")
    pip.prepend_push_options_history("r=bob")
    pip.prepend_push_options_history("r=alice")
    assert pip.load_push_options_history() == ["r=alice", "r=bob"]


def test_prepend_push_options_history_caps(history_home: Path) -> None:
    for i in range(25):
        pip.prepend_push_options_history(f"r=user{i}")
    loaded = pip.load_push_options_history()
    assert len(loaded) == 20
    assert loaded[0] == "r=user24"


def test_in_memory_history_entries_skips_initial() -> None:
    history = ["r=new", "r=mid", "r=old"]
    assert pip._in_memory_history_entries(history, "r=new") == ["r=old", "r=mid"]
    assert pip._in_memory_history_entries(history, "") == ["r=old", "r=mid", "r=new"]
    assert pip._in_memory_history_entries(history, "r=other") == ["r=old", "r=mid", "r=new"]


def test_should_navigate_requires_cursor_at_end() -> None:
    buffer = MagicMock()
    buffer.complete_state = None
    buffer.document.is_cursor_at_the_end = False
    assert pip.should_navigate_push_options_history(buffer) is False


def test_should_navigate_false_when_completions_active() -> None:
    buffer = MagicMock()
    buffer.complete_state = MagicMock(completions=["one", "two"])
    buffer.document.is_cursor_at_the_end = True
    assert pip.should_navigate_push_options_history(buffer) is False


def test_should_navigate_true_at_end_without_completions() -> None:
    buffer = MagicMock()
    buffer.complete_state = None
    buffer.document.is_cursor_at_the_end = True
    assert pip.should_navigate_push_options_history(buffer) is True


def test_should_navigate_true_when_complete_state_empty() -> None:
    buffer = MagicMock()
    buffer.complete_state = MagicMock(completions=[])
    buffer.document.is_cursor_at_the_end = True
    assert pip.should_navigate_push_options_history(buffer) is True
