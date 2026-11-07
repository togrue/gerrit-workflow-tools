"""prompt_toolkit glue for the Gerrit push options input line.

The pure parsing/formatting logic lives in :mod:`push_input_line`. This module
wires it into a prompt_toolkit ``PromptSession`` with a syntax-highlighting
lexer, live validation, keyword/reviewer completion, persisted push-options
history, and Up/Down recall (:class:`HistoryCursor`) when no completion menu is open.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.validation import ValidationError, Validator

from gerrit_workflow_tools.cli_style import is_color_enabled
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, branch_reviewers, resolve_push_reviewers
from gerrit_workflow_tools.push_input_line import (
    KW_LAZY,
    KW_OVERWRITE,
    KW_PRIVATE,
    KW_PUSH,
    KW_R,
    KW_TOPIC,
    KW_WIP,
    ParseResult,
    SpanKind,
    apply_session_strategy,
    parse,
)
from gerrit_workflow_tools.push_options_history import (
    HistoryCursor,
    load_push_options_history,
    push_options_history_identity,
)
from gerrit_workflow_tools.reviewer_catalog import ReviewerCatalog


_STYLE_BY_KIND: dict[SpanKind, str] = {
    "minus": "fg:ansiyellow",
    "keyword_r": "fg:ansigreen bold",
    "keyword_topic": "fg:ansicyan",
    "keyword_wip": "fg:#a05a2c bold",
    "keyword_private": "fg:#9b30ff bold",
    "keyword_push": "fg:#4a7ebb bold",
    "keyword_lazy": "fg:#4a7ebb bold",
    "keyword_overwrite": "fg:#4a7ebb bold",
    "equals": "fg:ansiwhite",
    "comma": "fg:ansiwhite",
    "reviewer": "fg:ansibrightgreen",
    "topic_value": "fg:ansicyan",
    "quoted": "fg:ansicyan",
    "error": "fg:ansired bold",
    "unknown": "fg:ansired",
    "whitespace": "",
}


def _style_for(kind: SpanKind) -> str:
    if not is_color_enabled():
        return ""
    return _STYLE_BY_KIND.get(kind, "")


class PushOptionsLexer(Lexer):
    """Color the buffer using spans from :func:`push_input_line.parse`."""

    def lex_document(self, document: Document):  # type: ignore[override]
        text = document.text

        def get_line(line_no: int) -> FormattedText:
            if line_no != 0:
                return FormattedText([("", "")])
            res = parse(text)
            return FormattedText(_fragments_from_spans(text, res))

        return get_line


def _fragments_from_spans(text: str, res: ParseResult) -> list[tuple[str, str]]:
    """Turn classified spans + remaining gaps into ``(style, text)`` fragments."""
    fragments: list[tuple[str, str]] = []
    cursor = 0
    for span in res.spans:
        if span.start > cursor:
            fragments.append(("", text[cursor : span.start]))
        fragments.append((_style_for(span.kind), text[span.start : span.end]))
        cursor = span.end
    if cursor < len(text):
        fragments.append(("", text[cursor:]))
    return fragments


class PushOptionsValidator(Validator):
    """Block accept on hard parse errors; warnings pass through."""

    def validate(self, document: Document) -> None:  # type: ignore[override]
        res = parse(document.text)
        for diag in res.diagnostics:
            if diag.severity == "error":
                raise ValidationError(message=diag.message, cursor_position=diag.start)


class PushOptionsCompleter(Completer):
    """Complete reserved keywords and known reviewer names at the cursor word."""

    def __init__(self, reviewer_seeds: Iterable[str] = (), *, catalog: ReviewerCatalog | None = None):
        seen: set[str] = set()
        ordered: list[str] = []
        for name in reviewer_seeds:
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        self._reviewer_seeds = ordered
        self._catalog = catalog

    def get_completions(self, document: Document, _complete_event: CompleteEvent):  # type: ignore[override]
        word = document.get_word_before_cursor(WORD=True)
        raw_word = word.lstrip("-")
        candidates: list[tuple[str, str]] = [
            (f"{KW_R}=", "reviewer list"),
            (KW_TOPIC + "=", "change topic"),
            (KW_WIP, "mark as WIP"),
            (KW_PRIVATE, "mark as private"),
            (KW_PUSH, "reviewers via %r= on push"),
            (KW_LAZY, "reviewers via REST only when missing"),
            (KW_OVERWRITE, "reviewers via REST on all changes"),
        ]
        seed_names = list(self._reviewer_seeds)
        if self._catalog is not None:
            for name in self._catalog.completion_candidates():
                if name and name not in seed_names:
                    seed_names.append(name)
        candidates.extend((name, "reviewer") for name in seed_names)
        if self._catalog is not None:
            seen_lower = {n.lower() for n in seed_names}
            for name in self._catalog.complete_prefix(raw_word):
                low = name.lower()
                if low not in seen_lower:
                    seen_lower.add(low)
                    candidates.append((name, "reviewer"))
        prefix = raw_word.lower()
        for value, meta in candidates:
            if not prefix or value.lower().startswith(prefix):
                yield Completion(value, start_position=-len(raw_word), display_meta=meta)


def should_navigate_push_options_history(buffer: Buffer) -> bool:
    """True when Up/Down should walk push-options history: no completion menu is open."""
    complete_state = buffer.complete_state
    return complete_state is None or not complete_state.completions


@Condition
def _push_options_history_navigation() -> bool:
    try:
        return should_navigate_push_options_history(get_app().current_buffer)
    except (RuntimeError, AttributeError):
        return False


def _history_bindings(cursor: HistoryCursor) -> KeyBindings:
    bindings = KeyBindings()

    def _show(buffer: Buffer, text: str) -> None:
        buffer.document = Document(text, cursor_position=len(text))

    @bindings.add("up", filter=_push_options_history_navigation)
    def _history_up(event):  # type: ignore[no-untyped-def]
        _show(event.current_buffer, cursor.up(event.current_buffer.text))

    @bindings.add("down", filter=_push_options_history_navigation)
    def _history_down(event):  # type: ignore[no-untyped-def]
        _show(event.current_buffer, cursor.down(event.current_buffer.text))

    return bindings


def _bottom_toolbar(text: str, catalog: ReviewerCatalog | None = None) -> FormattedText | None:
    res = parse(text)
    errors = [d for d in res.diagnostics if d.severity == "error"]
    warnings = [d for d in res.diagnostics if d.severity == "warning"]
    if errors:
        msg = errors[0].message
        extra = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return FormattedText([("fg:ansired", f"error: {msg}{extra}")])
    if catalog is not None:
        validation = catalog.validate_state(res.state)
        if validation.issues:
            msg = validation.issues[0].message
            extra = f" (+{len(validation.issues) - 1} more)" if len(validation.issues) > 1 else ""
            return FormattedText([("fg:ansired", f"reviewer: {msg}{extra}")])
    if warnings:
        msg = warnings[0].message
        extra = f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""
        return FormattedText([("fg:ansiyellow", f"warning: {msg}{extra}")])
    if catalog is not None:
        hint = catalog.default_toolbar_hint()
    else:
        hint = "keywords: r= topic= wip private push lazy overwrite"
    return FormattedText([("fg:#808080", hint)])


def prompt_push_options_line(
    *,
    default: str | None = None,
    reviewer_seeds: Iterable[str] = (),
    message: str = "Push options: ",
    cwd: Path | None = None,
    settings: Settings,
    change_id_hint: str | None = None,
    web_base: str | None = None,
    project: str | None = None,
    session_strategy: str | None = None,
) -> ParseResult:
    """Show the prompt and return the parsed result for the accepted line.

    The line starts as ``default`` (empty unless given): history is only a suggestion,
    reached with Up/Down, and an edited entry becomes the current line. History is keyed by
    ``(web_base, project)``; when identity is missing it is neither loaded nor saved. When
    ``session_strategy`` is a non-default CLI strategy, it is merged into the visible
    line and Up/Down entries for this session.
    The prompt saves nothing: ``ger push`` records a line once the push has succeeded.
    """
    stored = load_push_options_history(web_base=web_base, project=project)
    display_history = [apply_session_strategy(entry, session_strategy) for entry in stored]
    initial = apply_session_strategy(default or "", session_strategy)
    seed_list = [s for s in reviewer_seeds if s]

    def _invalidate_prompt() -> None:
        with contextlib.suppress(RuntimeError, AttributeError):
            get_app().invalidate()

    catalog = ReviewerCatalog.from_runtime(
        cwd=cwd,
        settings=settings,
        reviewer_seeds=seed_list,
        change_id_hint=change_id_hint,
        on_update=_invalidate_prompt,
    )
    completion_candidates = catalog.completion_candidates()
    session: PromptSession[str] = PromptSession(
        message=message,
        lexer=PushOptionsLexer(),
        validator=PushOptionsValidator(),
        validate_while_typing=False,
        completer=PushOptionsCompleter(completion_candidates, catalog=catalog),
        complete_while_typing=True,
        key_bindings=_history_bindings(HistoryCursor(display_history, initial)),
        bottom_toolbar=lambda: _bottom_toolbar(session.default_buffer.text, catalog),
    )
    try:
        raw = session.prompt(default=initial)
    finally:
        catalog.close()
    return parse(raw)


def prompt_branch_push_options(
    cwd: Path | None = None,
    branch: str | None = None,
    *,
    settings: Settings,
    change_id_hint: str | None = None,
    reviewer_flag_segments: list[str] | None = None,
    session_strategy: ReviewerStrategy | None = None,
    message: str = "Push options: ",
    prefill: bool = True,
) -> ParseResult:
    """Open the highlighted push-options input line for *branch*, history keyed by the stack's project.

    With *prefill* the line starts with the reviewers ``--reviewers`` / the registry /
    ``branch.*.gerritReviewers`` give; without it, the line is empty.
    """
    seeds = branch_reviewers(branch, settings=settings) if branch is not None else []
    web_base, project = push_options_history_identity(cwd, settings=settings)
    default = ""
    if prefill and cwd is not None and branch is not None:
        reviewers = resolve_push_reviewers(cwd, branch, reviewer_flag_segments or [], settings=settings)
        default = f"r={','.join(reviewers)}" if reviewers else ""
    return prompt_push_options_line(
        reviewer_seeds=seeds,
        message=message,
        cwd=cwd,
        settings=settings,
        change_id_hint=change_id_hint,
        web_base=web_base,
        project=project,
        default=default,
        session_strategy=session_strategy.value if session_strategy is not None else None,
    )
