"""``fixup!`` and ``squash!`` commits of a stack: what they are, what they fold into and where autosquash puts them."""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.stack import Commit


_FIXUP_PREFIX = "fixup! "
UNSQUASHED_FIXUP_ADVICE = "squash it with `ger rebase --autosquash`"


def is_unsquashed_fixup(subject: str) -> bool:
    """A ``fixup!``/``squash!`` commit still in the stack: the one test ``ger log`` and ``ger push`` share."""
    return subject.startswith((_FIXUP_PREFIX, "squash! "))


def fixed_subject(subject: str) -> str:
    """The subject of the commit a ``fixup!``/``squash!`` commit fixes, past every prefix."""
    while is_unsquashed_fixup(subject):
        subject = subject.split(" ", 1)[1] if " " in subject else ""
    return subject


def fixup_targets(commits: Sequence[tuple[str, str]]) -> dict[str, str]:
    """By the id of each unsquashed fixup in *commits* (``(sha, subject)``, oldest first), the commit
    it folds into: the nearest one below it with the subject it names, as ``ger fix`` writes it.
    A fixup whose original is not below it is left out."""
    targets: dict[str, str] = {}
    for index, (sha, subject) in enumerate(commits):
        if is_unsquashed_fixup(subject):
            named = fixed_subject(subject)
            below = (other for other, text in reversed(commits[:index]) if text == named)
            if (target := next(below, None)) is not None:
                targets[sha] = target
    return targets


def fixup_target_sha(commits: Sequence[Commit], sha: str) -> str:
    """The commit a new fixup of *sha* should name: the original when *sha* is itself a fixup.

    *commits* is oldest first. Returns *sha* when it is not a fixup or its original is not there.
    """
    index = next((i for i, commit in enumerate(commits) if commit.sha == sha), None)
    if index is None or not commits[index].subject.startswith(_FIXUP_PREFIX):
        return sha
    subject = commits[index].subject
    while subject.startswith(_FIXUP_PREFIX):
        subject = subject.removeprefix(_FIXUP_PREFIX)
    return next((commit.sha for commit in reversed(commits[:index]) if commit.subject == subject), sha)


def autosquash_order(commits: Sequence[Commit]) -> list[Commit]:
    """*commits* (oldest first) as ``git rebase --autosquash`` orders them: each fixup after its target.

    A fixup lands after the commit its subject names (:func:`fixup_target_sha`) and after the
    fixups of that commit before it; one whose target is not there stays where it is.
    """
    target = {c.sha: fixup_target_sha(commits, c.sha) for c in commits if c.subject.startswith(_FIXUP_PREFIX)}
    order: list[Commit] = []
    for commit in commits:
        if target.get(commit.sha, commit.sha) == commit.sha:
            order += [commit, *(c for c in commits if c.sha != commit.sha and target.get(c.sha) == commit.sha)]
    return order
