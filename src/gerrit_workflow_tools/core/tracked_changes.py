"""What the index and work tree hold of yours, sorted by what ``git stash`` makes of it.

Every ger command that refuses to move over your edits asks here.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git_out


def staged_submodules(cwd: Path | str | None, base: str = "HEAD") -> list[str]:
    """Submodules whose commit in the index is not the one *base* records: staged, not committed.

    A submodule's own state (another commit checked out, edits inside it) is not here: git
    leaves it out of what switching, rebasing and ``git stash`` touch.
    """
    raw = git_out("diff", "--cached", "--raw", "-z", "--no-renames", "--ignore-submodules=none", base, cwd=cwd)
    fields = raw.split("\0")
    # -z: each record is ``:mode mode sha sha status`` NUL ``path`` NUL, the path as is (never C-quoted).
    return [
        path
        for meta, path in zip(fields[::2], fields[1::2], strict=False)
        if "160000" in meta.lstrip(":").split()[:2]  # a gitlink on either side: a submodule
    ]
