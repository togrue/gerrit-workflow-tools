"""Resolving the paths of **CI findings** to files of the commit CI ran on.

A CI log names files as the build agent saw them: under a workspace directory that differs
per agent (``…/job``, ``…/job@2``, a container mount), relative to a subdirectory, or by
bare file name. The commit's own file list says which file is meant, without knowing any of
that: the longest tail of the logged path that is a tracked file is the file.

Two guards keep a file outside the repository (a system header, an installed package) from
being taken for one of ours:

- the directory a path would have to be stripped of must be one several findings agree on,
  or the match must be more than a bare file name;
- a logged path that is shorter than a tracked one resolves only when exactly one tracked
  file ends with it.

A path that resolves to nothing is left as logged.
"""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_log import normalize_log_path
from gerrit_workflow_tools.core.git_run import git


@dataclass(frozen=True)
class TrackedPaths:
    """The files of one commit, indexed for resolving logged paths."""

    paths: frozenset[str]
    by_name: dict[str, tuple[str, ...]]

    @classmethod
    def of(cls, paths: Iterable[str]) -> TrackedPaths:
        """Index *paths* (repository-relative, forward slashes)."""
        by_name: dict[str, list[str]] = {}
        known = frozenset(paths)
        for path in known:
            by_name.setdefault(posixpath.basename(path), []).append(path)
        return cls(paths=known, by_name={name: tuple(found) for name, found in by_name.items()})


def tracked_paths(cwd: Path | str, commit: str) -> TrackedPaths | None:
    """The files of *commit*, or ``None`` when the commit is not in this repository.

    Asked of git once per commit: a commit's tree never changes, so the answer is kept.
    """
    if not _is_object_id(commit):
        return None  # a ref can move; only an object id names one tree for good
    return _tracked_paths(str(cwd), commit)


def _is_object_id(commit: str) -> bool:
    return len(commit) in (40, 64) and all(c in "0123456789abcdef" for c in commit.lower())


@lru_cache(maxsize=16)
def _tracked_paths(cwd: str, commit: str) -> TrackedPaths | None:
    # --full-tree: from a subdirectory, ls-tree would list only that subtree, relative to it.
    listed = git("ls-tree", "-r", "-z", "--name-only", "--full-tree", commit, cwd=cwd, check=False)
    if listed.returncode != 0:
        return None
    return TrackedPaths.of(name for name in listed.stdout.split("\0") if name)


def _clean(path: str) -> str:
    """*path* normalized, with ``a/../b`` folded and any leading ``../`` dropped."""
    parts = posixpath.normpath(normalize_log_path(path)).split("/")
    while parts and parts[0] == "..":
        parts.pop(0)
    return "/".join(parts)


def _tails(path: str, tracked: TrackedPaths) -> list[tuple[str, str]]:
    """``(stripped directory, tracked file)`` for every tail of *path* that is tracked, longest first."""
    parts = path.split("/")
    return [("/".join(parts[:k]), tail) for k in range(1, len(parts)) if (tail := "/".join(parts[k:])) in tracked.paths]


def _unique_ending(path: str, tracked: TrackedPaths) -> str | None:
    """The one tracked file that ends with *path* (a path logged relative to a subdirectory)."""
    ending = f"/{path}"
    found = [known for known in tracked.by_name.get(posixpath.basename(path), ()) if known.endswith(ending)]
    return found[0] if len(found) == 1 else None


def resolve_paths(logged: Iterable[str], tracked: TrackedPaths) -> dict[str, str]:
    """Each of *logged* that names a tracked file, mapped to that file's repository path."""
    cleaned = {path: _clean(path) for path in set(logged)}
    tails = {path: _tails(clean, tracked) for path, clean in cleaned.items() if clean not in tracked.paths}
    # How many logged paths each directory would explain: the workspace is the one they agree on.
    votes = Counter(directory for found in tails.values() for directory, _ in found)
    resolved: dict[str, str] = {}
    for path, clean in cleaned.items():
        if clean in tracked.paths:
            resolved[path] = clean
            continue
        absolute = clean.startswith("/") or clean[1:2] == ":"
        backed = [
            (votes[directory], len(tail), tail)
            for directory, tail in tails[path]
            if votes[directory] > 1 or "/" in tail or not absolute
        ]
        if backed:
            resolved[path] = max(backed)[2]
        elif not absolute and (found := _unique_ending(clean, tracked)):
            resolved[path] = found
    return resolved


def resolve_finding_paths(findings: Sequence[CiFinding], tracked: TrackedPaths | None) -> list[CiFinding]:
    """*findings* with each path that names a file of the commit rewritten to it.

    The fingerprint follows the path, so the same finding from two build agents compares equal.
    """
    if tracked is None:
        return list(findings)
    resolved = resolve_paths((f.path for f in findings if f.path), tracked)
    return [
        replace(f, path=resolved[f.path], fingerprint="") if f.path and resolved.get(f.path, f.path) != f.path else f
        for f in findings
    ]
