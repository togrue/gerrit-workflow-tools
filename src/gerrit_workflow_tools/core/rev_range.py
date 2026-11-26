"""The revision range a stack is read from: the default for a branch, and whose upstream it needs.

Nothing here runs git or prompts: a range may name an ``@{upstream}`` that is not configured,
and the caller decides whether to prompt, fail, or carry on.
"""

from __future__ import annotations

import re


_UPSTREAM_TOKEN_RE = re.compile(r"(?P<branch>[^\s@]+)?@\{upstream\}")


def resolve_rev_range(arg_rev_range: str | None, *, branch: str) -> str:
    """Return the revision range to inspect.

    A bare ref becomes ``<ref>@{upstream}..<ref>``; a range is taken as given. With no
    argument, the stack of *branch* (the run's ``stack_branch``): its upstream range.
    """
    if arg_rev_range:
        if ".." in arg_rev_range:
            return arg_rev_range
        return f"{arg_rev_range}@{{upstream}}..{arg_rev_range}"
    if branch == "HEAD":
        return "@{upstream}..HEAD"
    return f"{branch}@{{upstream}}..{branch}"


def branches_needing_upstream(rev_range: str, *, branch: str) -> list[str]:
    """Return branch names in *rev_range* whose ``@{upstream}`` the caller must ensure exists.

    A bare ``@{upstream}`` or ``HEAD`` stands for *branch*, the stack's branch. Reports
    only; resolving an upstream is interactive and stays with the caller.
    """
    current = branch
    required: list[str] = []
    seen: set[str] = set()
    for match in _UPSTREAM_TOKEN_RE.finditer(rev_range):
        branch = (match.group("branch") or current).lstrip(".")
        if branch in ("HEAD", ""):
            branch = current
        if branch in seen:
            continue
        seen.add(branch)
        required.append(branch)
    return required
