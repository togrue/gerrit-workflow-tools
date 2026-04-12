from __future__ import annotations

import re
from dataclasses import dataclass

# Gerrit Change-Id line value: I + 40 hex digits
CHANGE_ID_VALUE_RE = re.compile(r"^I[0-9a-f]{40}$", re.IGNORECASE)


@dataclass
class ChangeIdIssue:
    kind: str  # "missing" | "duplicate" | "malformed"
    sha: str
    short_sha: str
    detail: str
    severity: str  # "error" | "warning"


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
    items: list[tuple[str, str, str | None]],
    *,
    strict: bool = True,
) -> tuple[list[ChangeIdIssue], int]:
    """Scan commit Change-Ids for missing, malformed, or duplicate values; return issues and a worst exit code."""
    issues: list[ChangeIdIssue] = []
    seen: dict[str, str] = {}
    for full_sha, short_sha, cid in items:
        ok, malformed = validate_change_id_value(cid)
        if malformed:
            sev = "error" if strict else "warning"
            issues.append(
                ChangeIdIssue(
                    kind="malformed",
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
                    kind="missing",
                    sha=full_sha,
                    short_sha=short_sha,
                    detail="no Change-Id in commit message",
                    severity="error",
                )
            )
            continue
        c = cid.strip() if cid else ""
        if c in seen:
            issues.append(
                ChangeIdIssue(
                    kind="duplicate",
                    sha=full_sha,
                    short_sha=short_sha,
                    detail=f"duplicate Change-Id {c} (also on {seen[c]})",
                    severity="error",
                )
            )
        else:
            seen[c] = short_sha
    exit_code = 0
    for issue in issues:
        if issue.severity == "error":
            exit_code = 2
            break
        if issue.severity == "warning" and exit_code < 2:
            exit_code = 1
    return issues, exit_code
