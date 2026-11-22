"""The VS Code extension that goes with this ger, and installing it into an editor.

A release build (``scripts/build_release.py``) puts the extension package next to this code, so
the extension an editor gets is always the one that matches the ``ger serve`` it will start.
An install from a source checkout has none bundled (:func:`bundled_extension` says so with
``None``) but has the extension's sources beside it (:func:`source_extension`), which
:func:`build_extension` packages the same way a release does.
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

_PACKAGE = Path(gerrit_workflow_tools.__file__).resolve().parent


def bundled_extension() -> Path | None:
    """The extension package this ger was released with, or ``None`` for an install from source."""
    path = _PACKAGE / "vscode_extension" / EXTENSION_FILE
    return path if path.is_file() else None


def source_extension() -> Path | None:
    """The ``vscode/`` sources of the checkout this ger runs from, or ``None`` when it does not run from one."""
    folder = _PACKAGE.parents[1] / "vscode"
    return folder if (folder / "package.json").is_file() else None


def build_extension(sources: Path, out: Path) -> int:
    """Package the extension in *sources* (a ``vscode/`` folder) into *out* with npm; npm's exit code, ``0`` built.

    The package must carry the licence, which the repository keeps once, at its root: it is
    copied in for the build and removed after.
    """
    npm = shutil.which("npm")  # npm.cmd on Windows
    if npm is None:
        raise UsageError(f"building the VS Code extension from {sources} needs npm on PATH (https://nodejs.org)")
    if not (sources / "node_modules").is_dir() and (
        code := run_editor_cli([npm, "ci", "--no-audit", "--no-fund"], sources)
    ):
        return code
    out.parent.mkdir(parents=True, exist_ok=True)
    licence = sources / "LICENSE"
    shutil.copyfile(sources.parent / "LICENSE", licence)
    try:
        return run_editor_cli([npm, "run", "compile"], sources) or run_editor_cli(
            [npm, "run", "package", "--", "--out", str(out)], sources
        )
    finally:
        licence.unlink()


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
