from __future__ import annotations

import pytest

from gerrit_workflow_tools.push_input_line import (
    Diagnostic,
    PushLineState,
    format_canonical,
    parse,
    tokenize,
)


def _state(buf: str) -> PushLineState:
    return parse(buf).state


def _diag_severities(buf: str) -> list[str]:
    return [d.severity for d in parse(buf).diagnostics]


# ---------- examples A: r alice,bob wip variants ----------


@pytest.mark.parametrize(
    "buf",
    [
        "r alice,bob wip",
        "r=alice,bob wip",
        "alice bob wip",
        "wip alice bob",
        "alice wip bob",
    ],
)
def test_example_a_variants_all_yield_alice_bob_wip(buf: str) -> None:
    s = _state(buf)
    assert s.reviewers == ["alice", "bob"]
    assert s.wip is True
    assert s.private is False
    assert s.topic is None
    assert parse(buf).valid_for_apply


# ---------- examples B: bob topic=abc alice variants ----------


@pytest.mark.parametrize(
    "buf",
    [
        "bob topic=abc alice",
        "bob topic abc alice",
        'bob topic "abc" alice',
        "topic=abc alice bob",
    ],
)
def test_example_b_variants_all_yield_alice_bob_topic_abc(buf: str) -> None:
    s = _state(buf)
    assert s.reviewers == ["bob", "alice"] or s.reviewers == ["alice", "bob"]
    assert s.topic == "abc"


# ---------- removal ----------


def test_minus_reviewer_removes_from_list() -> None:
    s = _state("alice bob -alice")
    assert s.reviewers == ["bob"]


def test_minus_wip_clears_flag() -> None:
    s = _state("wip -wip")
    assert s.wip is False


def test_minus_topic_clears_topic() -> None:
    s = _state("topic=abc -topic")
    assert s.topic is None


def test_minus_r_clears_reviewers() -> None:
    s = _state("alice bob -r")
    assert s.reviewers == []


def test_minus_then_re_add() -> None:
    s = _state("alice -alice alice")
    assert s.reviewers == ["alice"]


# ---------- collisions / keyword precedence ----------


def test_keyword_wip_not_treated_as_reviewer_name() -> None:
    s = _state("wip")
    assert s.wip is True
    assert s.reviewers == []


def test_dedup_reviewers_preserves_order() -> None:
    s = _state("alice bob alice carol bob")
    assert s.reviewers == ["alice", "bob", "carol"]


def test_strategy_keywords_set_strategy_last_one_wins() -> None:
    s = _state("lazy overwrite push lazy")
    assert s.strategy == "lazy"


def test_publish_drafts_is_a_flag_not_a_reviewer() -> None:
    s = _state("alice publish-drafts")
    assert s.reviewers == ["alice"]
    assert s.publish_drafts is True
    assert not parse("alice publish-drafts").diagnostics


def test_minus_publish_drafts_keeps_the_drafts() -> None:
    assert _state("publish-drafts -publish-drafts").publish_drafts is False
    assert _state("alice").publish_drafts is False


# ---------- diagnostics ----------


def test_unclosed_quote_errors() -> None:
    res = parse('topic "abc')
    assert any(d.severity == "error" and "Unclosed" in d.message for d in res.diagnostics)
    assert not res.valid_for_apply


def test_empty_r_equals_errors() -> None:
    sevs = _diag_severities("r=")
    assert "error" in sevs


def test_topic_without_value_errors() -> None:
    sevs = _diag_severities("topic")
    assert "error" in sevs


def test_topic_followed_by_keyword_errors() -> None:
    sevs = _diag_severities("topic wip")
    assert "error" in sevs


def test_unknown_key_errors() -> None:
    sevs = _diag_severities("foo=bar")
    assert "error" in sevs


def test_topic_redefined_warns() -> None:
    res = parse("topic=a topic=b")
    assert res.state.topic == "b"
    assert any(d.severity == "warning" for d in res.diagnostics)


# ---------- canonical format ----------


@pytest.mark.parametrize(
    "buf,expected",
    [
        ("r alice,bob wip", "r=alice,bob wip"),
        ("r=alice,bob wip", "r=alice,bob wip"),
        ("alice bob wip", "r=alice,bob wip"),
        ("wip alice bob", "r=alice,bob wip"),
        ("bob topic=abc alice", "r=bob,alice topic=abc"),
        ('bob topic "abc" alice', "r=bob,alice topic=abc"),
        ("alice private wip", "r=alice wip private"),
        ("alice lazy", "r=alice lazy"),
        ("alice overwrite", "r=alice overwrite"),
        ("publish-drafts alice lazy", "r=alice lazy publish-drafts"),
    ],
)
def test_canonical_examples(buf: str, expected: str) -> None:
    assert format_canonical(_state(buf)) == expected


def test_canonical_quotes_topic_with_space() -> None:
    s = PushLineState(topic="a b")
    assert format_canonical(s) == "topic='a b'"


def test_canonical_empty_state_is_empty_string() -> None:
    assert format_canonical(PushLineState()) == ""


def test_canonical_round_trip_through_parser() -> None:
    s1 = _state("alice bob topic=t1 wip private lazy")
    can = format_canonical(s1)
    s2 = _state(can)
    assert s2 == s1


# ---------- tokenizer offsets and spans ----------


def test_tokenizer_offsets_match_substring() -> None:
    buf = "  alice bob  wip"
    tokens, _ = tokenize(buf)
    for tok in tokens:
        assert buf[tok.start : tok.end] == tok.text or tok.quoted


def test_minus_span_records_minus_and_target_separately() -> None:
    res = parse("-alice")
    kinds = [s.kind for s in res.spans]
    assert "minus" in kinds
    assert "reviewer" in kinds


def test_keyword_spans_are_classified() -> None:
    res = parse("wip private topic=abc r=alice lazy overwrite push")
    kinds = {s.kind for s in res.spans}
    expected = {
        "keyword_wip",
        "keyword_private",
        "keyword_topic",
        "keyword_r",
        "keyword_lazy",
        "keyword_overwrite",
        "keyword_push",
        "topic_value",
        "reviewer",
        "equals",
    }
    assert expected.issubset(kinds)


def test_diagnostic_dataclass_is_hashable_via_fields() -> None:
    d = Diagnostic("error", "x", 0, 1)
    assert d.severity == "error"
    assert (d.start, d.end) == (0, 1)


@pytest.mark.parametrize(
    ("line", "answer"),
    [
        ("y", "push"),
        ("YES", "push"),
        ("n", "cancel"),
        ("no", "cancel"),
        ("q", "cancel"),
        ("quit", "cancel"),
        ("?", "help"),
        ("help", "help"),
        ("publish-drafts y", "push"),
        ("r=alice n", "cancel"),
    ],
)
def test_answer_words_answer_the_prompt_and_name_no_reviewer(line: str, answer: str) -> None:
    """Typed at the push prompt, y/n/? are answers: before, `y` pushed with %r=y and `?` broke the refspec."""
    res = parse(line)
    assert res.state.answer == answer
    assert "y" not in res.state.reviewers and "n" not in res.state.reviewers
    assert format_canonical(res.state) == format_canonical(parse(line.rpartition(" ")[0]).state)


def test_a_reviewer_named_like_an_answer_is_written_with_r() -> None:
    res = parse("r=y,n -n")
    assert res.state.reviewers == ["y"]
    assert res.state.answer is None


def test_contradicting_answers_are_an_error() -> None:
    res = parse("y n")
    assert not res.valid_for_apply
