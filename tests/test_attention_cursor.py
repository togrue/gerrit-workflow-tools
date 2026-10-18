"""Attention cursor: TTL, adopt, history, and edit-attention target selection."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.attention import (
    annotate_attention,
    next_commit_needing_edit_attention_after,
)
from gerrit_workflow_tools.core.attention_cursor import (
    load_branch_cursor,
    parse_attention_cursor_ttl,
    prepare_usable_cursor,
    read_cursor_history,
    resolve_edit_attention_target,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import git


_COMMIT = LogCommit(
    sha="a" * 40,
    short_sha="aaaaaaa",
    summary="subj",
    change_id="Iaaa",
    pushed=True,
    abandoned=False,
    patchset_status=PatchsetStatus.ACTIVE,
    verified=1,
    code_review=2,
    comments_unresolved=0,
)


def _commit(**kwargs: Any) -> LogCommit:
    return replace(_COMMIT, **kwargs)


def _annotated(*commits: LogCommit) -> list[LogCommit]:
    rows = list(commits)
    annotate_attention(rows)
    return rows


def test_parse_attention_cursor_ttl() -> None:
    assert parse_attention_cursor_ttl(None) == timedelta(days=14)
    assert parse_attention_cursor_ttl("7d") == timedelta(days=7)
    assert parse_attention_cursor_ttl("2w") == timedelta(weeks=2)
    assert parse_attention_cursor_ttl("48h") == timedelta(hours=48)


def test_resolve_resume_still_attention_then_next() -> None:
    a = _commit(sha="1" * 40, short_sha="1111111", change_id="Ia", comments_unresolved=1)
    b = _commit(sha="2" * 40, short_sha="2222222", change_id="Ib", verified=-1)
    c = _commit(sha="3" * 40, short_sha="3333333", change_id="Ic", comments_unresolved=1)
    commits = _annotated(a, b, c)

    assert resolve_edit_attention_target(commits, mode="resume", cursor_change_id="Ia") is a

    a_clean = _commit(sha="1" * 40, short_sha="1111111", change_id="Ia")
    commits2 = _annotated(a_clean, b, c)
    assert resolve_edit_attention_target(commits2, mode="resume", cursor_change_id="Ia") is b


def test_resolve_next_skips_still_attention_cursor() -> None:
    a = _commit(sha="1" * 40, short_sha="1111111", change_id="Ia", comments_unresolved=1)
    b = _commit(sha="2" * 40, short_sha="2222222", change_id="Ib", verified=-1)
    commits = _annotated(a, b)
    assert resolve_edit_attention_target(commits, mode="next", cursor_change_id="Ia") is b
    assert next_commit_needing_edit_attention_after(commits, "Ia") is b


def test_resolve_first_is_oldest() -> None:
    a = _commit(sha="1" * 40, short_sha="1111111", change_id="Ia", verified=-1)
    b = _commit(sha="2" * 40, short_sha="2222222", change_id="Ib", comments_unresolved=1)
    commits = _annotated(a, b)
    assert resolve_edit_attention_target(commits, mode="first") is a


def _init_repo(path: Path) -> Path:
    path.mkdir()
    git("init", "-b", "main", cwd=path)
    git("commit", "--allow-empty", "-m", "init", cwd=path)
    return path


def test_write_and_prepare_cursor_roundtrip(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    write_branch_cursor(repo, "main", "Iabc", action="first", sha="deadbeef")
    settings = Settings.from_cwd(repo)
    loaded = load_branch_cursor(settings, "main")
    assert loaded is not None
    assert loaded.change_id == "Iabc"
    assert settings.last_attention_change_id == "Iabc"
    hist = read_cursor_history(repo)
    assert hist[-1].action == "first"
    assert hist[-1].change_id == "Iabc"


def test_ttl_clears_even_if_still_in_stack(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    write_branch_cursor(repo, "main", "Iabc", action="set", now=old)
    git("config", "ger.attentionCursorTtl", "14d", cwd=repo)
    settings = Settings.from_cwd(repo)
    commit = _commit(change_id="Iabc", comments_unresolved=1)
    commits = _annotated(commit)
    result = prepare_usable_cursor(repo, branch="main", commits=commits, settings=settings)
    assert result.change_id is None
    assert any("expired" in n for n in result.notes)
    hist = read_cursor_history(repo)
    assert any(e.action == "clear-ttl" for e in hist)
    fresh = Settings.from_cwd(repo)
    assert load_branch_cursor(fresh, "main") is None
    assert fresh.last_attention_change_id is None


def test_lazy_adopt_when_branch_cursor_empty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    git("checkout", "-b", "feature", cwd=repo)
    git("config", "ger.lastAttentionChangeId", "Iadopt", cwd=repo)
    git("config", "ger.lastAttentionChangeIdAt", "2020-01-01T00:00:00Z", cwd=repo)
    settings = Settings.from_cwd(repo)
    commits = _annotated(_commit(change_id="Iadopt", comments_unresolved=1))
    result = prepare_usable_cursor(repo, branch="feature", commits=commits, settings=settings)
    assert result.change_id == "Iadopt"
    assert any("adopted" in n for n in result.notes)
    assert load_branch_cursor(Settings.from_cwd(repo), "feature") is not None


def test_clear_missing_when_change_id_absent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    write_branch_cursor(repo, "main", "Igone", action="set")
    settings = Settings.from_cwd(repo)
    commits = _annotated(_commit(change_id="Iother", comments_unresolved=1))
    result = prepare_usable_cursor(repo, branch="main", commits=commits, settings=settings)
    # lastAttentionChangeId is still Igone (not in stack) → no adopt
    assert result.change_id is None
    assert any("not in stack" in n for n in result.notes)
    assert any(e.action == "clear-missing" for e in read_cursor_history(repo))
