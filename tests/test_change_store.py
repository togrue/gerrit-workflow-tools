"""Tests for ChangeStore — the payload-backed GerritRest implementation."""

from __future__ import annotations

import inspect

import pytest

from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_change_info
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import change_info_for_sha

CID_A = "I" + "a" * 40
CID_B = "I" + "b" * 40


def _store() -> ChangeStore:
    """Two branches carrying the same Change-Id, plus a second change on main."""
    main_a = change_info_for_sha("sha_a_main", CID_A, branch="main", number=1)
    dev_a = change_info_for_sha("sha_a_dev", CID_A, branch="dev", number=2)
    main_b = change_info_for_sha("sha_b_main", CID_B, branch="main", number=3)
    return ChangeStore({str(row["id"]): row for row in (main_a, dev_a, main_b)})


# ---------------------------------------------------------------------------
# Query shapes the project actually issues
# ---------------------------------------------------------------------------


def test_project_scoped_or_returns_every_branch() -> None:
    """The stack overlay queries without branch: and disambiguates client-side."""
    rows = _store().query_changes(f"project:testproj (change:{CID_A} OR change:{CID_B})")
    assert sorted(row["_number"] for row in rows) == [1, 2, 3]


def test_triplet_scope_selects_one_branch() -> None:
    rows = _store().query_changes(f"project:testproj branch:dev change:{CID_A}")
    assert [row["_number"] for row in rows] == [2]


def test_bare_change_id_without_project_scope_matches_all_branches() -> None:
    rows = _store().query_changes(f"change:{CID_A}")
    assert sorted(row["_number"] for row in rows) == [1, 2]


def test_unknown_change_id_returns_empty_not_error() -> None:
    assert _store().query_changes(f"change:{'I' + 'c' * 40}") == []


def test_n_limits_returned_rows() -> None:
    rows = _store().query_changes(f"project:testproj (change:{CID_A} OR change:{CID_B})", n=2)
    assert len(rows) == 2


def test_stub_query_answers_searches_the_payload_engine_cannot_model() -> None:
    store = _store()
    row = change_info_for_sha("sha_x", "I" + "d" * 40, number=99)
    store.stub_query("status:open", [row])
    assert [r["_number"] for r in store.query_changes("status:open")] == [99]


def test_commit_query_matches_current_revision() -> None:
    store = _store()
    rows = store.query_changes("commit:sha_a_main")
    assert [row["_number"] for row in rows] == [1]


# ---------------------------------------------------------------------------
# Lookup by the several things Gerrit accepts as a change id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected_number",
    [
        (f"testproj~dev~{CID_A}", 2),
        ("3", 3),
        (CID_B, 3),
    ],
)
def test_get_change_accepts_triplet_number_and_change_id(ref: str, expected_number: int) -> None:
    assert _store().get_change(ref)["_number"] == expected_number


def test_get_change_unknown_raises_gerrit_api_error() -> None:
    """Misses must surface as the seam's error type, so callers map them normally."""
    with pytest.raises(GerritApiError, match="no matching change"):
        _store().get_change("testproj~main~" + "I" + "f" * 40)


# ---------------------------------------------------------------------------
# Stateful writes
# ---------------------------------------------------------------------------


def test_added_reviewers_are_visible_to_a_later_read() -> None:
    store = _store()
    triplet = f"testproj~main~{CID_A}"
    store.set_reviewers_batch(triplet, reviewers=["alice"], ccs=["bob"])

    rows = store.list_change_reviewers(triplet)
    by_state = {row["account"]["username"]: row["state"] for row in rows}
    assert by_state["alice"] == "REVIEWER"
    assert by_state["bob"] == "CC"

    # and through the ChangeInfo the production normalizer reads
    slugs = [account.slug for account in reviewer_accounts_from_change_info(store.get_change(triplet))]
    assert "alice" in slugs and "bob" in slugs


def test_adding_the_same_reviewer_twice_does_not_duplicate() -> None:
    store = _store()
    triplet = f"testproj~main~{CID_A}"
    store.set_reviewers_batch(triplet, reviewers=["alice"])
    store.set_reviewers_batch(triplet, reviewers=["alice"])
    usernames = [row["account"]["username"] for row in store.list_change_reviewers(triplet)]
    assert usernames.count("alice") == 1


def test_delete_reviewer_removes_by_account_id() -> None:
    store = _store()
    triplet = f"testproj~main~{CID_A}"
    store.set_reviewers_batch(triplet, reviewers=["alice"])
    alice = next(r for r in store.list_change_reviewers(triplet) if r["account"]["username"] == "alice")

    store.delete_reviewer(triplet, alice["account"]["_account_id"])

    usernames = [row["account"]["username"] for row in store.list_change_reviewers(triplet)]
    assert "alice" not in usernames


def test_writes_touch_only_the_addressed_branch() -> None:
    """Same Change-Id on two branches must not share reviewer state."""
    store = _store()
    store.set_reviewers_batch(f"testproj~main~{CID_A}", reviewers=["alice"])
    dev_usernames = [row["account"]["username"] for row in store.list_change_reviewers(f"testproj~dev~{CID_A}")]
    assert "alice" not in dev_usernames


def test_topic_wip_and_private_are_observable() -> None:
    store = _store()
    triplet = f"testproj~main~{CID_A}"
    store.set_topic(triplet, "my-topic")
    store.set_wip(triplet, True)
    store.set_private(triplet, True)

    change = store.get_change(triplet)
    assert change["topic"] == "my-topic"
    assert change["work_in_progress"] is True
    assert change["private"] is True

    store.set_topic(triplet, None)
    assert store.get_change(triplet)["topic"] is None


# ---------------------------------------------------------------------------
# Call recording
# ---------------------------------------------------------------------------


def test_queries_are_recorded_in_order() -> None:
    store = _store()
    store.query_changes(f"change:{CID_A}")
    store.query_changes(f"project:testproj change:{CID_B}")
    assert store.queries() == [f"change:{CID_A}", f"project:testproj change:{CID_B}"]


def test_calls_to_records_arguments() -> None:
    store = _store()
    store.set_topic(f"testproj~main~{CID_A}", "t")
    calls = store.calls_to("set_topic")
    assert len(calls) == 1
    assert calls[0].args == (f"testproj~main~{CID_A}", "t")


def test_comments_and_checks_default_empty_and_can_be_seeded() -> None:
    store = _store()
    triplet = f"testproj~main~{CID_A}"
    assert store.get_comments(triplet) == {}
    assert store.get_checks(triplet) == []
    assert store.get_messages(triplet) == []

    store.set_comments(triplet, {"a.py": [{"id": "c1", "message": "hi"}]})
    store.set_checks(triplet, [{"state": "FAILED", "checker_name": "build"}])
    store.set_messages(triplet, [{"message": "Build failed"}])
    assert store.get_comments(triplet)["a.py"][0]["message"] == "hi"
    assert store.get_checks(triplet)[0]["checker_name"] == "build"
    assert store.get_messages(triplet)[0]["message"] == "Build failed"


def test_comments_seeded_by_number_are_found_by_triplet() -> None:
    """Seeding and reading may use different aliases for the same change."""
    store = _store()
    store.set_comments("1", {"a.py": [{"id": "c1", "message": "hi"}]})
    assert store.get_comments(f"testproj~main~{CID_A}")["a.py"][0]["message"] == "hi"


# ---------------------------------------------------------------------------
# Conformance to the seam
# ---------------------------------------------------------------------------


def _protocol_operations() -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in vars(GerritRest).items()
        if callable(member) and not name.startswith("_")
    }


def test_change_store_implements_every_gerrit_rest_operation() -> None:
    """ChangeStore must satisfy GerritRest.

    mypy only runs over ``src/`` and ChangeStore lives here, so the type checker cannot
    catch drift between the two. This asserts it instead — without it, adding an operation
    to the seam would silently leave ChangeStore behind and every test using it would keep
    passing against a stale surface.
    """
    operations = _protocol_operations()
    assert len(operations) >= 14, f"expected the full seam, discovered {sorted(operations)}"

    for name, signature in operations.items():
        implementation = getattr(ChangeStore, name, None)
        assert implementation is not None, f"ChangeStore does not implement GerritRest.{name}"
        assert inspect.signature(implementation) == signature, f"ChangeStore.{name} has drifted from GerritRest.{name}"


def test_change_store_exposes_web_base() -> None:
    """``web_base`` is part of the seam, not just an implementation detail."""
    assert ChangeStore({}, web_base="https://g.example/").web_base == "https://g.example"
