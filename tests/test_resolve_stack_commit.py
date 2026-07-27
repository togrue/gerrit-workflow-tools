from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import resolve_stack_commit
from tests.fixtures import _cid


def test_resolve_by_change_id(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    cid = _cid("2")
    full = resolve_stack_commit(stack_repo, cid, settings=Settings.from_cwd(stack_repo))
    subj = git_out("log", "-1", "--format=%s", full, cwd=stack_repo)
    assert subj == "Extract command routing"


def test_resolve_change_id_case_sensitive(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    cid = _cid("2")
    full = resolve_stack_commit(stack_repo, cid, settings=Settings.from_cwd(stack_repo))
    with pytest.raises(GitError, match="no commit in current stack"):
        resolve_stack_commit(stack_repo, cid.lower(), settings=Settings.from_cwd(stack_repo))
    assert full == resolve_stack_commit(stack_repo, cid, settings=Settings.from_cwd(stack_repo))


def test_resolve_by_short_sha(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    sha = git_out("rev-parse", "--short", "HEAD~1", cwd=stack_repo)
    full = resolve_stack_commit(stack_repo, sha, settings=Settings.from_cwd(stack_repo))
    assert full == git_out("rev-parse", sha, cwd=stack_repo)


def test_resolve_unknown_change_id(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    missing = "I" + "f" * 40
    with pytest.raises(GitError, match="no commit in current stack"):
        resolve_stack_commit(stack_repo, missing, settings=Settings.from_cwd(stack_repo))


def test_resolve_ambiguous_change_id(dup_repo, monkeypatch):
    monkeypatch.chdir(dup_repo)
    cid = _cid("a")
    with pytest.raises(GitError, match="ambiguous Change-Id"):
        resolve_stack_commit(dup_repo, cid, settings=Settings.from_cwd(dup_repo))
