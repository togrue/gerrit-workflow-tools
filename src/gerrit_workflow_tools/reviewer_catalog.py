"""Gerrit-backed reviewer discovery and soft validation for prompts."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.gerrit_client import GerritApiError, GerritClient, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.reviewer import account_slug_from_gerrit, gerrit_credentials_configured
from gerrit_workflow_tools.push_input_line import PushLineState

_DEFAULT_STATUS_HINT = "keywords: r= topic= wip private push lazy overwrite"
_QUERY_DEBOUNCE_SECONDS = 0.35

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _account_slug(account: dict[str, object]) -> str | None:
    return account_slug_from_gerrit(account)


def _entry_to_slug(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    account = entry.get("account")
    if isinstance(account, dict):
        slug = _account_slug(account)
        if slug:
            return slug
    return _account_slug(entry)


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
    ) -> None:
        self._client = client
        self.status_note = status_note
        self._candidates: list[str] = []
        self._candidate_seen: set[str] = set()
        self._validation_cache: dict[str, Literal["ok", "unknown", "ambiguous"]] = {}
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
            return cls(client=None, status_note=None, candidates=reviewer_seeds)
        if not _gerrit_creds_configured(cwd):
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.user + token/password).",
                candidates=reviewer_seeds,
            )
        try:
            web_base = resolve_gerrit_web_base(cwd)
        except ValueError:
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.webUrl).",
                candidates=reviewer_seeds,
            )

        client = GerritClient(web_base, cwd=str(cwd))
        catalog = cls(client=client, status_note=None, candidates=reviewer_seeds)

        project = resolve_gerrit_project_name(cwd)
        if project:
            try:
                plugin_rows = client.get_plugin_project_reviewers(project)
            except GerritApiError:
                plugin_rows = None
            if plugin_rows:
                catalog.add_candidates([slug for slug in (_entry_to_slug(r) for r in plugin_rows) if slug])

        if change_id_hint:
            try:
                rows = client.suggest_change_reviewers(change_id_hint, n=25)
            except GerritApiError:
                rows = []
            if rows:
                catalog.add_candidates([slug for slug in (_entry_to_slug(r) for r in rows) if slug])
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
        q = _account_query_for_reviewer(reviewer)
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
            slug = _entry_to_slug(row)
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


def _account_query_for_reviewer(reviewer: str) -> str:
    s = reviewer.strip()
    if "@" in s:
        return f"email:{s}"
    if _USERNAME_RE.fullmatch(s):
        return f"username:{s}"
    return s
