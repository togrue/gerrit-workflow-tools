"""Resolve and run the user's git editor (``GIT_EDITOR`` / ``core.editor`` / …), and an editor's own CLI."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings


def module_editor_command(module: str) -> str:
    """A ``GIT_SEQUENCE_EDITOR`` value that runs Python *module* with this interpreter."""
    return f"{shlex.quote(sys.executable)} -m {module}"


def resolve_git_editor(
    cwd: Path | str | None,
    *,
    settings: Settings | None = None,
    env_overrides: tuple[str, ...] = (),
) -> str:
    """Return the editor command string, following git's lookup order.

    Priority:
    1. Each name in *env_overrides* (first set wins), then ``GIT_EDITOR``
    2. ``core.editor`` git config
    3. ``VISUAL`` env var
    4. ``EDITOR`` env var
    5. ``vi`` fallback

    ``sequence.editor`` is never read: rebase wrappers *are* the sequence editor.
    """
    for env_var in (*env_overrides, "GIT_EDITOR"):
        val = os.environ.get(env_var)
        if val:
            return val

    snap = settings if settings is not None else Settings.from_cwd(cwd)
    editor = snap.get("core.editor")
    if editor:
        return editor

    for env_var in ("VISUAL", "EDITOR"):
        val = os.environ.get(env_var)
        if val:
            return val

    return "vi"


def run_git_editor(
    path: Path,
    cwd: Path | str | None,
    *,
    settings: Settings | None = None,
    env_overrides: tuple[str, ...] = (),
) -> int:
    """Open *path* in the resolved editor and return the editor's exit code."""
    editor = resolve_git_editor(cwd, settings=settings, env_overrides=env_overrides)
    try:
        return subprocess.run([*shlex.split(editor), str(path)], check=False).returncode
    except FileNotFoundError:
        raise FileNotFoundError(editor) from None


def run_editor_cli(argv: list[str]) -> int:
    """Run an editor's command-line program (``code --install-extension …``) on this terminal; return its exit code."""
    return subprocess.run(argv, check=False).returncode
