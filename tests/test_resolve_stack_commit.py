# Spec: docu/spec/change-and-commit-identifiers.md
# Covers: resolve_stack_changeish — the one path ger fix / edit / reword / rebase share

from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeAmbiguousError,
    ChangeResolutionError,
    resolve_stack_changeish,
)
from gerrit_workflow_tools.core.git_run import git_out
from tests.fixtures import _cid


def _resolve(repo, ref, **kwargs):
    return resolve_stack_changeish(repo, ref, settings=Settings.from_cwd(repo), **kwargs).sha


def test_resolve_by_change_id(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    full = _resolve(stack_repo, _cid("2"))
    subj = git_out("log", "-1", "--format=%s", full, cwd=stack_repo)
    assert subj == "Extract command routing"


def test_resolve_change_id_case_insensitive(stack_repo, monkeypatch):
    """One Change-Id grammar, either case — so matching has to be case-insensitive too.

    The grammar has always accepted a lowercase ``i`` prefix, and ``norm_change_id`` lowercases
    ids for lookup, but the stack match compared footers exactly. Accepting a spelling and then
    failing to find it was the drift; ``i2222…`` now resolves to the commit whose footer spells
    it ``I2222…``.
    """
    monkeypatch.chdir(stack_repo)
    cid = _cid("2")
    full = _resolve(stack_repo, cid)
    assert full == _resolve(stack_repo, cid.lower())
    assert full == _resolve(stack_repo, cid.upper())


def test_resolve_by_short_sha(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    sha = git_out("rev-parse", "--short", "HEAD~1", cwd=stack_repo)
    assert _resolve(stack_repo, sha) == git_out("rev-parse", sha, cwd=stack_repo)


def test_resolve_unknown_change_id(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    with pytest.raises(ChangeResolutionError, match="no commit in current stack"):
        _resolve(stack_repo, "I" + "f" * 40)


def test_resolve_ambiguous_change_id(dup_repo, monkeypatch):
    monkeypatch.chdir(dup_repo)
    with pytest.raises(ChangeAmbiguousError, match="ambiguous Change-Id"):
        _resolve(dup_repo, _cid("a"))


# -- require_in_stack: the one axis on which the four commands differ ----------------


def test_require_in_stack_rejects_a_commit_below_the_stack(stack_repo, monkeypatch):
    """`ger fix` / `ger edit` / `ger reword` pass require_in_stack; a commit below the
    upstream tip cannot be rewritten by an interactive rebase over the stack.

    The upstream tip itself is the deterministic case: the stack is ``upstream_tip..HEAD``,
    which excludes it.
    """
    monkeypatch.chdir(stack_repo)
    upstream_tip = git_out("rev-parse", "@{upstream}", cwd=stack_repo)
    with pytest.raises(ChangeResolutionError, match="not in the current local stack"):
        _resolve(stack_repo, upstream_tip, require_in_stack=True)


def test_require_in_stack_allows_a_stack_commit(stack_repo, monkeypatch):
    monkeypatch.chdir(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    assert _resolve(stack_repo, sha, require_in_stack=True) == sha


def test_without_require_in_stack_any_commit_resolves(stack_repo, monkeypatch):
    """`ger rebase` takes the commit to rebase *from*, which normally sits below the stack."""
    monkeypatch.chdir(stack_repo)
    upstream_tip = git_out("rev-parse", "@{upstream}", cwd=stack_repo)
    assert _resolve(stack_repo, upstream_tip) == upstream_tip
