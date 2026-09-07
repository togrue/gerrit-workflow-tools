#!/usr/bin/env python3
"""Regenerate contrib/completion/ger.bash from live argparse definitions."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.bash_completion_generator import render_bash_completion_script

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contrib" / "completion" / "ger.bash"


def main() -> None:
    # newline="\n" keeps LF on Windows (default text mode would write CRLF).
    OUT.write_text(render_bash_completion_script(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
