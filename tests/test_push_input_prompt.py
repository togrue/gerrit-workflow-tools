"""Tests for push-options prompt history and navigation helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools import push_input_prompt as pip
from gerrit_workflow_tools.core.gerrit.paths import push_options_history_path
from gerrit_workflow_tools.push_input_line import apply_session_strategy, format_canonical, parse


WEB = "https://gerrit.example.com"
PROJECT_A = "group/repo-a"
PROJECT_B = "group/repo-b"


@pytest.fixture
def history_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def test_load_push_options_history_empty(history_cache: Path) -> None:
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == []


def test_load_push_options_history_missing_identity_is_empty(history_cache: Path) -> None:
    assert pip.load_push_options_history(web_base=None, project=PROJECT_A) == []
    assert pip.load_push_options_history(web_base=WEB, project=None) == []


def test_prepend_skips_when_identity_missing(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice", web_base=None, project=PROJECT_A)
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=None)
    assert list(history_cache.rglob("*.txt")) == []


def test_prepend_push_options_history_newest_first(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=PROJECT_A)
    pip.prepend_push_options_history("r=bob", web_base=WEB, project=PROJECT_A)
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == ["r=bob", "r=alice"]


def test_prepend_push_options_history_dedupes(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=PROJECT_A)
    pip.prepend_push_options_history("r=bob", web_base=WEB, project=PROJECT_A)
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=PROJECT_A)
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == ["r=alice", "r=bob"]


def test_prepend_push_options_history_caps(history_cache: Path) -> None:
    for i in range(25):
        pip.prepend_push_options_history(f"r=user{i}", web_base=WEB, project=PROJECT_A)
    loaded = pip.load_push_options_history(web_base=WEB, project=PROJECT_A)
    assert len(loaded) == 20
    assert loaded[0] == "r=user24"


def test_history_is_scoped_per_project(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=PROJECT_A)
    pip.prepend_push_options_history("r=bob", web_base=WEB, project=PROJECT_B)
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == ["r=alice"]
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_B) == ["r=bob"]


def test_history_path_uses_xdg_and_host(history_cache: Path) -> None:
    path = push_options_history_path(WEB, PROJECT_A)
    assert path == history_cache / "ger" / "gerrit.example.com" / "push_options_history" / "group_repo-a.txt"


def test_prepend_strips_strategy_from_history(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice topic=t wip lazy", web_base=WEB, project=PROJECT_A)
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == ["r=alice topic=t wip"]


def test_prepend_dedupes_after_stripping_strategy(history_cache: Path) -> None:
    pip.prepend_push_options_history("r=alice", web_base=WEB, project=PROJECT_A)
    pip.prepend_push_options_history("r=alice lazy", web_base=WEB, project=PROJECT_A)
    assert pip.load_push_options_history(web_base=WEB, project=PROJECT_A) == ["r=alice"]


def test_apply_session_strategy_merges_non_default() -> None:
    assert apply_session_strategy("r=alice topic=t", "lazy") == "r=alice topic=t lazy"
    assert apply_session_strategy("r=alice", "overwrite") == "r=alice overwrite"
    assert apply_session_strategy("r=alice", "push") == "r=alice"
    assert apply_session_strategy("r=alice", None) == "r=alice"


def test_format_canonical_can_omit_strategy() -> None:
    state = parse("r=alice lazy").state
    assert format_canonical(state) == "r=alice lazy"
    assert format_canonical(state, include_strategy=False) == "r=alice"


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
