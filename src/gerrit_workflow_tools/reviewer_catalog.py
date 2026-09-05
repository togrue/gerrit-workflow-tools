"""Gerrit-backed reviewer discovery and soft validation for prompts.

All Gerrit REST for the push-options prompt runs on a background worker with a
shared start-to-start rate limit. The UI thread only reads caches and enqueues
work (see ADR-0006).
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError, resolve_stack_context
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, HttpGerritRest, resolve_gerrit_web_base
from gerrit_workflow_tools.core.reviewer import gerrit_credentials_configured
from gerrit_workflow_tools.core.reviewer_completion import (
    ReviewerLookup,
    account_query_exact_lookup,
    fetch_reviewer_slugs_for_prefix,
    fetch_suggested_reviewer_slugs,
    is_reviewer_login_token,
    slug_from_suggest_or_account_row,
)
from gerrit_workflow_tools.push_input_line import PushLineState


_DEFAULT_STATUS_HINT = "keywords: r= topic= wip private push lazy overwrite"
_REQUEST_INTERVAL_SECONDS = 0.5

_JobKind = Literal["warmup_plugin", "warmup_suggest", "complete", "validate"]


@dataclass(frozen=True)
class ReviewerValidationIssue:
    reviewer: str
    message: str


@dataclass(frozen=True)
class ReviewerValidation:
    issues: list[ReviewerValidationIssue]
    pending_checks: bool = False


@dataclass(frozen=True)
class _Job:
    kind: _JobKind
    key: str
    generation: int
    token: str = ""


class ReviewerCatalog:
    """Holds completion candidates and soft reviewer validation state."""

    def __init__(
        self,
        *,
        client: ReviewerLookup | None,
        status_note: str | None = None,
        candidates: list[str] | None = None,
        change_id_hint: str | None = None,
        project: str | None = None,
        on_update: Callable[[], None] | None = None,
        start_worker: bool = True,
    ) -> None:
        self._client = client
        self.status_note = status_note
        self._change_id_hint = change_id_hint
        self._project = project
        self._on_update = on_update
        self._candidates: list[str] = []
        self._candidate_seen: set[str] = set()
        self._validation_cache: dict[str, Literal["ok", "unknown", "ambiguous"]] = {}
        self._prefix_completion_cache: dict[str, list[str]] = {}

        self._lock = threading.Lock()
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._next_slot_at = 0.0
        self._complete_generation: dict[str, int] = {}
        self._validate_generation: dict[str, int] = {}
        self._complete_tokens: dict[str, str] = {}
        self._inflight_complete: set[str] = set()
        self._inflight_validate: set[str] = set()
        self._queued_complete: set[str] = set()
        self._queued_validate: set[str] = set()
        self._warmup_plugin_queued = False
        self._warmup_suggest_queued = False
        self._outstanding = 0
        self._closed = False
        self._idle = threading.Event()
        self._idle.set()
        self._worker: threading.Thread | None = None
        if candidates:
            self.add_candidates(candidates)
        if start_worker and client is not None:
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="ger-reviewer-catalog",
                daemon=True,
            )
            self._worker.start()

    @classmethod
    def from_runtime(
        cls,
        *,
        cwd: Path | None,
        settings: Settings,
        reviewer_seeds: list[str],
        change_id_hint: str | None,
        on_update: Callable[[], None] | None = None,
    ) -> ReviewerCatalog:
        """Build a catalog; Gerrit REST warmup runs in the background."""
        if cwd is None:
            return cls(client=None, status_note=None, candidates=reviewer_seeds, change_id_hint=None)
        if not _gerrit_creds_configured(settings):
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.user + token/password).",
                candidates=reviewer_seeds,
                change_id_hint=None,
            )
        try:
            web_base = resolve_gerrit_web_base(settings)
        except ValueError:
            return cls(
                client=None,
                status_note="Gerrit reviewer validation unavailable (missing gerrit.webUrl).",
                candidates=reviewer_seeds,
                change_id_hint=None,
            )

        client = HttpGerritRest.from_settings(web_base, settings)
        try:
            stack = resolve_stack_context(cwd, settings=settings)
            project = stack.project
        except ChangeResolutionError:
            project = None

        catalog = cls(
            client=client,
            status_note=None,
            candidates=reviewer_seeds,
            change_id_hint=change_id_hint,
            project=project,
            on_update=on_update,
        )
        catalog._enqueue_warmup()
        return catalog

    def set_on_update(self, on_update: Callable[[], None] | None) -> None:
        """Install a UI invalidate callback (safe to call after prompt session starts)."""
        self._on_update = on_update

    def close(self) -> None:
        """Stop the background worker. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._jobs.put(None)
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        """Block until no queued/in-flight jobs remain. Returns False on timeout (tests)."""
        return self._idle.wait(timeout=timeout)

    def add_candidates(self, names: list[str]) -> None:
        with self._lock:
            self._add_candidates_unlocked(names)

    def _add_candidates_unlocked(self, names: list[str]) -> None:
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
        with self._lock:
            return list(self._candidates)

    def complete_prefix(self, prefix: str) -> list[str]:
        """Return cached Gerrit matches for *prefix*; enqueue a fetch on miss.

        Never blocks on REST. Local seeds are completed by the prompt completer.
        """
        token = prefix.lstrip("-").strip()
        if not token:
            return []
        if not is_reviewer_login_token(token):
            return []
        key = token.lower()
        with self._lock:
            if key in self._prefix_completion_cache:
                return list(self._prefix_completion_cache[key])
            if self._client is None:
                self._prefix_completion_cache[key] = []
                return []
            self._enqueue_complete_unlocked(key, token)
        return []

    def validate_state(self, state: PushLineState) -> ReviewerValidation:
        """Return soft validation from cache; enqueue at most one unresolved reviewer."""
        if not state.reviewers:
            return ReviewerValidation(issues=[])

        with self._lock:
            pending = False
            for reviewer in state.reviewers:
                key = reviewer.strip().lower()
                if not key:
                    continue
                if key not in self._validation_cache:
                    pending = True
            if pending and self._client is not None:
                for reviewer in state.reviewers:
                    key = reviewer.strip().lower()
                    if not key or key in self._validation_cache:
                        continue
                    if key in self._queued_validate or key in self._inflight_validate:
                        break
                    self._enqueue_validate_unlocked(key, reviewer.strip())
                    break
            pending_after = any(r.strip().lower() not in self._validation_cache for r in state.reviewers if r.strip())
            issues_after = self._issues_from_cache_unlocked(state.reviewers)
        return ReviewerValidation(issues=issues_after, pending_checks=pending_after)

    def default_toolbar_hint(self) -> str:
        if self.status_note:
            return f"{_DEFAULT_STATUS_HINT}  ·  {self.status_note}"
        return _DEFAULT_STATUS_HINT

    def _enqueue_warmup(self) -> None:
        with self._lock:
            if self._client is None or self._closed:
                return
            if self._project and not self._warmup_plugin_queued:
                self._warmup_plugin_queued = True
                self._submit_unlocked(_Job(kind="warmup_plugin", key="warmup_plugin", generation=0))
            if self._change_id_hint and not self._warmup_suggest_queued:
                self._warmup_suggest_queued = True
                self._submit_unlocked(
                    _Job(
                        kind="warmup_suggest",
                        key="warmup_suggest",
                        generation=0,
                        token=self._change_id_hint,
                    )
                )

    def _submit_unlocked(self, job: _Job) -> None:
        self._outstanding += 1
        self._idle.clear()
        self._jobs.put(job)

    def _enqueue_complete_unlocked(self, key: str, token: str) -> None:
        if self._closed:
            return
        gen = self._complete_generation.get(key, 0) + 1
        self._complete_generation[key] = gen
        self._complete_tokens[key] = token
        if key in self._queued_complete or key in self._inflight_complete:
            return
        self._queued_complete.add(key)
        self._submit_unlocked(_Job(kind="complete", key=key, generation=gen, token=token))

    def _reschedule_complete_if_needed_unlocked(self, key: str, *, allow_while_inflight: bool = False) -> None:
        if key in self._prefix_completion_cache:
            return
        if key in self._queued_complete:
            return
        if key in self._inflight_complete and not allow_while_inflight:
            return
        token = self._complete_tokens.get(key)
        if not token or self._client is None or self._closed:
            return
        gen = self._complete_generation.get(key, 0)
        self._queued_complete.add(key)
        self._submit_unlocked(_Job(kind="complete", key=key, generation=gen, token=token))

    def _enqueue_validate_unlocked(self, key: str, reviewer: str) -> None:
        if self._closed or key in self._queued_validate or key in self._inflight_validate:
            return
        gen = self._validate_generation.get(key, 0) + 1
        self._validate_generation[key] = gen
        self._queued_validate.add(key)
        self._submit_unlocked(_Job(kind="validate", key=key, generation=gen, token=reviewer))

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                if job is None:
                    with self._lock:
                        self._idle.set()
                    return
                with self._lock:
                    if job.kind == "complete":
                        self._queued_complete.discard(job.key)
                        self._inflight_complete.add(job.key)
                    elif job.kind == "validate":
                        self._queued_validate.discard(job.key)
                        self._inflight_validate.add(job.key)
                self._rate_limit_wait()
                self._run_job(job)
            finally:
                with self._lock:
                    if job is not None:
                        if job.kind == "complete":
                            self._inflight_complete.discard(job.key)
                        elif job.kind == "validate":
                            self._inflight_validate.discard(job.key)
                        self._outstanding = max(0, self._outstanding - 1)
                        if self._outstanding == 0:
                            self._idle.set()
                self._jobs.task_done()

    def _rate_limit_wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_slot_at - now)
            self._next_slot_at = now + delay + _REQUEST_INTERVAL_SECONDS
        if delay:
            time.sleep(delay)

    def _run_job(self, job: _Job) -> None:
        client = self._client
        if client is None:
            return
        try:
            if job.kind == "warmup_plugin":
                self._run_warmup_plugin(client)
            elif job.kind == "warmup_suggest":
                self._run_warmup_suggest(client, job.token)
            elif job.kind == "complete":
                self._run_complete(client, job)
            elif job.kind == "validate":
                self._run_validate(client, job)
        except GerritApiError:
            self._disable_client("Gerrit reviewer validation unavailable (request failed).")

    def _run_warmup_plugin(self, client: ReviewerLookup) -> None:
        project = self._project
        if not project:
            return
        try:
            plugin_rows = client.get_plugin_project_reviewers(project)
        except GerritApiError:
            self._disable_client("Gerrit reviewer validation unavailable (request failed).")
            return
        if not plugin_rows:
            return
        slugs = [slug for slug in (slug_from_suggest_or_account_row(r) for r in plugin_rows) if slug]
        with self._lock:
            self._add_candidates_unlocked(slugs)
        self._notify_update()

    def _run_warmup_suggest(self, client: ReviewerLookup, change_id: str) -> None:
        try:
            slugs = fetch_suggested_reviewer_slugs(client, change_id, n=100)
        except GerritApiError:
            self._disable_client("Gerrit reviewer validation unavailable (request failed).")
            return
        with self._lock:
            self._add_candidates_unlocked(slugs)
        self._notify_update()

    def _run_complete(self, client: ReviewerLookup, job: _Job) -> None:
        with self._lock:
            if self._complete_generation.get(job.key) != job.generation:
                self._reschedule_complete_if_needed_unlocked(job.key, allow_while_inflight=True)
                return
        try:
            out = fetch_reviewer_slugs_for_prefix(
                client,
                change_id=self._change_id_hint,
                token=job.token,
                n=100,
            )
        except GerritApiError:
            self._disable_client("Gerrit reviewer validation unavailable (request failed).")
            with self._lock:
                if self._complete_generation.get(job.key) == job.generation:
                    self._prefix_completion_cache[job.key] = []
            self._notify_update()
            return
        with self._lock:
            if self._complete_generation.get(job.key) != job.generation:
                self._reschedule_complete_if_needed_unlocked(job.key, allow_while_inflight=True)
                return
            self._prefix_completion_cache[job.key] = out
        self._notify_update()

    def _run_validate(self, client: ReviewerLookup, job: _Job) -> None:
        status = self._resolve_reviewer_status(client, job.token)
        with self._lock:
            if self._validate_generation.get(job.key) != job.generation:
                return
            self._validation_cache[job.key] = status
        self._notify_update()

    def _disable_client(self, note: str) -> None:
        with self._lock:
            self.status_note = note
            self._client = None
        self._notify_update()

    def _notify_update(self) -> None:
        cb = self._on_update
        if cb is None:
            return
        with contextlib.suppress(Exception):
            cb()

    def _issues_from_cache_unlocked(self, reviewers: list[str]) -> list[ReviewerValidationIssue]:
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

    def _resolve_reviewer_status(self, client: ReviewerLookup, reviewer: str) -> Literal["ok", "unknown", "ambiguous"]:
        q = account_query_exact_lookup(reviewer)
        try:
            rows = client.query_accounts(q, n=8)
        except GerritApiError:
            self._disable_client("Gerrit reviewer validation unavailable (request failed).")
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


def _gerrit_creds_configured(settings: Settings) -> bool:
    return gerrit_credentials_configured(settings)
