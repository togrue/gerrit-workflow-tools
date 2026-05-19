"""Reviewer/account normalization helpers shared by CLI and core."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.config import gerrit_password, gerrit_token, gerrit_user
from gerrit_workflow_tools.core.gerrit_change_status import ReviewerAccount


class ReviewerStrategy(str, Enum):
    """How reviewers are applied during `ger push`."""

    PUSH = "push"
    LAZY = "lazy"
    OVERWRITE = "overwrite"


def gerrit_credentials_configured(cwd: Path) -> bool:
    """Whether Gerrit REST credentials are configured in git config."""

    return bool(gerrit_user(cwd) and (gerrit_token(cwd) or gerrit_password(cwd)) is not None)


def account_slug_from_gerrit(account: dict[str, object]) -> str | None:
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


def format_gerrit_account_label(account: dict[str, object]) -> str | None:
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


def reviewer_accounts_from_change_info(detail: dict[str, object]) -> list[ReviewerAccount]:
    """Reviewer/CC account list in Gerrit API order."""

    out: list[ReviewerAccount] = []
    reviewers = detail.get("reviewers")
    if isinstance(reviewers, dict):
        for role in ("REVIEWER", "CC"):
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
        if state not in ("REVIEWER", "CC"):
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
