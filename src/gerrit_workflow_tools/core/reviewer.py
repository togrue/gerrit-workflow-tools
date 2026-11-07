"""Reviewer/account normalization helpers shared by CLI and core."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.http_rest import gerrit_auth_from_settings
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

    return gerrit_auth_from_settings(settings) is not None


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


def _reviewer_accounts(accounts: Iterable[object]) -> list[ReviewerAccount]:
    """AccountInfo rows that name someone, in order; anything else is skipped."""
    out: list[ReviewerAccount] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        slug = account_slug_from_gerrit(account)
        if slug:
            account_id = account.get("_account_id")
            out.append(ReviewerAccount(slug=slug, account_id=account_id if isinstance(account_id, int) else None))
    return out


def reviewer_accounts_from_reviewer_list(rows: Sequence[Mapping[str, object]]) -> list[ReviewerAccount]:
    """Parse ``GET changes/<id>/reviewers/`` rows (flat accounts or ReviewerInfo)."""

    def _keeps(row: Mapping[str, object]) -> bool:
        if not isinstance(row, dict):
            return False
        state = row.get("state")
        return not isinstance(state, str) or state in ("REVIEWER", "CC")

    return _reviewer_accounts(
        row["account"] if isinstance(row.get("account"), dict) else row for row in rows if _keeps(row)
    )


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

    reviewers = detail.get("reviewers")
    if isinstance(reviewers, dict):
        buckets = (reviewers.get(role) for role in roles)
        return _reviewer_accounts(account for bucket in buckets if isinstance(bucket, list) for account in bucket)
    if isinstance(reviewers, list):
        return _reviewer_accounts(
            entry.get("account") for entry in reviewers if isinstance(entry, dict) and entry.get("state") in roles
        )
    return []


def parse_reviewer_names(raw: str) -> list[str]:
    """Split a comma- or space-separated reviewer list, dropping blanks and duplicates."""
    return list(dict.fromkeys(raw.replace(",", " ").split()))


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
    return branch_reviewers(branch, settings=settings)


def branch_reviewers(branch: str, *, settings: Settings) -> list[str]:
    """Reviewers saved in *branch*'s ``gerritReviewers`` config."""
    cfg = settings.branch_gerrit_reviewers(branch)
    return parse_reviewer_names(cfg) if cfg else []
