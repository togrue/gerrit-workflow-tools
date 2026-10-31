"""Reviewer/account normalization helpers shared by CLI and core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.stack_context import stack_project
from gerrit_workflow_tools.core.reviewers_strategy import default_reviewers_via_registry
from gerrit_workflow_tools.core.stack import get_stack_snapshot


@dataclass(frozen=True)
class ReviewerAccount:
    """Normalized Gerrit reviewer account identity used by CLI and core."""

    slug: str
    account_id: int | None = None


class ReviewerStrategy(str, Enum):
    """How reviewers are applied during `ger push`."""

    PUSH = "push"
    LAZY = "lazy"
    OVERWRITE = "overwrite"


def gerrit_credentials_configured(settings: Settings) -> bool:
    """Whether Gerrit REST credentials are configured in git config."""

    return bool(settings.gerrit_user and (settings.gerrit_token or settings.gerrit_password) is not None)


def account_slug_from_gerrit(account: Mapping[str, object]) -> str | None:
    """Best-effort Gerrit account slug normalization."""

    username = account.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    email = account.get("email")
    if isinstance(email, str) and "@" in email:
        return email.split("@", 1)[0].strip()
    name = account.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def format_gerrit_account_label(account: Mapping[str, object]) -> str | None:
    """Human label for Gerrit AccountInfo, e.g. ``grt (Tobias Grün)``."""

    slug = account_slug_from_gerrit(account)
    raw_name = account.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if slug and name and slug.lower() != name.lower():
        return f"{slug} ({name})"
    if slug:
        return slug
    if name:
        return name
    return None


def reviewer_accounts_from_reviewer_list(rows: Sequence[Mapping[str, object]]) -> list[ReviewerAccount]:
    """Parse ``GET changes/<id>/reviewers/`` rows (flat accounts or ReviewerInfo)."""

    out: list[ReviewerAccount] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = row.get("state")
        if isinstance(state, str) and state not in ("REVIEWER", "CC"):
            continue
        account = row.get("account")
        if isinstance(account, dict):
            payload: Mapping[str, object] = account
        else:
            payload = row
        slug = account_slug_from_gerrit(payload)
        if not slug:
            continue
        account_id = payload.get("_account_id")
        out.append(ReviewerAccount(slug=slug, account_id=account_id if isinstance(account_id, int) else None))
    return out


def reviewer_accounts_from_change_info(
    detail: Mapping[str, object],
    *,
    roles: Sequence[str] = ("REVIEWER", "CC"),
) -> list[ReviewerAccount]:
    """Reviewer accounts in Gerrit API order, limited to *roles*.

    ChangeInfo stores reviewers in a ``REVIEWER`` / ``CC`` map. The list shape
    (``state`` on each row) is the same split. ``overwrite`` asks for
    ``REVIEWER`` only so CC accounts are not removed.
    """

    wanted = tuple(roles)
    out: list[ReviewerAccount] = []
    reviewers = detail.get("reviewers")
    if isinstance(reviewers, dict):
        for role in wanted:
            bucket = reviewers.get(role)
            if not isinstance(bucket, list):
                continue
            for account in bucket:
                if not isinstance(account, dict):
                    continue
                slug = account_slug_from_gerrit(account)
                if not slug:
                    continue
                account_id = account.get("_account_id")
                out.append(ReviewerAccount(slug=slug, account_id=account_id if isinstance(account_id, int) else None))
        return out
    if not isinstance(reviewers, list):
        return out
    for entry in reviewers:
        if not isinstance(entry, dict):
            continue
        state = entry.get("state")
        if state not in wanted:
            continue
        account = entry.get("account")
        if not isinstance(account, dict):
            continue
        slug = account_slug_from_gerrit(account)
        if not slug:
            continue
        account_id = account.get("_account_id")
        out.append(ReviewerAccount(slug=slug, account_id=account_id if isinstance(account_id, int) else None))
    return out


def parse_reviewer_names(raw: str) -> list[str]:
    """Split a comma- or space-separated reviewer list, dropping blanks and duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.replace(",", " ").split():
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def resolve_push_reviewers(
    cwd: Path,
    branch: str,
    reviewer_flag_segments: Sequence[str],
    *,
    settings: Settings,
) -> list[str]:
    """Reviewers for a push: flags, else the reviewer registry, else branch config."""
    if reviewer_flag_segments:
        return parse_reviewer_names(",".join(reviewer_flag_segments))
    try:
        rows = list(get_stack_snapshot(cwd, branch, head=branch, first_parent=True, settings=settings).commits)
    except Exception:  # pylint: disable=broad-exception-caught
        rows = []
    scripted = default_reviewers_via_registry(
        cwd,
        project=stack_project(cwd, settings=settings),
        branch=branch,
        commits=rows,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )
    if scripted is not None:
        return scripted
    cfg = settings.branch_gerrit_reviewers(branch)
    return parse_reviewer_names(cfg) if cfg else []
