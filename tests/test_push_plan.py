"""Push plan revise keeps the commit list and refreshes the credential gap."""

from __future__ import annotations

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.push import PushBoundary, PushOptions, PushPlan, push_refspec, revise
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.core.stack import Commit


def _plan(commits: tuple[Commit, ...], options: PushOptions) -> PushPlan:
    return PushPlan(
        branch="feature",
        push_branch="main",
        remote="origin",
        tip_sha=commits[0].sha,
        commits=commits,
        marks=None,
        boundary=PushBoundary(None, "", None, None, 0),
        options=options,
        credential_error=None,
        warnings=(),
        update_last_pushed=False,
    )


def test_revise_keeps_the_commit_list_and_refreshes_the_credential_gap() -> None:
    commits = (Commit("a" * 40, "aaaaaaa", "subj", "", "I" + "ab" * 20),)
    plan = _plan(commits, PushOptions((), ReviewerStrategy.PUSH))
    settings = Settings.from_map({})

    revised = revise(plan, PushOptions(("alice",), ReviewerStrategy.LAZY), settings=settings)

    assert revised.commits is commits
    assert revised.credential_error is not None
    assert "REST" in revised.credential_error
    restored = revise(revised, PushOptions((), ReviewerStrategy.PUSH), settings=settings)
    assert restored.commits is commits
    assert restored.credential_error is None


def test_push_refspec_omits_reviewers_for_lazy() -> None:
    options = PushOptions(("alice",), ReviewerStrategy.LAZY, topic="my-series", wip=True)
    assert push_refspec("abc", "main", options) == "abc:refs/for/main%wip,topic=my-series"
