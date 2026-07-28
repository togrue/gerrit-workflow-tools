# Spec: docu/spec/change-and-commit-identifiers.md
# Covers: the changeish grammar — classification, field extraction, batch-ref reading

from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.changeish import Changeish, is_change_id, parse
from tests.fixtures import _cid

CHANGE_ID = _cid("a")
CHANGE_ID_UPPER = "I" + ("A" * 40)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HEAD", "git-rev"),
        ("HEAD~2", "git-rev"),
        ("a1b2c3d", "git-rev"),
        ("120045", "git-rev"),
        ("feature/x", "git-rev"),
        ("rev:120045", "git-rev"),
        ("git:120045", "git-rev"),
        (CHANGE_ID, "change-id"),
        ("myproject~main~" + CHANGE_ID, "triplet"),
        ("change:120045", "change-number"),
        ("cl:120045", "change-number"),
        ("refs/changes/45/120045/3", "change-ref"),
        ("https://gerrit.example.com/c/myproject/+/120045", "url"),
        ("q:status:open", "query"),
    ],
)
def test_parse_classifies(value: str, expected: str) -> None:
    assert parse(value).kind == expected


def test_parse_is_total_on_empty_input() -> None:
    """Blank input is a git rev, not an exception. Rejecting it is a resolution concern."""
    parsed = parse("   ")
    assert parsed.kind == "git-rev"
    assert parsed.raw == ""


# -- The Change-Id grammar: one predicate, either case ------------------------------


@pytest.mark.parametrize("value", [CHANGE_ID, CHANGE_ID_UPPER, "i" + ("a" * 40), "I" + ("aF0" * 13) + "b"])
def test_is_change_id_accepts_either_case(value: str) -> None:
    assert is_change_id(value)


@pytest.mark.parametrize("value", ["I" + ("a" * 39), "I" + ("a" * 41), "x" + ("a" * 40), "Ibad", ""])
def test_is_change_id_rejects_wrong_length_or_charset(value: str) -> None:
    assert not is_change_id(value)


def test_uppercase_change_id_classifies_as_change_id() -> None:
    """The drift this module exists to remove: uppercase hex used to be valid to `ger push`
    and invalid to `ger change-id`."""
    assert parse(CHANGE_ID_UPPER).kind == "change-id"
    assert parse(CHANGE_ID_UPPER).change_id == CHANGE_ID_UPPER


# -- Field extraction ---------------------------------------------------------------


def test_triplet_fields() -> None:
    parsed = parse(f"group/proj~main~{CHANGE_ID}")
    assert (parsed.project, parsed.branch, parsed.change_id) == ("group/proj", "main", CHANGE_ID)
    assert parsed.triplet == f"group/proj~main~{CHANGE_ID}"


@pytest.mark.parametrize(
    "value",
    [
        "only~two",
        f"~main~{CHANGE_ID}",
        f"proj~~{CHANGE_ID}",
        "proj~main~notachangeid",
        f"a~b~c~{CHANGE_ID}",
    ],
)
def test_near_triplets_are_git_revs(value: str) -> None:
    """Both halves are validated. The splitters this replaced each checked only one."""
    assert parse(value).kind == "git-rev"
    assert parse(value).triplet is None


def test_prefixes_are_stripped_case_insensitively() -> None:
    assert parse("REV:HEAD~1").rev == "HEAD~1"
    assert parse("CL:120045").number == "120045"
    assert parse("Q:status:open").query == "status:open"


def test_change_number_prefix_does_not_validate_digits() -> None:
    """Classification stays separate from validation — `resolve_changeish` rejects this."""
    parsed = parse("change:abc")
    assert parsed.kind == "change-number"
    assert parsed.number == "abc"


def test_change_ref_number() -> None:
    assert parse("refs/changes/45/120045/3").number == "120045"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gerrit.example.com/c/myproject/+/120045", "120045"),
        ("https://gerrit.example.com/c/myproject/+/120045/2", "120045"),
        ("https://gerrit.example.com/120045", "120045"),
        ("https://gerrit.example.com/dashboard/self", None),
    ],
)
def test_url_change_number(url: str, expected: str | None) -> None:
    assert parse(url).number == expected


def test_nested_project_url_misparses() -> None:
    """Known wrong, and preserved deliberately: the pattern allows only one path segment
    before ``/+/``, so a nested project falls through to the URL tail. Pre-existing — this
    module moved the rule without changing it. Fixing it is a behaviour change of its own."""
    assert parse("https://gerrit.example.com/c/group/sub/+/120045/2").number == "2"


def test_explicit_prefix_beats_shape() -> None:
    assert parse(f"rev:{CHANGE_ID}").kind == "git-rev"
    assert parse(f"rev:{CHANGE_ID}").rev == CHANGE_ID


# -- Batch refs are a different key space -------------------------------------------


def test_as_batch_ref_reads_triplets_and_numbers() -> None:
    assert parse(f"p~main~{CHANGE_ID}").as_batch_ref() == f"p~main~{CHANGE_ID}"
    assert parse("120045").as_batch_ref() == "120045"


def test_as_batch_ref_returns_none_rather_than_raising() -> None:
    """Not being a batch ref is not an error; it used to raise GerritApiError."""
    for value in ("HEAD", CHANGE_ID, "q:status:open", "refs/changes/45/120045/3"):
        assert parse(value).as_batch_ref() is None


def test_bare_number_is_a_git_rev_but_a_numeric_batch_ref() -> None:
    """The one place the two key spaces deliberately disagree."""
    parsed = parse("120045")
    assert parsed.kind == "git-rev"
    assert parsed.as_batch_ref() == "120045"


def test_changeish_is_frozen() -> None:
    with pytest.raises(AttributeError):
        parse("HEAD").kind = "query"  # type: ignore[misc]


def test_parse_round_trips_through_raw() -> None:
    assert parse("  HEAD~1  ") == Changeish(raw="HEAD~1", kind="git-rev", rev="HEAD~1")
