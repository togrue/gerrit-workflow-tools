# Spec: comment chain grouping in gerrit_change_status

from __future__ import annotations

from gerrit_workflow_tools.core.comment_chains import (
    build_comment_chains,
    collect_comment_chains,
    unresolved_chain_counts,
)
from gerrit_workflow_tools.core.gerrit_change_status import ChainTurn
from gerrit_workflow_tools.render.comments import chain_location


def _comment(
    cid: str,
    *,
    message: str = "msg",
    unresolved: bool = True,
    in_reply_to: str | None = None,
    updated: str = "",
) -> dict:
    out: dict = {
        "id": cid,
        "message": message,
        "unresolved": unresolved,
        "updated": updated,
    }
    if in_reply_to is not None:
        out["in_reply_to"] = in_reply_to
    return out


def test_build_comment_chains_groups_replies() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=True),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=False,
                message="fixed",
            ),
        ]
    }
    chains = build_comment_chains(file_map)
    assert len(chains) == 1
    chain = chains[0]
    assert chain.root_id == "root"
    assert chain.resolved is True
    assert [c.comment_id for c in chain.comments] == ["root", "reply"]
    assert chain.comments[1].message == "fixed"


def test_collect_unresolved_skips_resolved_chain_tail() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=True),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=False,
            ),
        ]
    }
    assert collect_comment_chains(file_map, comments="unresolved") == []
    assert unresolved_chain_counts(file_map, None) == (0, 0)


def test_collect_unresolved_includes_open_chain() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=False),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=True,
                message="still open",
            ),
        ]
    }
    chains = collect_comment_chains(file_map, comments="unresolved")
    assert len(chains) == 1
    assert chains[0].resolved is False
    assert unresolved_chain_counts(file_map, None) == (1, 0)


def test_multiple_independent_chains() -> None:
    file_map = {
        "a.py": [
            _comment("c1", updated="1", unresolved=True),
            _comment("c2", updated="2", unresolved=False),
        ]
    }
    file_map["a.py"][0]["line"] = 1
    file_map["a.py"][1]["line"] = 5
    chains = build_comment_chains(file_map)
    assert len(chains) == 2
    assert unresolved_chain_counts(file_map, None) == (1, 0)


def test_orphan_reply_becomes_own_chain() -> None:
    file_map = {
        "a.py": [
            _comment(
                "orphan",
                in_reply_to="missing-parent",
                updated="1",
                unresolved=True,
            ),
        ]
    }
    chains = build_comment_chains(file_map)
    assert len(chains) == 1
    assert chains[0].root_id == "orphan"
    assert chains[0].resolved is False


def test_collect_comment_chains_all_puts_unresolved_first() -> None:
    file_map = {
        "a.py": [
            _comment("open", updated="1", unresolved=True),
            _comment("closed", updated="2", unresolved=False),
        ]
    }
    file_map["a.py"][0]["line"] = 10
    file_map["a.py"][1]["line"] = 1
    all_chains = collect_comment_chains(file_map, comments="all")
    assert [chain.root_id for chain in all_chains] == ["open", "closed"]
    assert [chain.resolved for chain in all_chains] == [False, True]
    resolved = collect_comment_chains(file_map, comments="resolved")
    assert [chain.root_id for chain in resolved] == ["closed"]


def test_build_comment_chains_keeps_context_lines() -> None:
    file_map = {
        "a.py": [
            {
                "id": "root",
                "message": "nit",
                "unresolved": True,
                "updated": "1",
                "line": 2,
                "context_lines": [
                    {"line_number": 1, "context_line": "alpha()"},
                    {"line_number": 2, "context_line": "beta()"},
                ],
            }
        ]
    }
    chain = build_comment_chains(file_map)[0]
    assert [(line.line_number, line.text) for line in chain.comments[0].context_lines] == [
        (1, "alpha()"),
        (2, "beta()"),
    ]


def test_range_comment_keeps_its_first_line() -> None:
    comment = {"id": "c1", "line": 4, "range": {"start_line": 2, "end_line": 4}, "unresolved": True}
    chain = build_comment_chains({"s3.txt": [comment]})[0]
    assert (chain.start_line, chain.line) == (2, 4)
    assert chain_location(chain) == "s3.txt:2-4"


def _draft(cid: str, *, in_reply_to: str | None, unresolved: bool, updated: str = "", message: str = "Done") -> dict:
    out = _comment(cid, message=message, unresolved=unresolved, in_reply_to=in_reply_to, updated=updated)
    out["patch_set"] = 2
    return out


def test_draft_reply_joins_its_chain_last_and_keeps_published_state() -> None:
    published = {"a.py": [_comment("root", updated="2024-01-02 10:00:00", unresolved=True)]}
    # Older timestamp than the comment it answers: publishing stamps it last anyway.
    drafts = {"a.py": [_draft("mine", in_reply_to="root", unresolved=False, updated="2024-01-01 09:00:00")]}
    (chain,) = build_comment_chains(published, drafts)
    assert [c.comment_id for c in chain.comments] == ["root", "mine"]
    assert chain.resolved is False
    assert chain.resolved_after_publish is True
    assert chain.draft is not None and chain.draft.comment_id == "mine"
    # Still listed as unresolved: the filter is Gerrit's published state.
    assert collect_comment_chains(published, drafts=drafts) == [chain]


def test_chain_without_drafts_has_no_draft_and_same_state_after_publish() -> None:
    (chain,) = build_comment_chains({"a.py": [_comment("root", unresolved=True)]})
    assert chain.draft is None
    assert chain.resolved_after_publish is chain.resolved is False


def test_draft_only_chain_is_your_own_unpublished_comment() -> None:
    (chain,) = build_comment_chains({}, {"b.py": [_draft("new", in_reply_to=None, unresolved=True)]})
    assert chain.root_id == "new"
    assert chain.comments[0].draft is True
    assert chain.resolved is False and chain.resolved_after_publish is False


ME, REVIEWER = 1000, 2000


def _by(cid: str, author: int, **kwargs) -> dict:
    out = _comment(cid, **kwargs)
    out["author"] = {"_account_id": author}
    return out


def test_turn_follows_the_last_published_comment() -> None:
    file_map = {
        "a.py": [
            _by("asked", REVIEWER, updated="2024-01-01 10:00:00"),
            _by("answered", ME, in_reply_to="asked", updated="2024-01-01 11:00:00"),
            _by("open", REVIEWER, updated="2024-01-01 10:00:00"),
        ]
    }
    turns = {chain.root_id: chain.turn for chain in build_comment_chains(file_map, me=ME)}
    assert turns == {"asked": ChainTurn.THEIRS, "open": ChainTurn.YOURS}
    assert unresolved_chain_counts(file_map, ME) == (2, 1)


def test_a_draft_answer_is_still_your_turn_to_publish() -> None:
    published = {"a.py": [_by("asked", REVIEWER)]}
    drafts = {"a.py": [_draft("mine", in_reply_to="asked", unresolved=True)]}
    (chain,) = build_comment_chains(published, drafts, me=ME)
    assert chain.turn is ChainTurn.DRAFT
    # Your own last word stays theirs, a further draft of yours or not.
    (chain,) = build_comment_chains(
        {"a.py": [_by("said", ME)]}, {"a.py": [_draft("more", in_reply_to="said", unresolved=True)]}, me=ME
    )
    assert chain.turn is ChainTurn.THEIRS


def test_without_knowing_who_you_are_no_chain_has_a_turn() -> None:
    file_map = {"a.py": [_by("said", ME)]}
    assert [chain.turn for chain in build_comment_chains(file_map)] == [None]
    assert unresolved_chain_counts(file_map, None) == (1, 0)
