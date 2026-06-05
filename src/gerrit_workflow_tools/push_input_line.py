"""Pure parsing/formatting for the interactive Gerrit push options line.

The interactive ``ger push`` reviewer prompt accepts a single line that mixes
reviewers, a topic, flags, and reviewer-assignment strategy keywords. This module turns the
raw buffer into a structured :class:`PushLineState` with diagnostics and
classified spans for highlighting, and renders the canonical form back out.

Grammar (best effort, case-insensitive keywords)::

    line     := element (WS+ element)*
    element  := removal | rclause | topic | flag | strategy | reviewer
    removal  := '-' (keyword | reviewer)
    rclause  := ('r' '=' list) | ('r' WS+ list)        # list = name (',' name)*
    topic    := ('topic' '=' value) | ('topic' WS+ value)
    flag     := 'wip' | 'private'
    strategy := 'push' | 'lazy' | 'overwrite'
    reviewer := bare-word that is not a reserved keyword

Quoted strings (``"..."`` / ``'...'``) are accepted as topic values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote

ReviewerStrategy = Literal["push", "lazy", "overwrite"]

KW_R = "r"
KW_TOPIC = "topic"
KW_WIP = "wip"
KW_PRIVATE = "private"
KW_PUSH = "push"
KW_LAZY = "lazy"
KW_OVERWRITE = "overwrite"

SpanKind = Literal[
    "whitespace",
    "minus",
    "keyword_r",
    "keyword_topic",
    "keyword_wip",
    "keyword_private",
    "keyword_push",
    "keyword_lazy",
    "keyword_overwrite",
    "equals",
    "comma",
    "reviewer",
    "topic_value",
    "quoted",
    "error",
    "unknown",
]

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Token:
    """One whitespace-separated segment of the input line."""

    text: str
    start: int
    end: int
    quoted: bool = False


@dataclass(frozen=True)
class Span:
    """Classified character range used by the lexer for highlighting."""

    kind: SpanKind
    start: int
    end: int


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    start: int
    end: int


@dataclass
class PushLineState:
    """Effective push-options state derived from a parsed line."""

    reviewers: list[str] = field(default_factory=list)
    topic: str | None = None
    wip: bool = False
    private: bool = False
    strategy: ReviewerStrategy = "push"

    def add_reviewer(self, name: str) -> None:
        """Append ``name`` if non-empty and not already present."""
        if name and name not in self.reviewers:
            self.reviewers.append(name)

    def remove_reviewer(self, name: str) -> None:
        """Remove ``name`` if present (no-op otherwise)."""
        if name in self.reviewers:
            self.reviewers.remove(name)


@dataclass
class ParseResult:
    """Output of :func:`parse`: state plus diagnostics and classified spans."""

    state: PushLineState
    diagnostics: list[Diagnostic]
    spans: list[Span]

    @property
    def valid_for_apply(self) -> bool:
        """``True`` when no diagnostic has severity ``error``."""
        return not any(d.severity == "error" for d in self.diagnostics)


def tokenize(buf: str) -> tuple[list[Token], list[Diagnostic]]:
    """Split ``buf`` into whitespace-separated segments, honoring ``"`` / ``'`` quotes.

    Inside quotes, whitespace is preserved as part of the segment text and the
    surrounding quote characters are stripped. An unclosed quote yields an
    error diagnostic; the partial segment is still emitted so the rest of the
    line can be parsed.
    """
    tokens: list[Token] = []
    diags: list[Diagnostic] = []
    i = 0
    n = len(buf)
    while i < n:
        ch = buf[i]
        if ch.isspace():
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            start = i
            i += 1
            text_chars: list[str] = []
            closed = False
            while i < n:
                if buf[i] == quote:
                    closed = True
                    i += 1
                    break
                text_chars.append(buf[i])
                i += 1
            end = i
            tokens.append(Token("".join(text_chars), start, end, quoted=True))
            if not closed:
                diags.append(Diagnostic("error", f"Unclosed {quote} quote", start, end))
            continue
        start = i
        text_chars = []
        while i < n and not buf[i].isspace() and buf[i] not in ('"', "'"):
            text_chars.append(buf[i])
            i += 1
        tokens.append(Token("".join(text_chars), start, i, quoted=False))
    return tokens, diags


def _split_reviewers(raw: str, base_offset: int, spans: list[Span]) -> list[str]:
    """Split a comma-separated reviewer RHS, recording comma + reviewer spans."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        j = i
        while j < n and raw[j] != ",":
            j += 1
        name = raw[i:j].strip()
        if name:
            name_start = base_offset + i + (len(raw[i:j]) - len(raw[i:j].lstrip()))
            spans.append(Span("reviewer", name_start, name_start + len(name)))
            out.append(name)
        if j < n:
            spans.append(Span("comma", base_offset + j, base_offset + j + 1))
        i = j + 1
    return out


def _classify_keyword(word: str) -> SpanKind | None:
    low = word.lower()
    if low == KW_R:
        return "keyword_r"
    if low == KW_TOPIC:
        return "keyword_topic"
    if low == KW_WIP:
        return "keyword_wip"
    if low == KW_PRIVATE:
        return "keyword_private"
    if low == KW_PUSH:
        return "keyword_push"
    if low == KW_LAZY:
        return "keyword_lazy"
    if low == KW_OVERWRITE:
        return "keyword_overwrite"
    return None


def parse(buf: str) -> ParseResult:  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
    """Parse ``buf`` into a :class:`PushLineState` plus diagnostics and spans."""
    state = PushLineState()
    tokens, diags = tokenize(buf)
    spans: list[Span] = []
    topic_seen_at: tuple[int, int] | None = None

    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        text = tok.text
        if not text and not tok.quoted:
            i += 1
            continue

        if tok.quoted:
            spans.append(Span("error", tok.start, tok.end))
            diags.append(
                Diagnostic("error", "Unexpected quoted value (only `topic` accepts a quoted value)", tok.start, tok.end)
            )
            i += 1
            continue

        if text.startswith("-") and len(text) > 1:
            inner = text[1:]
            spans.append(Span("minus", tok.start, tok.start + 1))
            inner_start = tok.start + 1
            inner_end = tok.end
            kw = _classify_keyword(inner)
            if kw == "keyword_r":
                spans.append(Span("keyword_r", inner_start, inner_end))
                state.reviewers.clear()
            elif kw == "keyword_wip":
                spans.append(Span("keyword_wip", inner_start, inner_end))
                state.wip = False
            elif kw == "keyword_private":
                spans.append(Span("keyword_private", inner_start, inner_end))
                state.private = False
            elif kw == "keyword_topic":
                spans.append(Span("keyword_topic", inner_start, inner_end))
                state.topic = None
                topic_seen_at = None
            elif kw in ("keyword_push", "keyword_lazy", "keyword_overwrite"):
                spans.append(Span(kw, inner_start, inner_end))
                state.strategy = "push"
            else:
                spans.append(Span("reviewer", inner_start, inner_end))
                state.remove_reviewer(inner)
            i += 1
            continue

        if "=" in text:
            key, _eq, value = text.partition("=")
            key_low = key.lower()
            key_start = tok.start
            key_end = tok.start + len(key)
            eq_start = key_end
            eq_end = eq_start + 1
            value_start = eq_end
            value_end = tok.end
            if key_low == KW_R:
                spans.append(Span("keyword_r", key_start, key_end))
                spans.append(Span("equals", eq_start, eq_end))
                if not value:
                    diags.append(Diagnostic("error", "Empty `r=` reviewer list", tok.start, tok.end))
                else:
                    for name in _split_reviewers(value, value_start, spans):
                        state.add_reviewer(name)
                i += 1
                continue
            if key_low == KW_TOPIC:
                spans.append(Span("keyword_topic", key_start, key_end))
                spans.append(Span("equals", eq_start, eq_end))
                if not value:
                    diags.append(Diagnostic("error", "Empty `topic=` value", tok.start, tok.end))
                else:
                    spans.append(Span("topic_value", value_start, value_end))
                    if topic_seen_at is not None and state.topic not in (None, value):
                        diags.append(Diagnostic("warning", "Topic redefined; using last value", tok.start, tok.end))
                    state.topic = value
                    topic_seen_at = (tok.start, tok.end)
                i += 1
                continue
            spans.append(Span("error", tok.start, tok.end))
            diags.append(Diagnostic("error", f"Unknown key `{key}=`", tok.start, tok.end))
            i += 1
            continue

        kw = _classify_keyword(text)
        if kw == "keyword_r":
            spans.append(Span("keyword_r", tok.start, tok.end))
            nxt = tokens[i + 1] if i + 1 < n else None
            if nxt is None or nxt.text.startswith("-") or _classify_keyword(nxt.text) is not None:
                diags.append(Diagnostic("error", "`r` requires a comma-separated reviewer list", tok.start, tok.end))
                i += 1
                continue
            for name in _split_reviewers(nxt.text, nxt.start, spans):
                state.add_reviewer(name)
            i += 2
            continue
        if kw == "keyword_topic":
            spans.append(Span("keyword_topic", tok.start, tok.end))
            nxt = tokens[i + 1] if i + 1 < n else None
            if nxt is None:
                diags.append(Diagnostic("error", "`topic` requires a value", tok.start, tok.end))
                i += 1
                continue
            if not nxt.quoted and (nxt.text.startswith("-") or _classify_keyword(nxt.text) is not None):
                diags.append(Diagnostic("error", "`topic` requires a value", tok.start, tok.end))
                i += 1
                continue
            value = nxt.text
            spans.append(Span("topic_value" if not nxt.quoted else "quoted", nxt.start, nxt.end))
            if topic_seen_at is not None and state.topic not in (None, value):
                diags.append(Diagnostic("warning", "Topic redefined; using last value", tok.start, nxt.end))
            state.topic = value
            topic_seen_at = (tok.start, nxt.end)
            i += 2
            continue
        if kw == "keyword_wip":
            spans.append(Span("keyword_wip", tok.start, tok.end))
            state.wip = True
            i += 1
            continue
        if kw == "keyword_private":
            spans.append(Span("keyword_private", tok.start, tok.end))
            state.private = True
            i += 1
            continue
        if kw == "keyword_push":
            spans.append(Span("keyword_push", tok.start, tok.end))
            state.strategy = "push"
            i += 1
            continue
        if kw == "keyword_lazy":
            spans.append(Span("keyword_lazy", tok.start, tok.end))
            state.strategy = "lazy"
            i += 1
            continue
        if kw == "keyword_overwrite":
            spans.append(Span("keyword_overwrite", tok.start, tok.end))
            state.strategy = "overwrite"
            i += 1
            continue

        spans.append(Span("reviewer", tok.start, tok.end))
        state.add_reviewer(text)
        i += 1

    diags.extend(_validate(state))
    spans.sort(key=lambda s: (s.start, s.end))
    return ParseResult(state=state, diagnostics=diags, spans=spans)


_REVIEWER_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+@")


def _validate(state: PushLineState) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for name in state.reviewers:
        if any(c not in _REVIEWER_NAME_OK for c in name):
            out.append(Diagnostic("warning", f"Reviewer `{name}` has unusual characters", 0, 0))
    return out


def _needs_quoting(value: str) -> bool:
    return any(c.isspace() or c in ('"', "'", "=") for c in value) or value == ""


def _quote(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def format_canonical(state: PushLineState) -> str:
    """Render ``state`` as a stable, ergonomic line.

    Reviewers come first as ``r=alice,bob``, then optional ``topic=…``, then
    flags (``wip private``), and finally a non-default strategy keyword
    (``lazy``/``overwrite``).
    """
    parts: list[str] = []
    if state.reviewers:
        parts.append(f"r={','.join(state.reviewers)}")
    if state.topic is not None:
        value = _quote(state.topic) if _needs_quoting(state.topic) else state.topic
        parts.append(f"topic={value}")
    if state.wip:
        parts.append("wip")
    if state.private:
        parts.append("private")
    if state.strategy != "push":
        parts.append(state.strategy)
    return " ".join(parts)


def refspec_options(state: PushLineState, strategy: ReviewerStrategy) -> list[str]:
    """Return the ``%`` segments to append to ``tip:refs/for/<branch>``.

    Reviewers are only emitted when ``strategy == "push"``; ``lazy`` and
    ``overwrite`` apply reviewers via REST after the push completes. ``wip``,
    ``private`` and ``topic`` are always emitted because Gerrit only honors
    them as magic ref options.
    """
    parts: list[str] = []
    if strategy == "push":
        parts.extend(f"r={r}" for r in state.reviewers)
    if state.wip:
        parts.append("wip")
    if state.private:
        parts.append("private")
    if state.topic:
        parts.append(f"topic={quote(state.topic, safe='')}")
    return parts
