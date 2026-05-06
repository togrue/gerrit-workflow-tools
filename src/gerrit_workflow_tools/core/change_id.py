"""Change-Id parsing and validation helpers for local commits."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

# Gerrit Change-Id line value: I + 40 hex digits
CHANGE_ID_VALUE_RE = re.compile(r"^I[0-9a-f]{40}$", re.IGNORECASE)

# Footer line as used by ger change-id: last line must be ``Change-Id: I...`` with lowercase hex.
CHANGE_ID_LAST_LINE_FOOTER_RE = re.compile(r"^Change-Id:\s*(I[a-f0-9]{40})$", re.MULTILINE)


@dataclass
class ChangeIdIssue:
    """Represents a single Change-Id validation issue for one commit."""

    kind: ChangeIdIssueKind
    sha: str
    short_sha: str
    detail: str
    severity: IssueSeverity


@dataclass(frozen=True)
class ChangeIdRow:
    """Named row used by Change-Id validators."""

    sha: str
    short_sha: str
    change_id: str | None


class ChangeIdIssueKind(str, Enum):
    """Bounded issue categories for Change-Id validation."""

    MISSING = "missing"
    DUPLICATE = "duplicate"
    MALFORMED = "malformed"


class IssueSeverity(str, Enum):
    """Bounded severities for validation issues."""

    ERROR = "error"
    WARNING = "warning"


def is_change_id_token(s: str) -> bool:
    """Return True if *s* is a Change-Id token (``I`` + 40 lowercase hex digits).

    Stricter than :data:`CHANGE_ID_VALUE_RE`: used for CLI passthrough (e.g. ``ger change-id``)
    where uppercase hex is not accepted as a bare argument.
    """
    return s.startswith("I") and len(s) == 41 and all(c in "0123456789abcdef" for c in s[1:])


def extract_change_id_from_msg(msg: str) -> str | None:
    """Return the Change-Id from the last non-empty line of *msg*, if it matches ``Change-Id: I…``."""
    s = msg.rstrip("\n")
    i = s.rfind("\n")
    line = (s[i + 1 :] if i >= 0 else s).strip()
    if line:
        m = CHANGE_ID_LAST_LINE_FOOTER_RE.match(line)
        return m.group(1) if m else None
    return None


def validate_change_id_value(raw: str | None) -> tuple[bool, bool]:
    """Return whether ``raw`` is a valid Gerrit Change-Id value and whether it is malformed vs missing."""
    if raw is None:
        return False, False
    s = raw.strip()
    if not s:
        return False, False
    if CHANGE_ID_VALUE_RE.match(s):
        return True, False
    return False, True


def classify_issues(
    items: Iterable[ChangeIdRow],
    *,
    strict: bool = True,
) -> tuple[list[ChangeIdIssue], int]:
    """Scan commit Change-Ids for missing, malformed, or duplicate values; return issues and a worst exit code."""
    issues: list[ChangeIdIssue] = []
    seen: dict[str, str] = {}
    for item in items:
        full_sha, short_sha, cid = item.sha, item.short_sha, item.change_id
        ok, malformed = validate_change_id_value(cid)
        if malformed:
            sev = IssueSeverity.ERROR if strict else IssueSeverity.WARNING
            issues.append(
                ChangeIdIssue(
                    kind=ChangeIdIssueKind.MALFORMED,
                    sha=full_sha,
                    short_sha=short_sha,
                    detail=f"invalid Change-Id: {cid!r}",
                    severity=sev,
                )
            )
            continue
        if not ok:
            issues.append(
                ChangeIdIssue(
                    kind=ChangeIdIssueKind.MISSING,
                    sha=full_sha,
                    short_sha=short_sha,
                    detail="no Change-Id in commit message",
                    severity=IssueSeverity.ERROR,
                )
            )
            continue
        c = cid.strip() if cid else ""
        if c in seen:
            issues.append(
                ChangeIdIssue(
                    kind=ChangeIdIssueKind.DUPLICATE,
                    sha=full_sha,
                    short_sha=short_sha,
                    detail=f"duplicate Change-Id {c} (also on {seen[c]})",
                    severity=IssueSeverity.ERROR,
                )
            )
        else:
            seen[c] = short_sha
    exit_code = 0
    for issue in issues:
        if issue.severity == IssueSeverity.ERROR:
            exit_code = 2
            break
        if issue.severity == IssueSeverity.WARNING and exit_code < 2:
            exit_code = 1
    assert all(i.kind for i in issues), "each issue must set kind"
    return issues, exit_code
