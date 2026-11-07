# Spec: comment chain grouping in gerrit_change_status

from __future__ import annotations

from gerrit_workflow_tools.core.comment_chains import (
    build_comment_chains,
    collect_comment_chains,
    collect_unresolved_comment_chains,
    count_unresolved_in_file_map,
)
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
    assert collect_unresolved_comment_chains(file_map) == []
    assert count_unresolved_in_file_map(file_map) == 0


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
    chains = collect_unresolved_comment_chains(file_map)
    assert len(chains) == 1
    assert chains[0].resolved is False
    assert count_unresolved_in_file_map(file_map) == 1


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
    assert count_unresolved_in_file_map(file_map) == 1


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
    assert collect_comment_chains(file_map, comments="unresolved") == collect_unresolved_comment_chains(file_map)


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


def test_chain_comments_keep_patchset_and_own_flag() -> None:
    root = _comment("root", updated="2024-01-01 10:00:00", unresolved=True)
    root["patch_set"] = 3
    reply = _comment("reply", in_reply_to="root", updated="2024-01-01 11:00:00", unresolved=False)
    reply["patch_set"] = 4
    (chain,) = build_comment_chains({"a.py": [root, reply]})
    assert [(c.patch_set, c.unresolved, c.draft) for c in chain.comments] == [(3, True, False), (4, False, False)]


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
