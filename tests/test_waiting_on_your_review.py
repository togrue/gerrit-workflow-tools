"""A change that waits on your review says so wherever it is shown: listed under *to review*, and
in its own view (``/show``, what VS Code's Change view leads with).

Both read one verdict: the *to review* section's query (``build_to_review_query``). The Change view
once showed the author's reasons instead, so a change listed under *to review* read "No reviewers,
awaiting review" when opened (issue #71).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.attention import Attention, as_your_review
from gerrit_workflow_tools.core.config import Settings
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
