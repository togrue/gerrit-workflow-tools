"""A change that waits on your review says so wherever it is shown: listed under *to review*, and
in its own view (``/show``, what VS Code's Change view leads with).

Both read one verdict: the *to review* section's query (``build_to_review_query``). The Change view
once showed the author's reasons instead, so a change listed under *to review* read "No reviewers,
awaiting review" when opened (issue #71).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.attention import Attention, as_your_review
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.inbox import TO_REVIEW, build_to_review_query
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.test_inbox import SELF, _change
from tests.test_serve import _gerrit, _get


def _theirs(number: int, *, asks_you: bool, verified: int = 1) -> dict[str, Any]:
    """Alice's change *number* on testproj; *asks_you*: you are among its reviewers."""
    change = _change(
        number=number,
        sha=f"{number:040x}",
        parent=None,
        subject=f"Alice's change {number}",
        updated="2026-11-08 09:00:00.000000000",
        verified=verified,
    )
    change.update(project="testproj", id=f"testproj~main~{change['change_id']}")
    if asks_you:
        change["reviewers"] = {"REVIEWER": [{"_account_id": SELF, "name": "me", "username": "me"}]}
    return change


def _store(repo: Path, *changes: dict[str, Any]) -> ChangeStore:
    """The repository's own stack on Gerrit, *changes* beside it, and *to review* listing those that ask you."""
    store, _keys, _rows = _gerrit(repo)
    for change in changes:
        store.add_change(change)
    query = build_to_review_query(Settings.from_cwd(repo), projects=["testproj"], include_unready=False)
    store.stub_query(query, [change for change in changes if "reviewers" in change])
    return store


def _shown(api: GerApi, number: int) -> dict[str, Any]:
    [commit] = _get(api, "/show", rev=f"change:{number}")["commits"]
    return commit


def test_a_change_that_waits_on_your_review_leads_with_that_in_its_view(stack_repo: Path) -> None:
    api = GerApi(
        stack_repo, gerrit=_store(stack_repo, _theirs(900, asks_you=True), _theirs(901, asks_you=True, verified=-1))
    )
    shown = _shown(api, 900)
    assert shown["attention_reasons"] == [Attention.WAITING_ON_YOU], "not the author's 'awaiting review'"
    assert shown["severity"] == "pending"
    # What else is true of the change stays, after it.
    assert _shown(api, 901)["attention_reasons"] == [Attention.WAITING_ON_YOU, Attention.CI_FAILED]


def test_somebodys_change_that_does_not_ask_you_keeps_the_authors_reasons(stack_repo: Path) -> None:
    api = GerApi(stack_repo, gerrit=_store(stack_repo, _theirs(900, asks_you=False)))
    assert _shown(api, 900)["attention_reasons"] == [Attention.NO_REVIEWERS, Attention.AWAITING_REVIEW]


def test_the_to_review_list_and_each_changes_view_agree(stack_repo: Path) -> None:
    changes = [_theirs(900, asks_you=True), _theirs(901, asks_you=False), _theirs(902, asks_you=True, verified=-1)]
    api = GerApi(stack_repo, gerrit=_store(stack_repo, *changes))
    [to_review] = [section for section in _get(api, "/overview")["sections"] if section["name"] == TO_REVIEW]
    listed = {member["number"] for chain in to_review["chains"] for member in chain["members"]}
    leads = {
        change["_number"]
        for change in changes
        if _shown(api, change["_number"])["attention_reasons"][:1] == [Attention.WAITING_ON_YOU]
    }
    assert listed == leads == {900, 902}


def test_your_review_replaces_what_the_author_waits_for_from_reviewers() -> None:
    reasons = [Attention.CI_FAILED, Attention.NO_REVIEWERS, Attention.AWAITING_REVIEW, Attention.UNRESOLVED_COMMENTS]
    assert as_your_review(reasons) == [Attention.WAITING_ON_YOU, Attention.CI_FAILED, Attention.UNRESOLVED_COMMENTS]


def test_a_colleagues_change_in_your_local_stack_still_waits_on_you(stack_repo: Path) -> None:
    """``ger review 900`` puts Alice's change in the local stack: it waits on you there too."""
    store, keys, _rows = _gerrit(stack_repo)
    top = store._changes[keys[-1]]  # pylint: disable=protected-access
    top["owner"] = {"name": "Alice", "email": "alice@example.com", "_account_id": 2}
    top["reviewers"] = {"REVIEWER": [{"_account_id": SELF, "name": "me", "username": "me"}]}
    api = GerApi(stack_repo, gerrit=store)
    [head] = _get(api, "/show", rev="HEAD")["commits"]
    assert head["attention_reasons"][:1] == [Attention.WAITING_ON_YOU]
    [below] = _get(api, "/show", rev="HEAD~1")["commits"]
    assert Attention.WAITING_ON_YOU not in below["attention_reasons"], (
        "your own change waits on nobody's review of yours"
    )


def test_gerrit_refusing_the_to_review_query_keeps_the_authors_reasons(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(stack_repo, _theirs(900, asks_you=True))
    asked = store.query_changes

    def refusing(query: str, *args: Any, **kwargs: Any) -> Any:
        if "reviewer:self" in query and "change:900" in query:
            raise GerritApiError("Gerrit HTTP 400: bad query", status=400)
        return asked(query, *args, **kwargs)

    monkeypatch.setattr(store, "query_changes", refusing)
    assert _shown(GerApi(stack_repo, gerrit=store), 900)["attention_reasons"] == [Attention.AWAITING_REVIEW]


def test_showing_changes_asks_the_to_review_query_once_and_only_for_those_that_list_you(stack_repo: Path) -> None:
    store = _store(stack_repo, _theirs(900, asks_you=True), _theirs(901, asks_you=False))
    api = GerApi(stack_repo, gerrit=store)
    _get(api, "/show", rev=["change:900", "change:901"])
    asked = [query for query in store.queries() if "reviewer:self" in query]
    assert len(asked) == 1, asked
    assert "change:900" in asked[0] and "change:901" not in asked[0]


def test_a_custom_to_review_query_is_asked_for_changes_that_do_not_list_you(stack_repo: Path) -> None:
    """``ger.inboxToReviewQuery`` may select changes without ``reviewer:self``: none is skipped for it."""
    subprocess.run(["git", "config", "ger.inboxToReviewQuery", "is:open topic:team-review"], cwd=stack_repo, check=True)
    store = _store(stack_repo, _theirs(900, asks_you=False))
    shown = _shown(GerApi(stack_repo, gerrit=store), 900)
    assert any("topic:team-review" in query and "change:900" in query for query in store.queries())
    assert shown["attention_reasons"][:1] == [Attention.WAITING_ON_YOU]
