"""Stack inspection helpers for commit ranges and ancestry."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.change_id import parse_change_id_footer, validate_change_id_value
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git, git_out, is_unknown_revision, unknown_revision_error
from gerrit_workflow_tools.core.git_state import configured_upstream_abbrev, resolve_branch_for_branch_config
from gerrit_workflow_tools.core.upstream import missing_upstream_error


logger = logging.getLogger(__name__)

_FIXUP_PREFIX = "fixup! "


def _resolve_stack_branch(
    cwd: Path | str | None,
    branch: str | None,
    *,
    settings: Settings | None = None,
) -> str:
    """Local branch whose upstream defines the stack, including during rebase."""
    if branch is not None:
        return branch
    snap = settings if settings is not None else Settings.from_cwd(cwd)
    return resolve_branch_for_branch_config(cwd, settings=snap)


def upstream_tracking_tip_and_display(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """
    Return ``(upstream_tip_sha, display_name)`` for the branch's **upstream** only.

    The local stack is ``<sha>..HEAD`` (same *sha* as the first element here).
    """
    snap = settings if settings is not None else Settings.from_cwd(cwd)
    b = _resolve_stack_branch(cwd, branch, settings=snap)
    display = configured_upstream_abbrev(snap, b)
    if not display:
        upstream_sym = f"{b}@{{upstream}}" if b != "HEAD" else "@{upstream}"
        upstream_name = git("rev-parse", "--abbrev-ref", upstream_sym, cwd=cwd, check=False)
        if upstream_name.returncode != 0:
            raise missing_upstream_error(b)
        display = upstream_name.stdout.strip()
    upstream_ref = git("rev-parse", "--verify", display, cwd=cwd, check=False)
    if upstream_ref.returncode != 0:
        raise GitError(
            f"Upstream {display!r} for branch {b!r} does not resolve to a ref. "
            f"Fetch from your remote so the tracking branch exists."
        )
    return (upstream_ref.stdout.strip(), display)


def abbreviated_sha(sha: str) -> str:
    """*sha* as ger shows a commit it did not read through ``git log`` (git's own default length, 7)."""
    return sha[:7]


@dataclass(frozen=True)
class Commit:
    """One commit from a ``git log`` metadata line (subject + full message body)."""

    sha: str
    short_sha: str
    subject: str
    body: str
    change_id: str | None
    """The footer value exactly as written — not necessarily a valid Gerrit id.

    Kept raw so Change-Id validation can report a malformed footer as malformed rather
    than as missing. Anything about to send this to Gerrit reads :attr:`valid_change_id`."""

    @property
    def valid_change_id(self) -> str | None:
        """The footer value when it is a well-formed **Change-Id**, else ``None``."""
        return self.change_id if validate_change_id_value(self.change_id)[0] else None


@dataclass(frozen=True)
class StackSnapshot:
    """Upstream tracking tip + ``upstream_tip..HEAD`` commits (one ``git log``)."""

    upstream_tip: str
    commits: tuple[Commit, ...]
    upstream: str = ""
    """The upstream as the user names it (``origin/main``), for advice that quotes it."""

    def carrying(self, change_id: str) -> list[Commit]:
        """The stack commits whose footer is *change_id*, case-insensitively (the grammar accepts either)."""
        want = change_id.lower()
        return [c for c in self.commits if c.valid_change_id and c.valid_change_id.lower() == want]


def get_stack_snapshot(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    head: str = "HEAD",
    first_parent: bool = False,
    settings: Settings | None = None,
) -> StackSnapshot:
    """The **local stack**: the upstream tip SHA and oldest-first ``upstream_tip..head`` commits.

    The one way to load the stack. *first_parent* follows Gerrit's relation-chain
    semantics (merged side branches are not stack members).
    """
    upstream_tip, display = upstream_tracking_tip_and_display(cwd, branch, settings=settings)
    rows_list = commits_in_range(cwd, f"{upstream_tip}..{head}", first_parent=first_parent)
    return StackSnapshot(upstream_tip=upstream_tip, commits=tuple(rows_list), upstream=display)


def merge_base_with_target(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    head: str = "HEAD",
    settings: Settings | None = None,
) -> tuple[str, str, str]:
    """
    Return ``(rebase_fork, upstream_display, upstream_tip_sha)``.

    *upstream_tip_sha* is ``git rev-parse`` of the branch's ``@{upstream}``; the default
    local stack is ``upstream_tip_sha..head`` (``HEAD`` when *head* is omitted).

    *rebase_fork* is ``merge-base(head, upstream_tip_sha)`` — the onto point for
    ``git rebase -i <fork>`` (not the same commit as *upstream_tip_sha* when histories diverge).
    """
    upstream_tip, display = upstream_tracking_tip_and_display(cwd, branch, settings=settings)
    rebase_fork = git_out("merge-base", head, upstream_tip, cwd=cwd)
    logger.debug(
        "merge_base_with_target: rebase_fork=%s upstream_display=%r upstream_tip=%r",
        rebase_fork[:8],
        display,
        upstream_tip[:8],
    )
    return rebase_fork, display, upstream_tip


def rev_spec_stack_base_to_end(
    cwd: Path | str | None,
    input_arg: str,
    branch: str | None = None,
    *,
    settings: Settings | None = None,
) -> str:
    """``upstream_tip..END`` where END is *input_arg* or the right side of ``left..right``."""
    _fork, _display, upstream_tip = merge_base_with_target(cwd, branch, settings=settings)
    if ".." in input_arg:
        idx = input_arg.index("..")
        right = input_arg[idx + 2 :].strip() or "HEAD"
        logger.debug("rev_spec_stack_base_to_end rev-parse %r (range right)", right)
    else:
        right = input_arg
        logger.debug("rev_spec_stack_base_to_end rev-parse %r (end ref)", right)
    end = git_out("rev-parse", right, cwd=cwd)
    return f"{upstream_tip}..{end}"


# Field separator in `git log --format` (ASCII RS). Avoids NUL in argv (Windows
# subprocess rejects embedded nulls); %x1e is expanded by git, not by the shell.
_RS = "\x1e"
_COMMIT_ROW_FMT = "%H%x1e%h%x1e%s%x1e%B%x1e"
# %ad/%cd are raw "unix_ts tz" under --date=raw (matches commit object / Change-Id hook).
_REWRITE_ROW_FMT = "%H%x1e%T%x1e%P%x1e%an%x1e%ae%x1e%ad%x1e%cn%x1e%ce%x1e%cd%x1e%s%x1e%B%x1e"


def _log_records(cwd: Path | str | None, fmt: str, width: int, *log_args: str) -> list[tuple[str, ...]]:
    """One ``git log`` split into records of *width* fields each.

    %B may contain newlines but not RS, and every record ends with RS, so the text after the
    last RS is the only field to discard. Git puts a newline between commits, so a field after
    RS may start with one. Empty mid-record fields stay: a root commit has an empty %P.
    """
    p = git("log", *log_args, f"--format={fmt}", cwd=cwd, check=False)
    if p.returncode != 0:
        if is_unknown_revision(p.stderr):
            raise unknown_revision_error(cwd, p.stderr, p.returncode)
        raise GitError("git log failed", stderr=p.stderr, returncode=p.returncode)
    fields = [part.lstrip("\n") for part in p.stdout.split(_RS)][:-1]
    if len(fields) % width != 0:
        raise GitError(
            f"git log returned unexpected field count {len(fields)} (not divisible by {width})",
            stderr=p.stdout[:200],
            returncode=1,
        )
    return list(zip(*[iter(fields)] * width, strict=True))


def read_commit_rows(cwd: Path | str | None, *log_args: str) -> list[Commit]:
    """**Commit rows** for whatever *log_args* select (a range, ``--all``, ``--no-walk`` SHAs).

    The one commit reader: a single ``git log`` in ``git log`` order. Raises :class:`GitError`
    when git fails.
    """
    return [
        Commit(
            sha=sha.strip(),
            short_sha=short_sha.strip(),
            subject=subject.strip(),
            body=body.strip(),
            change_id=parse_change_id_footer(body.strip()),
        )
        for sha, short_sha, subject, body in _log_records(cwd, _COMMIT_ROW_FMT, 4, *log_args)
    ]


@dataclass(frozen=True)
class CommitMeta:
    """Author, date and full message of one commit, as ``ger show`` prints them."""

    author_line: str
    date: str
    body: str


def read_commit_meta(cwd: Path | str | None, sha: str) -> CommitMeta:
    """``Author <email>``, the author date and the message body of *sha*, from one ``git show``."""
    lines = git_out("show", "-s", "--no-patch", "--format=%an%n%ae%n%ad%n%B", sha, cwd=cwd).splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    author, email, date = [*lines, "", "", ""][:3]
    body = "\n".join(lines[3:]).rstrip("\n")
    return CommitMeta(author_line=f"{author} <{email}>" if email else author, date=date, body=body)


@dataclass(frozen=True)
class RewriteCommit:
    """One commit with everything a message-only ``commit-tree`` rewrite needs."""

    sha: str
    tree: str
    parents: tuple[str, ...]
    subject: str
    body: str
    ident_env: dict[str, str]
    committer_ident: str


def read_rewrite_rows(cwd: Path | str | None, rev_spec: str) -> list[RewriteCommit]:
    """Oldest-first commits in *rev_spec* with tree, parents and idents, from one ``git log``."""
    rows: list[RewriteCommit] = []
    for sha, tree, parents, an, ae, ad, cn, ce, cd, subject, body in _log_records(
        cwd, _REWRITE_ROW_FMT, 11, "--reverse", "--date=raw", rev_spec
    ):
        rows.append(
            RewriteCommit(
                sha=sha,
                tree=tree,
                parents=tuple(parents.split()),
                subject=subject,
                body=body,
                ident_env={
                    "GIT_AUTHOR_NAME": an,
                    "GIT_AUTHOR_EMAIL": ae,
                    "GIT_AUTHOR_DATE": ad,
                    "GIT_COMMITTER_NAME": cn,
                    "GIT_COMMITTER_EMAIL": ce,
                    "GIT_COMMITTER_DATE": cd,
                },
                committer_ident=f"{cn} <{ce}> {cd}",
            )
        )
    return rows


def commits_in_range(
    cwd: Path | str | None,
    rev_range: str,
    *,
    first_parent: bool = False,
) -> list[Commit]:
    """
    Oldest-first commits in *rev_range* (e.g. ``upstream_tip..HEAD``).

    One ``git log`` call.  Pass *first_parent=True* to restrict traversal to
    first-parent edges only (i.e. ignore commits reachable via merge parents).
    """
    extra: list[str] = ["--first-parent"] if first_parent else []
    return read_commit_rows(cwd, "--reverse", *extra, rev_range)


UNSQUASHED_FIXUP_ADVICE = "squash it with `ger rebase --autosquash`"


def is_unsquashed_fixup(subject: str) -> bool:
    """A ``fixup!``/``squash!`` commit still in the stack: the one test ``ger log`` and ``ger push`` share."""
    return subject.startswith((_FIXUP_PREFIX, "squash! "))


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
