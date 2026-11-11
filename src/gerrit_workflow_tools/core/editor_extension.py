"""The VS Code extension that ships inside a release of ger, and installing it into an editor.

A release build (``scripts/build_release.py``) puts the extension package next to this code, so
the extension an editor gets is always the one that matches the ``ger serve`` it will start.
An install from a source checkout has none: :func:`bundled_extension` says so with ``None``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import gerrit_workflow_tools
from gerrit_workflow_tools.core.git_editor import run_editor_cli
from gerrit_workflow_tools.core.usage import UsageError


REPOSITORY_URL = "https://github.com/togrue/gerrit-workflow-tools"

EXTENSION_FILE = "ger-vscode.vsix"

# Command-line programs of VS Code and the editors built on it, in the order they are tried.
EDITOR_CLIS: tuple[str, ...] = ("code", "code-insiders", "codium", "cursor")


def release_url(asset: str, version: str | None = None) -> str:
    """Where a release serves *asset*: the latest release, or the one for *version*."""
    where = f"download/v{version}" if version else "latest/download"
    return f"{REPOSITORY_URL}/releases/{where}/{asset}"


def bundled_extension() -> Path | None:
    """The extension package this ger was released with, or ``None`` for an install from source."""
    path = Path(gerrit_workflow_tools.__file__).resolve().parent / "vscode_extension" / EXTENSION_FILE
    return path if path.is_file() else None


def find_editor_cli(name: str | None = None) -> str:
    """The editor's command-line program: *name* (on ``PATH``, or a path), else the first of :data:`EDITOR_CLIS`."""
    for candidate in (name,) if name else EDITOR_CLIS:
        if found := shutil.which(candidate):
            return found
    if name:
        raise UsageError(f"editor command {name!r} not found")
    raise UsageError(
        f"no editor command found on PATH (tried {', '.join(EDITOR_CLIS)}); name yours with --editor, "
        "or in VS Code run 'Shell Command: Install 'code' command in PATH'"
    )


def install_extension(editor_cli: str, extension: Path) -> int:
    """Install (or update to) *extension* in the editor behind *editor_cli*; the editor reports the result itself."""
    return run_editor_cli([editor_cli, "--install-extension", str(extension), "--force"])
