"""Invoked as GIT_SEQUENCE_EDITOR to patch a single `pick` line for git gedit."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import configure_logging

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("GEDIT_VERBOSE"):
        configure_logging(True)
    args = argv if argv is not None else sys.argv
    if len(args) < 2:
        print("usage: GIT_SEQUENCE_EDITOR for gedit only", file=sys.stderr)
        return 1
    todo = Path(args[1])
    short_sha = os.environ.get("GEDIT_SHORT_SHA", "")
    action = os.environ.get("GEDIT_ACTION", "edit")
    logger.debug(
        "sequence_editor todo=%s action=%s short_sha=%s",
        todo,
        action,
        short_sha,
    )
    if action not in ("edit", "reword", "drop"):
        print(f"invalid GEDIT_ACTION: {action}", file=sys.stderr)
        return 1
    text = todo.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("#") or not line.strip():
            out.append(line)
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2 and short_sha and parts[1].startswith(short_sha):
            if parts[0] == "pick":
                out.append(line.replace("pick", action, 1))
            else:
                out.append(line)
            found = True
        else:
            out.append(line)
    if not found:
        print(
            f"error: could not find commit starting with {short_sha!r} in rebase todo",
            file=sys.stderr,
        )
        return 1
    logger.debug("sequence_editor patched todo for %s -> %s", short_sha, action)
    todo.write_text("".join(out), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
