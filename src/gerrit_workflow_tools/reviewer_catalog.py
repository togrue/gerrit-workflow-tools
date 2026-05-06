"""Gerrit-backed reviewer discovery and soft validation for prompts."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.gerrit_client import GerritApiError, GerritClient, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.reviewer import gerrit_credentials_configured
from gerrit_workflow_tools.core.reviewer_completion import (
    account_query_exact_lookup,
    fetch_reviewer_slugs_for_prefix,
    fetch_suggested_reviewer_slugs,
    is_reviewer_login_token,
    slug_from_suggest_or_account_row,
)
from gerrit_workflow_tools.push_input_line import PushLineState

_DEFAULT_STATUS_HINT = "keywords: r= topic= wip private push lazy overwrite"
_QUERY_DEBOUNCE_SECONDS = 0.35


@dataclass(frozen=True)
class ReviewerValidationIssue:
    reviewer: str
    message: str


@dataclass(frozen=True)
class ReviewerValidation:
    issues: list[ReviewerValidationIssue]
    pending_checks: bool = False


class ReviewerCatalog:
    """Holds completion candidates and soft reviewer validation state."""

    def __init__(
        self,
        *,
        client: GerritClient | None,
        status_note: str | None = None,
        candidates: list[str] | None = None,
        change_id_hint: str | None = None,
    ) -> None:
        self._client = client
        self.status_note = status_note
        self._change_id_hint = change_id_hint
        self._candidates: list[str] = []
        self._candidate_seen: set[str] = set()
        self._validation_cache: dict[str, Literal["ok", "unknown", "ambiguous"]] = {}
        self._prefix_completion_cache: dict[str, list[str]] = {}
        self._next_allowed_query_at = 0.0
        if candidates:
            self.add_candidates(candidates)

    @classmethod
    def from_runtime(
        cls,
        *,
        cwd: Path | None,
        reviewer_seeds: list[str],
        change_id_hint: str | None,
    ) -> ReviewerCatalog:
        """Build a catalog; gracefully degrade to local seeds when unavailable."""
        if cwd is None:
            return cls(client=None, status_note=None, candidates=reviewer_seeds, change_id_hint=None)
        if not _gerrit_creds_configured(cwd):
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.user + token/password).",
                candidates=reviewer_seeds,
                change_id_hint=None,
            )
        try:
            web_base = resolve_gerrit_web_base(cwd)
        except ValueError:
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.webUrl).",
                candidates=reviewer_seeds,
                change_id_hint=None,
            )

        client = GerritClient(web_base, cwd=str(cwd))
        catalog = cls(
            client=client,
            status_note=None,
            candidates=reviewer_seeds,
            change_id_hint=change_id_hint,
        )

        project = resolve_gerrit_project_name(cwd)
        if project:
            try:
                plugin_rows = client.get_plugin_project_reviewers(project)
            except GerritApiError:
                plugin_rows = None
            if plugin_rows:
                catalog.add_candidates(
                    [slug for slug in (slug_from_suggest_or_account_row(r) for r in plugin_rows) if slug]
                )

        if change_id_hint:
            with contextlib.suppress(GerritApiError):
                catalog.add_candidates(fetch_suggested_reviewer_slugs(client, change_id_hint, n=100))
        return catalog

    def add_candidates(self, names: list[str]) -> None:
        for name in names:
            s = name.strip()
            if not s:
                continue
            low = s.lower()
            if low in self._candidate_seen:
                continue
            self._candidate_seen.add(low)
            self._candidates.append(s)

    def completion_candidates(self) -> list[str]:
        return list(self._candidates)

    def complete_prefix(self, prefix: str) -> list[str]:
        """Return reviewer login names matching *prefix* for tab completion.

        The initial candidate list is capped/biased by Gerrit's top-N suggestions for the change;
        this asks Gerrit again with the typed prefix so names like ``bal`` appear when ``bea``
        does but ranked suggestions omit them.
        """
        token = prefix.lstrip("-").strip()
        if not token:
            return []
        if not is_reviewer_login_token(token):
            return []
        key = token.lower()
        if key in self._prefix_completion_cache:
            return self._prefix_completion_cache[key]

        if self._client is None:
            self._prefix_completion_cache[key] = []
            return []

        try:
            out = fetch_reviewer_slugs_for_prefix(
                self._client,
                change_id=self._change_id_hint,
                token=token,
                n=100,
            )
        except GerritApiError:
            self._prefix_completion_cache[key] = []
            return []

        self._prefix_completion_cache[key] = out
        return out

    def validate_state(self, state: PushLineState) -> ReviewerValidation:
        """Return soft validation issues and update one unresolved reviewer lazily."""
        if not state.reviewers:
            return ReviewerValidation(issues=[])

        pending = False
        issues: list[ReviewerValidationIssue] = []
        for reviewer in state.reviewers:
            key = reviewer.strip().lower()
            if not key:
                continue
            status = self._validation_cache.get(key)
            if status is None:
                pending = True
                continue
            if status == "unknown":
                issues.append(
                    ReviewerValidationIssue(
                        reviewer=reviewer,
                        message=f"Gerrit could not resolve reviewer `{reviewer}`.",
                    )
                )
            elif status == "ambiguous":
                issues.append(
                    ReviewerValidationIssue(
                        reviewer=reviewer,
                        message=f"Gerrit resolves reviewer `{reviewer}` ambiguously.",
                    )
                )

        # Debounced: resolve at most one missing reviewer per call.
        if pending and self._client is not None and time.monotonic() >= self._next_allowed_query_at:
            self._next_allowed_query_at = time.monotonic() + _QUERY_DEBOUNCE_SECONDS
            for reviewer in state.reviewers:
                key = reviewer.strip().lower()
                if not key or key in self._validation_cache:
                    continue
                self._validation_cache[key] = self._resolve_reviewer_status(reviewer)
                break

        pending_after = any(r.strip().lower() not in self._validation_cache for r in state.reviewers if r.strip())
        issues_after = self._issues_from_cache(state.reviewers)
        return ReviewerValidation(issues=issues_after, pending_checks=pending_after)

    def default_toolbar_hint(self) -> str:
        if self.status_note:
            return f"{_DEFAULT_STATUS_HINT}  ·  {self.status_note}"
        return _DEFAULT_STATUS_HINT

    def _issues_from_cache(self, reviewers: list[str]) -> list[ReviewerValidationIssue]:
        out: list[ReviewerValidationIssue] = []
        for reviewer in reviewers:
            key = reviewer.strip().lower()
            if not key:
                continue
            status = self._validation_cache.get(key)
            if status == "unknown":
                out.append(
                    ReviewerValidationIssue(
                        reviewer=reviewer,
                        message=f"Gerrit could not resolve reviewer `{reviewer}`.",
                    )
                )
            elif status == "ambiguous":
                out.append(
                    ReviewerValidationIssue(
                        reviewer=reviewer,
                        message=f"Gerrit resolves reviewer `{reviewer}` ambiguously.",
                    )
                )
        return out

    def _resolve_reviewer_status(self, reviewer: str) -> Literal["ok", "unknown", "ambiguous"]:
        if self._client is None:
            return "ok"
        q = account_query_exact_lookup(reviewer)
        try:
            rows = self._client.query_accounts(q, n=8)
        except GerritApiError:
            # Keep prompt usable when Gerrit is temporarily unavailable.
            self.status_note = "Gerrit reviewer validation unavailable (request failed)."
            self._client = None
            return "ok"
        if not rows:
            return "unknown"
        low = reviewer.strip().lower()
        exact = 0
        for row in rows:
            slug = slug_from_suggest_or_account_row(row)
            if slug and slug.lower() == low:
                exact += 1
        if exact > 1:
            return "ambiguous"
        if exact == 1:
            return "ok"
        if len(rows) > 1:
            return "ambiguous"
        return "ok"


def _gerrit_creds_configured(cwd: Path) -> bool:
    return gerrit_credentials_configured(cwd)
