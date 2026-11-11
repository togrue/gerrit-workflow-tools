"""``ger vscode`` — install the VS Code extension this ger was released with."""

from __future__ import annotations

import argparse

from gerrit_workflow_tools import __version__
from gerrit_workflow_tools.cli_common import init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.editor_extension import (
    EDITOR_CLIS,
    EXTENSION_FILE,
    bundled_extension,
    find_editor_cli,
    install_extension,
    release_url,
)
from gerrit_workflow_tools.core.usage import UsageError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ger vscode",
        description=(
            "Install the VS Code extension that came with this ger into your editor, so the two always "
            "match. Run it again after updating ger to update the extension."
        ),
    )
    parser.add_argument("action", choices=["install"], help="install: install or update the extension in your editor.")
    parser.add_argument(
        "--editor",
        metavar="COMMAND",
        help=(
            "The editor's command-line program, by name or path (for example cursor, or code-insiders). "
            f"Default: the first of {', '.join(EDITOR_CLIS)} on PATH."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger vscode``."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    init_cli_runtime(args, repository=False)
    extension = bundled_extension()
    if extension is None:
        raise UsageError(
            "this ger was installed from source and carries no extension. Install a release of ger "
            f"({release_url('ger.tar.gz')}), or download {release_url(EXTENSION_FILE, __version__)} "
            "and install it with 'Extensions: Install from VSIX…'"
        )
    try:
        editor = find_editor_cli(args.editor)
    except UsageError as e:
        raise UsageError(f"{e}. The extension is at {extension}: 'Extensions: Install from VSIX…' takes it") from None
    return install_extension(editor, extension)
