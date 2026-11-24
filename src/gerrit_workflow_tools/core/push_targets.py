"""What each commit a push sends becomes on Gerrit: a new change, or the next patch set of one. Does not print.

Before the push, read from the changes it loads anyway (``check_existing_changes``) and from the
repository: no request of its own. After it, :func:`pushed_changes` says what they became.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.code_facts import CodeChange, code_change
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus, overlay_code_facts, patchset_status
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit


_CODE_WORDS = {CodeChange.REBASED: "rebase only", CodeChange.CHANGED: "code changed"}


@dataclass(frozen=True)
class NextPatchset:
    """A sent commit whose change Gerrit has already: the patch set it becomes, and what changed."""

    number: int | None
    patch_set: int | None
    """The patch set this push makes: one past Gerrit's current one."""
    code: CodeChange
    owner: str | None
    """The change owner's name, when the change is somebody else's."""
    reviewers: tuple[str, ...]
    """Who reviews the change already."""

    def describe(self) -> str:
        """``change 2101, patchset 3 (code changed), owner Alice``."""
        what = [_CODE_WORDS[self.code]] if self.code in _CODE_WORDS else []
        what += [f"owner {self.owner}"] if self.owner else []
        return f"change {self.number}, patchset {self.patch_set}" + (f" ({', '.join(what)})" if what else "")


NEW_CHANGE = "new change"
"""What a sent commit becomes when Gerrit has no change for it yet."""


def next_patchsets(
    cwd: Path | str | None, commits: Iterable[Commit], views: Mapping[str, ChangeView], *, user: str | None
) -> dict[str, NextPatchset]:
    """By SHA, the commits in *commits* that are new patch sets of a change in *views* (by SHA); *user* is you.

    A commit missing here becomes a new change. Whether the code changed is read with three git calls
    for all of them (``overlay_code_facts``), and is unknown when Gerrit's patch set is not in this repository.
    """
    newer = [
        (c, views[c.sha])
        for c in commits
        if c.sha in views and patchset_status(c.sha, views[c.sha]) == PatchsetStatus.NEWER
    ]
    facts = overlay_code_facts(cwd, newer)
    you = {user.casefold()} if user else set()
    out: dict[str, NextPatchset] = {}
    for commit, view in newer:
        current = view.current_revision_number
        owners = {name.casefold() for name in (view.owner_username, view.owner_email) if name}
        out[commit.sha] = NextPatchset(
            view.number,
            current + 1 if current is not None else None,
            code_change(commit.sha, view, facts),
            view.owner_name if you and owners and not owners & you else None,
            tuple(account.slug for account in view.reviewers or ()),
        )
    return out


@dataclass(frozen=True)
class PushedChange:
    """A commit a push sent, as Gerrit has it now: the change and the patch set it became."""

    commit: Commit
    number: int | None
    patch_set: int | None
    url: str | None
    """The patch set's page, built from ``ger.webUrl`` (not from the URLs git's remote output names)."""


def pushed_changes(run: RunContext, commits: Sequence[Commit]) -> tuple[PushedChange, ...]:
    """*commits*, which a push just sent, as Gerrit has them now; empty without credentials or an answer.

    One request for all of them: the push dropped their cached state, so this fetch is what the
    next ``ger log`` would make anyway, and it leaves the cache current.
    """
    service = run.gerrit
    with_ids = [commit for commit in commits if commit.valid_change_id]
    if not with_ids or not service.credentials_configured:
        return ()
    try:
        stack = run.stack
        triplets = {c.sha: build_triplet(stack.project, stack.push_branch, c.valid_change_id or "") for c in with_ids}
        payloads = service.changes.get_payloads(list(dict.fromkeys(triplets.values())))
    except (ValueError, GerritApiError, ChangeResolutionError):
        return ()
    out: list[PushedChange] = []
    for commit in with_ids:
        payload = payloads.get(triplets[commit.sha])
        view = ChangeView.from_payload(payload) if payload else None
        revision = view.revisions.get(commit.sha.lower()) if view else None
        if view is None or revision is None:  # Gerrit does not show it as a patch set of the change
            continue
        page = view.url(service.web_base)
        out.append(PushedChange(commit, view.number, revision.number, page and f"{page}/{revision.number}"))
    return tuple(out)
